"""Anthropic Claude provider for Odoo AI.

Extends the upstream ai module's provider abstraction so Claude appears as
a first-class LLM choice. Integration points:

1. Add a `Provider("anthropic", ...)` entry to `ai.utils.llm_providers.PROVIDERS`
   so model selections (`ai.agent.llm_model`, `ai.fields`, etc.) include Claude.

2. Extend `LLMApiService`:
   - `__init__` sets base_url when provider='anthropic'
   - `_get_api_token` returns the key stored at `ai.anthropic_key`
   - `_request_llm_anthropic` implements the Messages API (tools, files,
     structured output via forced tool call, usage stats, prompt caching)
   - `_request_llm` dispatches to the new method

Anthropic API docs:
  https://docs.claude.com/en/api/messages
  https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview
  https://docs.claude.com/en/docs/build-with-claude/prompt-caching
"""

import json
import logging
import os
from typing import Any, Callable

from odoo.exceptions import UserError
from odoo.tools import _

from odoo.addons.ai.utils import llm_providers
from odoo.addons.ai.utils.llm_providers import Provider
from odoo.addons.ai.utils import llm_api_service
from odoo.addons.ai.utils.llm_api_service import LLMApiService
from odoo.addons.ai.utils.ai_logging import api_call_logging

_logger = logging.getLogger(__name__)

ANTHROPIC_PROVIDER_NAME = "anthropic"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_DEFAULT_MAX_TOKENS = 16000
ANTHROPIC_REQUEST_TIMEOUT = 180
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Claude models — current and recent legacy. Values are (alias, display_name).
# Use the unsuffixed alias (Anthropic recommends this — appending date suffixes
# to an alias bypasses the alias mechanism). Admins can add custom model_ids
# in ai.agent form when Anthropic releases new models.
# Verified against shared/models.md (Anthropic SDK skill, 2026-10).
ANTHROPIC_MODELS = [
    ("claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
    ("claude-opus-4-7", "Claude Opus 4.7"),
    ("claude-opus-4-6", "Claude Opus 4.6"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ("claude-opus-4-5", "Claude Opus 4.5 (legacy)"),
    ("claude-sonnet-4-5", "Claude Sonnet 4.5 (legacy)"),
]

# Models that still accept sampling parameters. Every newer model rejects
# `temperature` with a 400 and takes the response style as `effort` instead.
SAMPLING_MODELS = {
    "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5",
    "claude-opus-4-5", "claude-sonnet-4-5",
}
# Models that reject a forced `tool_choice` (`any` / `tool`) with a 400.
NO_FORCED_TOOL_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
# Models that take the server-side refusal fallback in its "default" form.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

# Odoo's response style (sent as a temperature, see TEMPERATURE_MAP) mapped
# to Claude effort levels.
RESPONSE_STYLE_TO_EFFORT = {
    "analytical": "high",
    "balanced": "medium",
    "creative": "low",
}


# ---------------------------------------------------------------------------
# 1. Register Anthropic in PROVIDERS (module-level list, mutated in place)
# ---------------------------------------------------------------------------
def _register_provider():
    # Idempotency — don't duplicate if the module is reloaded.
    if any(p.name == ANTHROPIC_PROVIDER_NAME for p in llm_providers.PROVIDERS):
        return
    # Anthropic doesn't expose a native embedding endpoint — leave the
    # embedding_model empty. `get_embedding_model_selection` consumers that
    # filter falsy values will ignore it; anything that tries to dispatch
    # embeddings to Anthropic will still fall through to the existing
    # provider chain.
    values = {
        "name": ANTHROPIC_PROVIDER_NAME,
        "display_name": "Anthropic",
        "embedding_model": "",
        "embedding_config": {},
        "llms": ANTHROPIC_MODELS,
        "deprecated_models": [],
        # Only read for deprecated models (none here), but required by the
        # NamedTuple since 19.0 deprecated the first generation of models.
        "response_style_to_llm_model_and_reasoning": {
            "analytical": ("claude-opus-5-5", "high"),
            "balanced": ("claude-sonnet-5-5", "medium"),
            "creative": ("claude-sonnet-5-5", "low"),
        },
    }
    # Odoo keeps adding fields to Provider within 19.0: fill any unknown
    # one with an empty value instead of failing at import time.
    llm_providers.PROVIDERS.append(Provider(**{
        field: values.get(field, {}) for field in Provider._fields
    }))


_register_provider()


# ---------------------------------------------------------------------------
# 2. LLMApiService extensions
# ---------------------------------------------------------------------------
_orig_init = LLMApiService.__init__
_orig_get_api_token = LLMApiService._get_api_token
_orig_request_llm = LLMApiService._request_llm
_orig_build_tool_call_response = LLMApiService._build_tool_call_response
_orig_get_embedding = LLMApiService.get_embedding


def _patched_init(self, env, provider: str = "openai") -> None:
    if provider == ANTHROPIC_PROVIDER_NAME:
        self.provider = provider
        self.base_url = ANTHROPIC_BASE_URL
        self.env = env
        return
    _orig_init(self, env, provider)


def _patched_get_api_token(self):
    if self.provider == ANTHROPIC_PROVIDER_NAME:
        key = (
            self.env["ir.config_parameter"].sudo().get_param("ai.anthropic_key")
            or os.getenv("ODOO_AI_ANTHROPIC_TOKEN")
        )
        if not key:
            raise UserError(_("No API key set for provider 'anthropic'"))
        return key
    return _orig_get_api_token(self)


def _anthropic_headers(self) -> dict:
    return {
        "x-api-key": self._get_api_token(),
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }


def _build_user_content_block(prompts, files):
    """Build the Anthropic `content` array for a user message.

    Text blocks for each prompt; images as base64 data blocks; PDFs as
    `document` blocks; plain-text files inlined as additional text.
    Empty/None prompts are skipped — Anthropic rejects the request with
    'text content blocks must be non-empty' if any block is empty."""
    content = [{"type": "text", "text": p} for p in (prompts or []) if p and p.strip()]
    for file in files or []:
        mimetype = file.get("mimetype", "")
        value = file.get("value", "")
        if mimetype == "text/plain":
            content.append({"type": "text", "text": value})
        elif mimetype == "application/pdf":
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": value,
                },
            })
        elif mimetype.startswith("image/"):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mimetype,
                    "data": value,
                },
            })
    return content


def _normalize_input_entry(entry):
    """Normalise one history-input entry to Anthropic Messages API shape.

    Accepts either Anthropic-native shape ({role, content: [blocks]}) or
    OpenAI/Odoo chat shape ({role, content: "string"}). Returns the
    Anthropic version, or None if the entry is malformed/unsupported."""
    if not isinstance(entry, dict):
        return None
    role = entry.get("role")
    content = entry.get("content")
    # Anthropic-shape: content is already a list of typed blocks. Keep as-is.
    if isinstance(content, list):
        if role in ("user", "assistant"):
            return entry
        return None
    # OpenAI-shape: wrap string content in a single text block.
    # Skip empty strings — Anthropic rejects empty content blocks.
    if isinstance(content, str) and role in ("user", "assistant"):
        if not content.strip():
            return None
        return {"role": role, "content": [{"type": "text", "text": content}]}
    # Anything else (Gemini parts, function_call openai blocks, etc.) we
    # don't try to translate — caller will likely have configured the
    # wrong provider for this conversation.
    _logger.debug("Anthropic adapter: dropping unrecognised input entry: %r", entry)
    return None


def _build_tool_schemas(tools):
    """Translate the ai module's tool format to Anthropic's.

    Incoming shape (same as openai/gemini builders):
        {tool_name: (description, _, _, parameter_schema)}

    Anthropic shape:
        [{"name": ..., "description": ..., "input_schema": {...}}]"""
    return [
        {
            "name": name,
            "description": description,
            "input_schema": parameter_schema,
        }
        for name, (description, _unused1, _unused2, parameter_schema) in tools.items()
    ]


STRUCTURED_OUTPUT_TOOL_NAME = "respond_with_structured_output"


def _schema_as_forced_tool(schema):
    """Claude has no native JSON-schema response mode, so we expose the
    schema as a single tool and force Claude to call it. The tool arguments
    become the structured output."""
    return {
        "name": STRUCTURED_OUTPUT_TOOL_NAME,
        "description": "Return the answer as JSON matching the schema exactly.",
        "input_schema": schema,
    }


def _request_llm_anthropic(
    self, llm_model, system_prompts, user_prompts, tools=None,
    files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False,
):
    """Call Anthropic's Messages API.

    Contract matches `_request_llm_openai` / `_request_llm_google`:
      returns (response_lines, to_call, next_inputs)
    where to_call is [(tool_name, call_id, arguments)] and next_inputs is
    the assistant turns to append to the next call (for tool-use loops)."""
    if web_grounding:
        # Anthropic's web_search is native but gated; leave unsupported
        # until the account-level feature is widely available. Callers can
        # inspect the provider and fall back to openai/google for grounding.
        raise NotImplementedError(
            "Web grounding is not yet supported on the Anthropic provider."
        )

    # System prompts — Anthropic supports an array of blocks, optionally
    # marked with cache_control for prompt caching. We cache only if more
    # than one non-empty system prompt is present (heuristic: agents combine
    # several stable chunks — persona + org rules — that benefit from caching).
    # Filter out empty strings / None: Anthropic rejects the request with
    # "text content blocks must be non-empty" if any block is empty, and
    # Odoo occasionally passes an empty topic preamble or empty RAG context.
    non_empty_system = [p for p in (system_prompts or []) if p and p.strip()]
    system_blocks = []
    for idx, prompt in enumerate(non_empty_system):
        block = {"type": "text", "text": prompt}
        # Cache all but the last system block if there are ≥2 — stable
        # prefix gets a 90% discount on repeat calls within ~5 min.
        if len(non_empty_system) >= 2 and idx < len(non_empty_system) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        system_blocks.append(block)

    # Messages: user turn (with files) + any prior conversation/tool-use inputs.
    messages = []
    user_content = _build_user_content_block(user_prompts, files)
    if user_content:
        messages.append({"role": "user", "content": user_content})
    # `inputs` may arrive in two shapes:
    #   - Anthropic shape (from this method's own next_inputs return value
    #     during a tool-use loop) — already correct.
    #   - OpenAI shape (from chat history persisted by Discuss / livechat
    #     when the conversation switched to a Claude agent mid-thread):
    #     `{"role": "user"|"assistant", "content": "<string>"}` — needs
    #     wrapping into Anthropic's content-block array.
    # Detection: string content is OpenAI-shape; list content is Anthropic-shape.
    for entry in (inputs or ()):
        normalized = _normalize_input_entry(entry)
        if normalized is not None:
            messages.append(normalized)

    body = {
        "model": llm_model,
        "max_tokens": ANTHROPIC_DEFAULT_MAX_TOKENS,
        "messages": messages,
    }
    if llm_model in SAMPLING_MODELS:
        body["temperature"] = temperature
    else:
        response_style = next(
            (style for style, temp in llm_providers.TEMPERATURE_MAP.items() if temp == temperature),
            "balanced",
        )
        body["output_config"] = {"effort": RESPONSE_STYLE_TO_EFFORT[response_style]}
    # The fallback beta can be switched off (system parameter set to '0')
    # if the Anthropic account rejects it, without a deploy.
    use_fallback = self.env["ir.config_parameter"].sudo().get_param(
        "ai.anthropic_server_fallback", "1") == "1"
    if llm_model in FALLBACK_MODELS and use_fallback:
        body["fallbacks"] = "default"
    if system_blocks:
        body["system"] = system_blocks

    # Combine real tools and (optionally) a schema-forcing tool.
    # IMPORTANT: only force tool_choice on the schema tool when there are NO
    # real tools to call. Forcing tool_choice with real tools present blocks
    # the agent from ever using them (Claude is stuck calling the schema
    # tool over and over). When schema + real tools coexist, we expose the
    # schema tool with `tool_choice=any` so Claude can either call a real
    # tool OR finalise via the schema tool — at the cost of less strict
    # structured-output enforcement.
    anthropic_tools = []
    if tools:
        anthropic_tools.extend(_build_tool_schemas(tools))
    if schema:
        anthropic_tools.append(_schema_as_forced_tool(schema))
        if not tools and llm_model in NO_FORCED_TOOL_MODELS:
            # Forced tool_choice is a 400 on these models: steer from the
            # system prompt and leave tool_choice on 'auto'.
            body.setdefault("system", []).append({
                "type": "text",
                "text": f"Always answer by calling the {STRUCTURED_OUTPUT_TOOL_NAME} tool.",
            })
        elif not tools:
            body["tool_choice"] = {"type": "tool", "name": STRUCTURED_OUTPUT_TOOL_NAME}
        # else: leave tool_choice unset (default 'auto') so real tools remain reachable
    if anthropic_tools:
        body["tools"] = anthropic_tools

    with api_call_logging(messages, tools) as record_response:
        response, to_call, next_inputs, request_token_usage = \
            _request_llm_anthropic_helper(self, body, tools, inputs, schema)
        if record_response:
            record_response(to_call, response, request_token_usage)
        return response, to_call, next_inputs


def _request_llm_anthropic_helper(self, body, tools, inputs, schema):
    """POST to /messages and convert Anthropic response into the
    (response, to_call, next_inputs, usage) tuple the ai module expects."""
    headers = _anthropic_headers(self)
    if "fallbacks" in body:
        headers["anthropic-beta"] = ANTHROPIC_FALLBACK_BETA
    llm_response = self._request(
        method="post",
        endpoint="/messages",
        headers=headers,
        body=body,
        timeout=ANTHROPIC_REQUEST_TIMEOUT,
    )

    to_call = []
    response_texts = []
    next_inputs = list(inputs or ())

    content_blocks = _drop_declined_partial(llm_response.get("content") or [])
    assistant_content = []  # for adding to next_inputs on tool use
    has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks)

    for block in content_blocks:
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name", "")
            call_id = block.get("id", "")
            arguments = block.get("input") or {}
            if schema and name == STRUCTURED_OUTPUT_TOOL_NAME:
                # Structured output: don't treat as tool call for caller's
                # loop — serialise the arguments as a JSON text response so
                # downstream code that expects string output gets valid JSON.
                response_texts.append(json.dumps(arguments))
            else:
                to_call.append((name, call_id, arguments))
                assistant_content.append(block)
        elif btype == "text" and not has_tool_use:
            if text := block.get("text"):
                response_texts.append(text)
        elif btype == "text" and has_tool_use:
            # Claude sometimes emits a text block alongside tool_use with
            # reasoning/commentary — preserve it in the loop context.
            assistant_content.append(block)
        elif btype in ("thinking", "redacted_thinking") and has_tool_use:
            # The assistant turn waiting on its tool_result must go back with
            # its thinking blocks unchanged and in place.
            assistant_content.append(block)

    if has_tool_use and assistant_content:
        next_inputs.append({"role": "assistant", "content": assistant_content})

    if not response_texts and not to_call and llm_response.get("stop_reason") == "refusal":
        response_texts.append(self.env._("The AI model declined to answer this request."))

    request_token_usage = {}
    if usage := llm_response.get("usage"):
        # Claude returns input_tokens, output_tokens, plus cache_read /
        # cache_creation breakouts. Map to the ai module's schema.
        request_token_usage["input_tokens"] = usage.get("input_tokens", 0)
        request_token_usage["cached_tokens"] = usage.get("cache_read_input_tokens", 0)
        request_token_usage["output_tokens"] = usage.get("output_tokens", 0)

    return response_texts, to_call, next_inputs, request_token_usage


def _drop_declined_partial(content_blocks):
    """After a server-side fallback, the blocks the declining model produced
    before the last `fallback` marker must not be echoed back (thinking and
    tool_use). Text survives; the marker itself is only an audit trail."""
    boundary = max(
        (idx for idx, block in enumerate(content_blocks) if block.get("type") == "fallback"),
        default=None,
    )
    if boundary is None:
        return content_blocks
    kept = [
        block for block in content_blocks[:boundary]
        if block.get("type") not in ("thinking", "redacted_thinking", "tool_use")
    ]
    return kept + content_blocks[boundary + 1:]


def _patched_request_llm(self, *args, **kwargs):
    if self.provider == ANTHROPIC_PROVIDER_NAME:
        from odoo.addons.ai.utils.llm_providers import check_model_depreciation
        model = kwargs.get("llm_model") or args[0]
        check_model_depreciation(self.env, model)
        return _request_llm_anthropic(self, *args, **kwargs)
    return _orig_request_llm(self, *args, **kwargs)


def _patched_build_tool_call_response(self, tool_call_id, return_value):
    """Anthropic tool_result message format. The agentic loop appends this
    return value back into messages on the next turn — without the patch
    upstream raises NotImplementedError for any provider that isn't
    openai/google, breaking every Claude agent on the second turn."""
    if self.provider == ANTHROPIC_PROVIDER_NAME:
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": str(return_value),
            }],
        }
    return _orig_build_tool_call_response(self, tool_call_id, return_value)


def _patched_get_embedding(self, *args, **kwargs):
    """Anthropic has no native embedding endpoint. When a Claude agent has
    sources / RAG enabled, the upstream code calls get_embedding on the
    Claude provider — which would 404 against Anthropic's API.

    Fallback: delegate to the OpenAI service if a key is configured.
    Embedding cost is small relative to LLM inference, and the resulting
    vectors are model-portable for retrieval purposes."""
    if self.provider != ANTHROPIC_PROVIDER_NAME:
        return _orig_get_embedding(self, *args, **kwargs)
    try:
        openai_service = LLMApiService(self.env, provider="openai")
        # Force the openai default model regardless of what the caller passed
        # (an empty string from our Provider entry would otherwise leak through).
        kwargs["model"] = "text-embedding-3-small"
        return _orig_get_embedding(openai_service, *args, **kwargs)
    except UserError as e:
        raise UserError(_(
            "Claude has no native embedding API. To use sources / RAG with a "
            "Claude agent, configure an OpenAI API key in Settings → AI "
            "(used for embeddings only). Original error: %s",
        ) % e)


# Attach to the class.
LLMApiService.__init__ = _patched_init
LLMApiService._get_api_token = _patched_get_api_token
LLMApiService._request_llm = _patched_request_llm
LLMApiService._build_tool_call_response = _patched_build_tool_call_response
LLMApiService.get_embedding = _patched_get_embedding
LLMApiService._request_llm_anthropic = _request_llm_anthropic
