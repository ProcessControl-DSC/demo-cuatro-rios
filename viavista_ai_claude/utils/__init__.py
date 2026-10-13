"""Monkey-patches applied at module import time.

The upstream `ai` module hardcodes OpenAI + Google in two places:

  * `odoo.addons.ai.utils.llm_providers.PROVIDERS`
  * `odoo.addons.ai.utils.llm_api_service.LLMApiService` (__init__,
    _get_api_token, _request_llm)

We extend both in-place so Anthropic Claude is indistinguishable from
any other provider to the rest of the ai_* module family. Runs once at
module load — Odoo module registry only imports us on server start."""

from . import llm_anthropic
