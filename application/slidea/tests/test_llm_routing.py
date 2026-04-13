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
    def __init__(self, model_name: str, *, error: Exception | None = None, content: str = "ok"):
        self.model_name = model_name
        self._error = error
        self._content = content

    async def ainvoke(self, _args, config=None):
        if self._error is not None:
            raise self._error
        return FakeResponse(self._content)

    def with_structured_output(self, *_args, **_kwargs):
        return self


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


if __name__ == "__main__":
    unittest.main()
