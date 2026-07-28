import importlib
import inspect
import sys
import unittest
from unittest.mock import AsyncMock, patch


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {}
        self.response_metadata = {}


class FakeClient:
    def __init__(self, model_name, *, error=None, content="ok"):
        self.model_name = model_name
        self._error = error
        self._content = content
        self.calls = []

    async def ainvoke(self, args, config=None):
        self.calls.append({"args": args, "config": config})
        if self._error is not None:
            raise self._error
        return FakeResponse(self._content)

    def with_structured_output(self, *_args, **_kwargs):
        raise AssertionError("structured output must be parsed after the plain invocation")


class StructuredResult:
    def __init__(self, value):
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


class ModelInvocationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.modules.pop("core.utils.llm", None)
        self.llm_module = importlib.import_module("core.utils.llm")

    async def test_text_and_vision_use_their_default_clients(self):
        text_client = FakeClient("default-llm", content="text-result")
        vision_client = FakeClient("default-vlm", content="vision-result")

        with patch.object(self.llm_module, "default_llm", text_client), patch.object(
            self.llm_module, "default_vlm", vision_client
        ):
            text = await self.llm_module.llm_invoke(["text"])
            vision = await self.llm_module.vlm_invoke(["image"])
            raw_vision = await self.llm_module.vlm_raw_invoke(["raw-image"])

        self.assertEqual(text, "text-result")
        self.assertEqual(vision, "vision-result")
        self.assertEqual(raw_vision.content, "vision-result")
        self.assertEqual([call["args"] for call in text_client.calls], [["text"]])
        self.assertEqual(
            [call["args"] for call in vision_client.calls],
            [["image"], ["raw-image"]],
        )

    def test_default_clients_use_independent_settings(self):
        with patch.object(self.llm_module.settings, "DEFAULT_LLM_MODEL", "text-model"), patch.object(
            self.llm_module.settings, "DEFAULT_LLM_API_KEY", "text-key"
        ), patch.object(
            self.llm_module.settings,
            "DEFAULT_LLM_API_BASE_URL",
            "https://text.example/v1",
        ), patch.object(
            self.llm_module.settings, "DEFAULT_VLM_MODEL", "vision-model"
        ), patch.object(
            self.llm_module.settings, "DEFAULT_VLM_API_KEY", "vision-key"
        ), patch.object(
            self.llm_module.settings,
            "DEFAULT_VLM_API_BASE_URL",
            "https://vision.example/v1",
        ):
            text_config = self.llm_module._resolve_chat_client_config("default_llm")
            vision_config = self.llm_module._resolve_chat_client_config("default_vlm")

        self.assertEqual(
            (text_config.request_model_name, text_config.api_key, text_config.base_url),
            ("text-model", "text-key", "https://text.example/v1"),
        )
        self.assertEqual(
            (vision_config.request_model_name, vision_config.api_key, vision_config.base_url),
            ("vision-model", "vision-key", "https://vision.example/v1"),
        )

    async def test_pydantic_schema_is_parsed_after_plain_invocation(self):
        text_client = FakeClient("default-llm", content='{"value": "parsed"}')

        with patch.object(self.llm_module, "default_llm", text_client):
            result = await self.llm_module.llm_invoke(
                ["text"],
                self.llm_module.InvokeOptions(pydantic_schema=StructuredResult),
            )

        self.assertIsInstance(result, StructuredResult)
        self.assertEqual(result.value, "parsed")
        self.assertEqual(len(text_client.calls), 1)

    async def test_json_schema_is_validated_after_plain_invocation(self):
        text_client = FakeClient("default-llm", content='{"value": "parsed"}')
        schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

        with patch.object(self.llm_module, "default_llm", text_client):
            result = await self.llm_module.llm_invoke(
                ["text"],
                self.llm_module.InvokeOptions(json_schema=schema),
            )

        self.assertEqual(result, {"value": "parsed"})
        self.assertEqual(len(text_client.calls), 1)

    async def test_invalid_image_request_is_not_retried(self):
        class InvalidImageRequestError(RuntimeError):
            status_code = 400

        vision_client = FakeClient(
            "default-vlm",
            error=InvalidImageRequestError(
                "The image data is invalid. Please ensure the image is a valid image."
            ),
        )

        with patch.object(self.llm_module, "default_vlm", vision_client), patch.object(
            self.llm_module, "MAX_INVOKE_ATTEMPTS", 5
        ), patch.object(
            self.llm_module.asyncio,
            "sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            with self.assertRaises(self.llm_module.LLMInvokeError):
                await self.llm_module.vlm_raw_invoke(["image"])

        self.assertEqual(len(vision_client.calls), 1)
        sleep_mock.assert_not_awaited()

    async def test_text_invocation_retries_and_reports_model(self):
        text_client = FakeClient("default-llm", error=RuntimeError("timeout"))

        with patch.object(self.llm_module, "default_llm", text_client), patch.object(
            self.llm_module, "MAX_INVOKE_ATTEMPTS", 3
        ), patch.object(
            self.llm_module.asyncio,
            "sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            with self.assertRaisesRegex(
                self.llm_module.LLMInvokeError,
                "default-llm",
            ):
                await self.llm_module.llm_invoke(["text"])

        self.assertEqual(len(text_client.calls), 3)
        self.assertEqual(sleep_mock.await_count, 2)

    async def test_client_handle_creates_and_reuses_one_default_client(self):
        built_client = FakeClient("default-llm", content="text-result")

        with patch.object(
            self.llm_module,
            "_build_chat_client_for_name",
            return_value=built_client,
        ) as build_mock:
            handle = self.llm_module._ClientHandle("default_llm")
            with patch.object(self.llm_module, "default_llm", handle):
                first = await self.llm_module.llm_invoke(["first"])
                second = await self.llm_module.llm_invoke(["second"])

        self.assertEqual(first, "text-result")
        self.assertEqual(second, "text-result")
        build_mock.assert_called_once_with("default_llm")

    async def test_model_clients_cannot_be_passed_through_public_interfaces(self):
        self.assertEqual(
            list(inspect.signature(self.llm_module.llm_invoke).parameters),
            ["args", "options"],
        )
        self.assertEqual(
            list(inspect.signature(self.llm_module.vlm_invoke).parameters),
            ["args", "options"],
        )
        self.assertEqual(
            list(inspect.signature(self.llm_module.vlm_raw_invoke).parameters),
            ["args", "config", "schema_name"],
        )

        with self.assertRaises(TypeError):
            await self.llm_module.llm_invoke(
                FakeClient("custom"),
                ["text"],
                self.llm_module.InvokeOptions(),
            )
        with self.assertRaises(TypeError):
            await self.llm_module.vlm_invoke(
                FakeClient("custom"),
                ["image"],
                self.llm_module.InvokeOptions(),
            )
        with self.assertRaises(TypeError):
            await self.llm_module.vlm_raw_invoke(
                FakeClient("custom"),
                ["image"],
                None,
                "plain_text",
            )


if __name__ == "__main__":
    unittest.main()
