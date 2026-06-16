import importlib
import sys
import unittest
from unittest.mock import patch


class FakeResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {}
        self.response_metadata = {}


class FakeClient:
    def __init__(
        self,
        model_name: str,
        *,
        error: Exception | None = None,
        content: str = "ok",
        bound_kwargs: dict | None = None,
        calls: list | None = None,
    ):
        self.model_name = model_name
        self._error = error
        self._content = content
        self._bound_kwargs = bound_kwargs or {}
        self.calls = calls if calls is not None else []

    async def ainvoke(self, _args, config=None, **kwargs):
        self.calls.append({
            "config": config,
            "bound_kwargs": self._bound_kwargs,
            "kwargs": kwargs,
        })
        if self._error is not None:
            raise self._error
        return FakeResponse(self._content)

    def with_structured_output(self, *_args, **_kwargs):
        return self

    def bind(self, **kwargs):
        return FakeClient(
            self.model_name,
            error=self._error,
            content=self._content,
            bound_kwargs={**self._bound_kwargs, **kwargs},
            calls=self.calls,
        )


class FakeClientWithoutStructuredOutput(FakeClient):
    def with_structured_output(self, *_args, **_kwargs):
        raise AssertionError("with_structured_output should not be called")


class StructuredResult:
    def __init__(self, value: str):
        self.value = value

    @classmethod
    def model_json_schema(cls):
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    @classmethod
    def model_validate(cls, value):
        return cls(value=value["value"])


class LLMRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.modules.pop("core.utils.llm", None)
        self.llm_module = importlib.import_module("core.utils.llm")

    async def test_economic_mode_uses_default_models_for_text_and_vision(self):
        default_llm = FakeClient("default-llm", content="default-text")
        default_vlm = FakeClient("default-vlm", content="default-vision")
        premium_llm = FakeClient("premium-llm", content="premium-text")

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module, "default_vlm", default_vlm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="ECONOMIC"):
            text = await self.llm_module.llm_invoke(self.llm_module.ModelRoute.PREMIUM, ["text"])
            vision = await self.llm_module.vlm_invoke(self.llm_module.ModelRoute.PREMIUM, ["image"])

        self.assertEqual(text, "default-text")
        self.assertEqual(vision, "default-vision")

    async def test_premium_mode_uses_premium_model_for_premium_text_route(self):
        default_llm = FakeClient("default-llm", content="default-text")
        premium_llm = FakeClient("premium-llm", content="premium-text")

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=True), \
             patch.object(self.llm_module.settings, "has_premium_llm_config", return_value=True):
            text = await self.llm_module.llm_invoke(self.llm_module.ModelRoute.PREMIUM, ["text"])
            default_route_text = await self.llm_module.llm_invoke(self.llm_module.ModelRoute.DEFAULT, ["text"])

        self.assertEqual(text, "premium-text")
        self.assertEqual(default_route_text, "default-text")

    async def test_premium_text_failure_falls_back_to_default_llm(self):
        default_llm = FakeClient("default-llm", content="default-text")
        premium_llm = FakeClient("premium-llm", error=RuntimeError("timeout"))

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module, "MAX_INVOKE_ATTEMPTS", 1), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=True), \
             patch.object(self.llm_module.settings, "has_premium_llm_config", return_value=True), \
             patch.object(self.llm_module.logger, "warning") as warning_mock:
            text = await self.llm_module.llm_invoke(self.llm_module.ModelRoute.PREMIUM, ["text"])

        self.assertEqual(text, "default-text")
        self.assertTrue(any("Fallback to default-llm" in str(call) for call in warning_mock.call_args_list))

    async def test_premium_vision_failure_falls_back_to_default_vlm(self):
        default_vlm = FakeClient("default-vlm", content="default-vision")
        premium_llm = FakeClient("premium-llm", error=RuntimeError("auth failed"))

        with patch.object(self.llm_module, "default_vlm", default_vlm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module, "MAX_INVOKE_ATTEMPTS", 1), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=True), \
             patch.object(self.llm_module.settings, "has_premium_llm_config", return_value=True), \
             patch.object(self.llm_module.logger, "warning") as warning_mock:
            vision = await self.llm_module.vlm_invoke(self.llm_module.ModelRoute.PREMIUM, ["image"])

        self.assertEqual(vision, "default-vision")
        self.assertTrue(any("Fallback to default-vlm" in str(call) for call in warning_mock.call_args_list))

    async def test_premium_vision_route_uses_premium_model_without_default_vlm(self):
        default_vlm = FakeClient("default-vlm", error=RuntimeError("should not be called"))
        premium_llm = FakeClient("premium-llm", content="premium-vision")

        with patch.object(self.llm_module, "default_vlm", default_vlm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=True), \
             patch.object(self.llm_module.settings, "has_premium_llm_config", return_value=True):
            vision = await self.llm_module.vlm_invoke(self.llm_module.ModelRoute.PREMIUM, ["image"])

        self.assertEqual(vision, "premium-vision")

    async def test_premium_mode_without_api_key_falls_back_to_economic_mode(self):
        default_llm = FakeClient("default-llm", content="default-text")
        default_vlm = FakeClient("default-vlm", content="default-vision")
        premium_llm = FakeClient("premium-llm", content="premium-text")

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module, "default_vlm", default_vlm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=False), \
             patch.object(self.llm_module.logger, "warning") as warning_mock:
            text = await self.llm_module.llm_invoke(self.llm_module.ModelRoute.PREMIUM, ["text"])
            vision = await self.llm_module.vlm_invoke(self.llm_module.ModelRoute.PREMIUM, ["image"])

        self.assertEqual(text, "default-text")
        self.assertEqual(vision, "default-vision")
        self.assertTrue(any("Falling back to ECONOMIC mode" in str(call) for call in warning_mock.call_args_list))

    async def test_premium_mode_with_incomplete_premium_config_falls_back_to_economic_mode(self):
        default_llm = FakeClient("default-llm", content="default-text")
        default_vlm = FakeClient("default-vlm", content="default-vision")
        premium_llm = FakeClient("premium-llm", content="premium-text")

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module, "default_vlm", default_vlm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=True), \
             patch.object(self.llm_module.settings, "has_premium_llm_config", return_value=False), \
             patch.object(self.llm_module.logger, "warning") as warning_mock:
            text = await self.llm_module.llm_invoke(self.llm_module.ModelRoute.PREMIUM, ["text"])
            vision = await self.llm_module.vlm_invoke(self.llm_module.ModelRoute.PREMIUM, ["image"])

        self.assertEqual(text, "default-text")
        self.assertEqual(vision, "default-vision")
        self.assertTrue(any("PREMIUM_LLM settings are incomplete" in str(call) for call in warning_mock.call_args_list))
        self.assertTrue(any("Falling back to ECONOMIC mode" in str(call) for call in warning_mock.call_args_list))

    async def test_premium_fallback_failure_raises(self):
        default_llm = FakeClient("default-llm", error=RuntimeError("default failed"))
        premium_llm = FakeClient("premium-llm", error=RuntimeError("premium failed"))

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module, "MAX_INVOKE_ATTEMPTS", 1), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=True), \
             patch.object(self.llm_module.settings, "has_premium_llm_config", return_value=True):
            with self.assertRaises(self.llm_module.LLMInvokeError):
                await self.llm_module.llm_invoke(self.llm_module.ModelRoute.PREMIUM, ["text"])

    async def test_pydantic_schema_uses_plain_ainvoke_and_post_parse(self):
        default_llm = FakeClientWithoutStructuredOutput(
            "default-llm",
            content='{"value": "parsed"}',
        )

        result = await self.llm_module.llm_invoke(
            default_llm,
            ["text"],
            self.llm_module.InvokeOptions(pydantic_schema=StructuredResult),
        )

        self.assertIsInstance(result, StructuredResult)
        self.assertEqual(result.value, "parsed")
        self.assertEqual(len(default_llm.calls), 1)

    async def test_work_node_does_not_add_headers_without_handover(self):
        default_llm = FakeClient("default-llm", content="default-text")

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="ECONOMIC"):
            text = await self.llm_module.llm_invoke(
                self.llm_module.ModelRoute.DEFAULT,
                ["text"],
                self.llm_module.InvokeOptions(work_node="parse_query"),
            )

        self.assertEqual(text, "default-text")
        self.assertEqual(default_llm.calls[0]["bound_kwargs"], {})
        self.assertNotIn("extra_headers", default_llm.calls[0]["kwargs"])

    async def test_handover_work_node_adds_agent_profile_headers(self):
        default_llm = FakeClient("default-llm", content="default-text")

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module.settings, "MODEL_INVOKE_HANDOVER", True):
            text = await self.llm_module.llm_invoke(
                self.llm_module.ModelRoute.DEFAULT,
                ["text"],
                self.llm_module.InvokeOptions(work_node="parse_query"),
            )

        self.assertEqual(text, "default-text")
        self.assertEqual(
            default_llm.calls[0]["bound_kwargs"]["extra_headers"],
            {
                "X-Agent-Id": "slidea",
                "X-Work-Node": "parse_query",
            },
        )

    async def test_client_handle_reuses_cached_client_without_request_headers(self):
        build_calls = []
        default_llm = FakeClient("default-llm", content="default-text")

        def fake_build_chat_client_for_name(client_name, default_headers=None):
            build_calls.append({
                "client_name": client_name,
                "default_headers": default_headers,
            })
            return default_llm

        with patch.object(self.llm_module, "_build_chat_client_for_name", side_effect=fake_build_chat_client_for_name):
            handle = self.llm_module._ClientHandle("default_llm")  # pylint: disable=protected-access
            first = await self.llm_module.llm_invoke(
                handle, ["text"], self.llm_module.InvokeOptions(work_node="parse_query"),
            )
            second = await self.llm_module.llm_invoke(
                handle, ["text"], self.llm_module.InvokeOptions(work_node="other_node"),
            )

        self.assertEqual(first, "default-text")
        self.assertEqual(second, "default-text")
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(default_llm.calls[0]["kwargs"], {})
        self.assertEqual(default_llm.calls[1]["kwargs"], {})

    async def test_handover_client_handle_reuses_cached_client_with_agent_profile_headers(self):
        build_calls = []
        default_llm = FakeClient("default-llm", content="default-text")

        def fake_build_chat_client_for_name(client_name, default_headers=None):
            build_calls.append({
                "client_name": client_name,
                "default_headers": default_headers,
            })
            return default_llm

        with patch.object(
            self.llm_module, "_build_chat_client_for_name",
            side_effect=fake_build_chat_client_for_name,
        ), patch.object(
            self.llm_module.settings, "MODEL_INVOKE_HANDOVER", True,
        ):
            handle = self.llm_module._ClientHandle("default_llm")  # pylint: disable=protected-access
            response = await self.llm_module.llm_invoke(
                handle, ["text"], self.llm_module.InvokeOptions(work_node="parse_query"),
            )

        self.assertEqual(response, "default-text")
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(
            default_llm.calls[0]["kwargs"]["extra_headers"],
            {
                "X-Agent-Id": "slidea",
                "X-Work-Node": "parse_query",
            },
        )

    async def test_handover_uses_default_llm_for_premium_text_and_vision_routes(self):
        default_llm = FakeClient("default-llm", content="handover")
        default_vlm = FakeClient("default-vlm", error=RuntimeError("should not be called"))
        premium_llm = FakeClient("premium-llm", error=RuntimeError("should not be called"))

        with patch.object(self.llm_module, "default_llm", default_llm), \
             patch.object(self.llm_module, "default_vlm", default_vlm), \
             patch.object(self.llm_module, "premium_llm", premium_llm), \
             patch.object(self.llm_module.settings, "MODEL_INVOKE_HANDOVER", True), \
             patch.object(self.llm_module.settings, "get_slidea_mode", return_value="PREMIUM"), \
             patch.object(self.llm_module.settings, "has_premium_llm_api_key", return_value=True), \
             patch.object(self.llm_module.settings, "has_premium_llm_config", return_value=True):
            text = await self.llm_module.llm_invoke(
                self.llm_module.ModelRoute.PREMIUM,
                ["text"],
                self.llm_module.InvokeOptions(work_node="generate_ppt_page"),
            )
            vision = await self.llm_module.vlm_invoke(
                self.llm_module.ModelRoute.PREMIUM,
                ["image"],
                self.llm_module.InvokeOptions(work_node="get_img_score"),
            )

        self.assertEqual(text, "handover")
        self.assertEqual(vision, "handover")
        self.assertEqual(len(default_llm.calls), 2)
        self.assertEqual(default_vlm.calls, [])
        self.assertEqual(premium_llm.calls, [])
        self.assertEqual(
            default_llm.calls[0]["bound_kwargs"]["extra_headers"],
            {
                "X-Agent-Id": "slidea",
                "X-Work-Node": "generate_ppt_page",
            },
        )
        self.assertEqual(
            default_llm.calls[1]["bound_kwargs"]["extra_headers"],
            {
                "X-Agent-Id": "slidea",
                "X-Work-Node": "get_img_score",
            },
        )

    def test_handover_builds_any_chat_client_from_default_llm_settings(self):
        build_calls = []

        def fake_build_chat_client(**kwargs):
            build_calls.append(kwargs)
            return FakeClient(kwargs["model"])

        with patch.object(self.llm_module, "ChatOpenAI", object()), \
             patch.object(self.llm_module, "_build_chat_client", side_effect=fake_build_chat_client), \
             patch.object(self.llm_module.settings, "MODEL_INVOKE_HANDOVER", True), \
             patch.object(self.llm_module.settings, "DEFAULT_LLM_MODEL", "ignored-default-model"), \
             patch.object(self.llm_module.settings, "DEFAULT_LLM_API_KEY", "default-key"), \
             patch.object(self.llm_module.settings, "DEFAULT_LLM_API_BASE_URL", "https://model-service.example/v1"), \
             patch.object(self.llm_module.settings, "PREMIUM_LLM_API_KEY", "premium-key"), \
             patch.object(self.llm_module.settings, "PREMIUM_LLM_API_BASE_URL", "https://openrouter.example/v1"), \
             patch.object(self.llm_module.settings, "DEFAULT_VLM_API_KEY", "vlm-key"), \
             patch.object(self.llm_module.settings, "DEFAULT_VLM_API_BASE_URL", "https://vlm.example/v1"):
            self.llm_module._build_chat_client_for_name("premium_llm")  # pylint: disable=protected-access
            self.llm_module._build_chat_client_for_name("default_vlm")  # pylint: disable=protected-access

        self.assertEqual(
            [
                {
                    "model": call["model"],
                    "api_key": call["api_key"],
                    "base_url": call["base_url"],
                }
                for call in build_calls
            ],
            [
                {
                    "model": "",
                    "api_key": "default-key",
                    "base_url": "https://model-service.example/v1",
                },
                {
                    "model": "",
                    "api_key": "default-key",
                    "base_url": "https://model-service.example/v1",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
