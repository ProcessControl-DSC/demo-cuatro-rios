from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai.utils.llm_providers import PROVIDERS, get_provider
from odoo.addons.ai.utils.llm_api_service import LLMApiService


@tagged('post_install', '-at_install')
class TestAnthropicProvider(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env['ir.config_parameter'].sudo()

    # ------------------------------------------------------------------
    # Provider registration
    # ------------------------------------------------------------------
    def test_anthropic_in_providers(self):
        """Claude provider must be registered in PROVIDERS after module load."""
        self.assertTrue(
            any(p.name == 'anthropic' for p in PROVIDERS),
            "Anthropic not registered in PROVIDERS",
        )

    def test_claude_models_resolvable(self):
        """Every Claude model id must resolve back to 'anthropic' provider."""
        anthropic = next(p for p in PROVIDERS if p.name == 'anthropic')
        for model_id, _label in anthropic.llms:
            self.assertEqual(get_provider(self.env, model_id), 'anthropic',
                             f"{model_id} does not map to anthropic provider")

    # ------------------------------------------------------------------
    # Config & auth
    # ------------------------------------------------------------------
    def test_missing_key_raises(self):
        """Request without configured key + no env var must raise UserError."""
        self.icp.set_param('ai.anthropic_key', '')
        service = LLMApiService(self.env, provider='anthropic')
        with patch.dict('os.environ', {}, clear=False):
            import os
            os.environ.pop('ODOO_AI_ANTHROPIC_TOKEN', None)
            with self.assertRaises(UserError):
                service._get_api_token()

    def test_config_key_returned(self):
        self.icp.set_param('ai.anthropic_key', 'sk-ant-test-fixture')
        service = LLMApiService(self.env, provider='anthropic')
        self.assertEqual(service._get_api_token(), 'sk-ant-test-fixture')

    def test_base_url_set(self):
        service = LLMApiService(self.env, provider='anthropic')
        self.assertEqual(service.base_url, 'https://api.anthropic.com/v1')

    def test_other_providers_unaffected(self):
        """Patching must not break openai/google dispatch."""
        openai_service = LLMApiService(self.env, provider='openai')
        self.assertEqual(openai_service.base_url, 'https://api.openai.com/v1')
        google_service = LLMApiService(self.env, provider='google')
        self.assertIn('generativelanguage.googleapis.com', google_service.base_url)

    # ------------------------------------------------------------------
    # Request dispatch with mocked HTTP
    # ------------------------------------------------------------------
    def _mock_anthropic_response(self, *, text=None, tool_use=None, usage=None):
        content = []
        if text is not None:
            content.append({"type": "text", "text": text})
        if tool_use is not None:
            content.append({
                "type": "tool_use",
                "id": "tool_1",
                "name": tool_use["name"],
                "input": tool_use["input"],
            })
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": content,
            "usage": usage or {"input_tokens": 10, "output_tokens": 5},
        }

    def test_dispatch_returns_text(self):
        self.icp.set_param('ai.anthropic_key', 'sk-ant-test')
        service = LLMApiService(self.env, provider='anthropic')
        with patch.object(LLMApiService, '_request',
                          return_value=self._mock_anthropic_response(text="Zdravo!")):
            response, to_call, next_inputs = service._request_llm(
                'claude-sonnet-4-6',
                ['You are helpful.'],
                ['Pozdravi me'],
            )
        self.assertEqual(response, ["Zdravo!"])
        self.assertEqual(to_call, [])
        self.assertEqual(next_inputs, [])

    def test_tool_calling_returns_to_call(self):
        self.icp.set_param('ai.anthropic_key', 'sk-ant-test')
        service = LLMApiService(self.env, provider='anthropic')
        tools = {
            'get_weather': (
                'Get weather for a city',
                True,
                lambda args: {'temp': 20},
                {'type': 'object', 'properties': {'city': {'type': 'string'}}},
            ),
        }
        with patch.object(LLMApiService, '_request',
                          return_value=self._mock_anthropic_response(
                              tool_use={'name': 'get_weather', 'input': {'city': 'Sarajevo'}})):
            response, to_call, next_inputs = service._request_llm(
                'claude-sonnet-4-6',
                ['System'],
                ['What is the weather in Sarajevo?'],
                tools=tools,
            )
        self.assertEqual(len(to_call), 1)
        name, call_id, arguments = to_call[0]
        self.assertEqual(name, 'get_weather')
        self.assertEqual(arguments, {'city': 'Sarajevo'})
        # next_inputs should carry the assistant's tool_use for the next call
        self.assertEqual(len(next_inputs), 1)
        self.assertEqual(next_inputs[0]['role'], 'assistant')

    def test_structured_output_serialises_to_json(self):
        """Schema-forced tool call must be returned as JSON text, not as a
        tool-call the caller needs to dispatch."""
        self.icp.set_param('ai.anthropic_key', 'sk-ant-test')
        service = LLMApiService(self.env, provider='anthropic')
        schema = {
            'type': 'object',
            'properties': {'answer': {'type': 'integer'}},
            'required': ['answer'],
        }
        from odoo.addons.viavista_ai_claude.utils.llm_anthropic import (
            STRUCTURED_OUTPUT_TOOL_NAME,
        )
        with patch.object(LLMApiService, '_request',
                          return_value=self._mock_anthropic_response(
                              tool_use={'name': STRUCTURED_OUTPUT_TOOL_NAME,
                                        'input': {'answer': 42}})):
            response, to_call, _next_inputs = service._request_llm(
                'claude-sonnet-4-6',
                ['System'],
                ['Give me 42 as JSON'],
                schema=schema,
            )
        self.assertEqual(to_call, [])
        self.assertEqual(len(response), 1)
        import json
        self.assertEqual(json.loads(response[0]), {'answer': 42})

    def test_usage_tracking_maps_cache_tokens(self):
        self.icp.set_param('ai.anthropic_key', 'sk-ant-test')
        service = LLMApiService(self.env, provider='anthropic')
        captured = {}

        def fake_request(*args, **kwargs):
            return self._mock_anthropic_response(
                text="ok",
                usage={
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 70,
                    "cache_creation_input_tokens": 0,
                },
            )

        with patch.object(LLMApiService, '_request', side_effect=fake_request):
            # Call the lower-level helper directly to capture usage.
            from odoo.addons.viavista_ai_claude.utils.llm_anthropic import (
                _request_llm_anthropic_helper,
            )
            response, to_call, next_inputs, usage = _request_llm_anthropic_helper(
                service,
                body={'model': 'claude-sonnet-4-6', 'max_tokens': 4096, 'messages': []},
                tools=None,
                inputs=(),
                schema=None,
            )
        self.assertEqual(usage['input_tokens'], 100)
        self.assertEqual(usage['cached_tokens'], 70)
        self.assertEqual(usage['output_tokens'], 50)

    # ------------------------------------------------------------------
    # Current Odoo 19 Provider contract and Claude 5.x request rules
    # ------------------------------------------------------------------
    def _capture_body(self, model, response, **kwargs):
        self.icp.set_param('ai.anthropic_key', 'sk-ant-test')
        service = LLMApiService(self.env, provider='anthropic')
        captured = {}

        def fake_request(_self, *args, **kw):
            captured.update(kw)
            return response

        with patch.object(LLMApiService, '_request', autospec=True, side_effect=fake_request):
            result = service._request_llm(model, ['System'], ['Hello'], **kwargs)
        return captured, result

    def test_provider_fills_every_upstream_field(self):
        """The Provider NamedTuple gained fields during 19.0; every field
        must be filled, including the response style replacement map."""
        anthropic = next(p for p in PROVIDERS if p.name == 'anthropic')
        self.assertEqual(len(anthropic), len(type(anthropic)._fields))
        mapping = anthropic.response_style_to_llm_model_and_reasoning
        for style in ('analytical', 'balanced', 'creative'):
            model_id, _effort = mapping[style]
            self.assertEqual(get_provider(self.env, model_id), 'anthropic')

    def test_claude_5_models_registered(self):
        anthropic = next(p for p in PROVIDERS if p.name == 'anthropic')
        model_ids = [m[0] for m in anthropic.llms]
        for model_id in ('claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5'):
            self.assertIn(model_id, model_ids)

    def test_claude_5_sends_effort_not_temperature(self):
        """Claude 5.x rejects sampling parameters with a 400: the response
        style must travel as effort."""
        captured, _result = self._capture_body(
            'claude-opus-5-5', self._mock_anthropic_response(text='ok'), temperature=0.2)
        body = captured['body']
        self.assertNotIn('temperature', body)
        self.assertEqual(body['output_config']['effort'], 'high')

    def test_legacy_model_keeps_temperature(self):
        captured, _result = self._capture_body(
            'claude-haiku-4-5', self._mock_anthropic_response(text='ok'), temperature=0.5)
        body = captured['body']
        self.assertEqual(body['temperature'], 0.5)
        self.assertNotIn('output_config', body)

    def test_claude_5_structured_output_without_forced_tool(self):
        """Forced tool_choice returns a 400 on Claude 5.5 models."""
        from odoo.addons.viavista_ai_claude.utils.llm_anthropic import (
            STRUCTURED_OUTPUT_TOOL_NAME,
        )
        schema = {'type': 'object', 'properties': {'answer': {'type': 'integer'}}}
        captured, (response, to_call, _next) = self._capture_body(
            'claude-sonnet-5-5',
            self._mock_anthropic_response(
                tool_use={'name': STRUCTURED_OUTPUT_TOOL_NAME, 'input': {'answer': 42}}),
            schema=schema,
        )
        self.assertNotIn('tool_choice', captured['body'])
        self.assertEqual(to_call, [])
        import json
        self.assertEqual(json.loads(response[0]), {'answer': 42})

    def test_thinking_blocks_echoed_in_tool_loop(self):
        """With thinking always on, the assistant turn that carries the
        tool_use must go back with its thinking block unchanged."""
        llm_response = {
            'content': [
                {'type': 'thinking', 'thinking': '', 'signature': 'sig-1'},
                {'type': 'tool_use', 'id': 'tool_1', 'name': 'get_weather',
                 'input': {'city': 'Telde'}},
            ],
            'stop_reason': 'tool_use',
            'usage': {'input_tokens': 10, 'output_tokens': 5},
        }
        tools = {'get_weather': ('Weather', True, lambda a: {}, {'type': 'object'})}
        _captured, (_response, to_call, next_inputs) = self._capture_body(
            'claude-opus-5-5', llm_response, tools=tools)
        self.assertEqual(len(to_call), 1)
        self.assertEqual(
            [b['type'] for b in next_inputs[0]['content']], ['thinking', 'tool_use'])
        self.assertEqual(next_inputs[0]['content'][0]['signature'], 'sig-1')

    def test_claude_5_request_uses_long_timeout(self):
        captured, _result = self._capture_body(
            'claude-opus-5-5', self._mock_anthropic_response(text='ok'))
        self.assertGreaterEqual(captured['timeout'], 120)
        self.assertGreaterEqual(captured['body']['max_tokens'], 16000)

    def test_server_fallback_can_be_disabled(self):
        captured, _result = self._capture_body(
            'claude-opus-5-5', self._mock_anthropic_response(text='ok'))
        self.assertEqual(captured['body']['fallbacks'], 'default')
        self.assertIn('anthropic-beta', captured['headers'])
        self.icp.set_param('ai.anthropic_server_fallback', '0')
        captured, _result = self._capture_body(
            'claude-opus-5-5', self._mock_anthropic_response(text='ok'))
        self.assertNotIn('fallbacks', captured['body'])
        self.assertNotIn('anthropic-beta', captured['headers'])

    def test_refusal_returns_message(self):
        llm_response = {
            'content': [],
            'stop_reason': 'refusal',
            'usage': {'input_tokens': 10, 'output_tokens': 0},
        }
        _captured, (response, to_call, _next) = self._capture_body('claude-opus-5-5', llm_response)
        self.assertEqual(to_call, [])
        self.assertEqual(len(response), 1)

    def test_settings_field_roundtrip(self):
        """res.config.settings form should read/write anthropic_key via config parameter."""
        settings = self.env['res.config.settings'].create({
            'anthropic_key': 'sk-ant-from-form',
        })
        settings.execute()
        self.assertEqual(
            self.icp.get_param('ai.anthropic_key'),
            'sk-ant-from-form',
        )
