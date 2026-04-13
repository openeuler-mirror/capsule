import importlib
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.ppt_generator.thought_to_ppt.state import PPTPage, PageType


class VlmFallbackTests(unittest.IsolatedAsyncioTestCase):
    @contextmanager
    def _patched_optional_modules(self, extra_modules=None):
        class HumanMessage:
            def __init__(self, content):
                self.content = content

        async def makedirs(path, exist_ok=False):
            Path(path).mkdir(parents=True, exist_ok=exist_ok)

        common_module = types.ModuleType("core.ppt_generator.utils.common")
        common_module.get_scale_step_value = Mock(return_value=1.0)
        common_module.build_image_url = lambda path: path
        common_module.wait_for_page_assets_ready = Mock(return_value=None)
        common_module.get_web_images_content = Mock(return_value=("", [], {}))
        common_module.download_image = Mock(return_value="")
        common_module.sanitize_filename = lambda name: name.replace(" ", "_")
        common_module.htmls_to_pptx = Mock(return_value=None)

        browser_module = types.ModuleType("core.ppt_generator.utils.browser")

        class BrowserManager:
            @staticmethod
            def get_browser_context():
                raise AssertionError("browser context should be patched in tests")

        browser_module.BrowserManager = BrowserManager

        image_module = types.ModuleType("core.ppt_generator.utils.image")
        image_module.generate_ai_image = Mock(return_value=None)
        image_module.get_ai_images_content = Mock(return_value=("", [], {}))

        tavily_module = types.ModuleType("core.utils.tavily_search")
        tavily_module.async_search = Mock(return_value=[])

        json_repair_module = types.ModuleType("json_repair")
        json_repair_module.repair_json = lambda value, **_kwargs: value

        pydantic_module = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

            @classmethod
            def model_json_schema(cls):
                return {"title": cls.__name__, "type": "object"}

        def Field(default=None, **_kwargs):
            return default

        class TypeAdapter:
            def __init__(self, _annotation):
                self._annotation = _annotation

            def json_schema(self):
                return {"type": "array"}

        pydantic_module.BaseModel = BaseModel
        pydantic_module.Field = Field
        pydantic_module.TypeAdapter = TypeAdapter

        langchain_module = types.ModuleType("langchain")
        langchain_messages_module = types.ModuleType("langchain.messages")
        langchain_messages_module.HumanMessage = HumanMessage
        langchain_module.messages = langchain_messages_module

        aiofiles_module = types.ModuleType("aiofiles")
        aiofiles_os_module = types.ModuleType("aiofiles.os")
        aiofiles_os_module.makedirs = makedirs
        aiofiles_module.os = aiofiles_os_module

        langgraph_module = types.ModuleType("langgraph")
        langgraph_types_module = types.ModuleType("langgraph.types")
        langgraph_types_module.StreamWriter = object
        langgraph_module.types = langgraph_types_module

        langchain_core_module = types.ModuleType("langchain_core")
        langchain_core_runnables_module = types.ModuleType("langchain_core.runnables")
        langchain_core_runnables_module.RunnableConfig = object
        langchain_core_module.runnables = langchain_core_runnables_module

        def make_graph_module(module_name, export_name):
            module = types.ModuleType(module_name)
            setattr(module, export_name, object())
            return module

        stub_modules = {
            "langchain": langchain_module,
            "langchain.messages": langchain_messages_module,
            "aiofiles": aiofiles_module,
            "aiofiles.os": aiofiles_os_module,
            "json_repair": json_repair_module,
            "pydantic": pydantic_module,
            "langgraph": langgraph_module,
            "langgraph.types": langgraph_types_module,
            "langchain_core": langchain_core_module,
            "langchain_core.runnables": langchain_core_runnables_module,
            "core.ppt_generator.utils.common": common_module,
            "core.ppt_generator.utils.browser": browser_module,
            "core.utils.tavily_search": tavily_module,
            "core.ppt_generator.utils.image": image_module,
            "core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph": make_graph_module(
                "core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph",
                "generate_ppt_page_app",
            ),
            "core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph": make_graph_module(
                "core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph",
                "generate_cover_thanks_pages_app",
            ),
            "core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.graph": make_graph_module(
                "core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.graph",
                "generate_sep_pages_app",
            ),
            "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph": make_graph_module(
                "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph",
                "generate_content_pages_app",
            ),
            "core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.graph": make_graph_module(
                "core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.graph",
                "generate_toc_page_app",
            ),
        }
        if extra_modules:
            stub_modules.update(extra_modules)

        with patch.dict(sys.modules, stub_modules):
            yield

    def _import_fresh(self, module_name: str):
        for dependency_name in [
            "core.ppt_generator.thought_to_ppt.state",
            "core.ppt_generator.thought_to_ppt.page_generators.state",
            "core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.state",
            "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.state",
        ]:
            sys.modules.pop(dependency_name, None)
        sys.modules.pop(module_name, None)
        return importlib.import_module(module_name)

    async def test_modify_ppt_page_skips_vlm_when_vlm_is_not_configured(self):
        with self._patched_optional_modules():
            base_node = self._import_fresh(
                "core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.node"
            )

            state = {
                "final_file_path": "/tmp/demo.html",
                "iteration": 1,
                "html_content": "<html>existing</html>",
            }

            with patch.object(base_node, "can_vlm_invoke_route", return_value=False), patch.object(
                base_node.BrowserManager,
                "get_browser_context",
                side_effect=AssertionError("browser should not be used without VLM"),
            ):
                result = await base_node.modify_ppt_page_node(state)

        self.assertEqual(result["html_content"], "<html>existing</html>")

    async def test_distribute_images_via_vlm_keeps_outline_unchanged_without_vlm(self):
        outline = [
            PPTPage(
                title="Page A",
                abstract="A",
                type=PageType.CONTENT,
                index=0,
                reference_images=["a.png", "b.png"],
            ),
            PPTPage(
                title="Page B",
                abstract="B",
                type=PageType.CONTENT,
                index=1,
                reference_images=["a.png", "b.png"],
            ),
        ]

        with self._patched_optional_modules():
            page_node = self._import_fresh("core.ppt_generator.thought_to_ppt.page_generators.node")

            with patch.object(page_node, "can_vlm_invoke_route", return_value=False):
                result = await page_node.distribute_images_via_vlm(outline)

        self.assertEqual(result[0].reference_images, ["a.png", "b.png"])
        self.assertEqual(result[1].reference_images, ["a.png", "b.png"])
        self.assertIsNot(result[0], outline[0])

    async def test_get_img_score_uses_fallback_without_vlm(self):
        pil_module = types.ModuleType("PIL")

        class FakeImageFile:
            width = 20
            height = 10

            def verify(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        image_module = types.SimpleNamespace(open=lambda _path: FakeImageFile())
        pil_module.Image = image_module

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "demo.png"
            image_path.write_bytes(b"not-a-real-image")

            with self._patched_optional_modules(extra_modules={"PIL": pil_module}):
                content_node = self._import_fresh(
                    "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.node"
                )

                with patch.object(content_node, "can_vlm_invoke_route", return_value=False):
                    result = await content_node.get_img_score_node(
                        {
                            "relevant_material": "demo material",
                            "image_path": str(image_path),
                            "image_description": "search result description",
                        }
                    )

        score = result["img_scores"][0]
        self.assertIsNotNone(score)
        self.assertEqual(score["img_description"], "search result description")
        self.assertEqual(score["score"], 5.0)
        self.assertEqual(score["size"], "图片高度为10，宽度为20")
        self.assertEqual(score["image_path"], str(image_path))

    async def test_modify_ppt_page_uses_premium_vlm_route_when_available(self):
        with self._patched_optional_modules():
            base_node = self._import_fresh(
                "core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.node"
            )

            class FakePage:
                async def goto(self, *_args, **_kwargs):
                    return None

                async def screenshot(self, path):
                    Path(path).write_bytes(b"fake")

                async def close(self):
                    return None

            class FakeContext:
                async def new_page(self):
                    return FakePage()

                async def close(self):
                    return None

            class FakeBrowser:
                async def new_context(self, **_kwargs):
                    return FakeContext()

            class FakeBrowserContext:
                async def __aenter__(self):
                    return FakeBrowser()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            state = {
                "final_file_path": "/tmp/demo.html",
                "iteration": 1,
                "html_content": "<html>existing</html>",
                "ppt_prompt": "demo prompt",
            }

            with patch.object(base_node, "can_vlm_invoke_route", return_value=True), \
                 patch.object(base_node, "llm_invoke", AsyncMock(return_value="页面摘要")), \
                 patch.object(
                     base_node,
                     "vlm_raw_invoke",
                     AsyncMock(return_value=SimpleNamespace(content="```html\n<html>premium</html>\n```")),
                 ) as vlm_mock, \
                 patch.object(base_node, "wait_for_page_assets_ready", AsyncMock(return_value=None)), \
                 patch.object(base_node.BrowserManager, "get_browser_context", return_value=FakeBrowserContext()):
                result = await base_node.modify_ppt_page_node(state)

        self.assertEqual(result["html_content"], "<html>premium</html>")
        self.assertEqual(vlm_mock.await_args.args[0], base_node.ModelRoute.PREMIUM)

    async def test_distribute_images_via_vlm_runs_when_premium_route_is_available(self):
        outline = [
            PPTPage(
                title="Page A",
                abstract="A",
                type=PageType.CONTENT,
                index=0,
                reference_images=["a.png"],
            )
        ]

        with self._patched_optional_modules():
            page_node = self._import_fresh("core.ppt_generator.thought_to_ppt.page_generators.node")

            with patch.object(page_node, "can_vlm_invoke_route", return_value=True), \
                 patch.object(page_node, "detect_distribution_mode", return_value="global"), \
                 patch.object(page_node, "_process_global_mode", AsyncMock(return_value=None)) as process_mock:
                await page_node.distribute_images_via_vlm(outline)

        process_mock.assert_awaited_once()

    async def test_get_img_score_uses_premium_route_when_available(self):
        pil_module = types.ModuleType("PIL")

        class FakeImageFile:
            width = 20
            height = 10

            def verify(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def convert(self, _mode):
                return self

            def save(self, *_args, **_kwargs):
                return None

        image_module = types.SimpleNamespace(open=lambda _path: FakeImageFile())
        pil_module.Image = image_module

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "demo.png"
            image_path.write_bytes(b"not-a-real-image")

            with self._patched_optional_modules(extra_modules={"PIL": pil_module}):
                content_node = self._import_fresh(
                    "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.node"
                )

                with patch.object(content_node, "can_vlm_invoke_route", return_value=True), \
                     patch.object(
                         content_node,
                         "vlm_invoke",
                         AsyncMock(return_value=SimpleNamespace(img_description="premium description", score=8.2)),
                     ) as vlm_mock:
                    result = await content_node.get_img_score_node(
                        {
                            "relevant_material": "demo material",
                            "image_path": str(image_path),
                            "image_description": "search result description",
                        }
                    )

        score = result["img_scores"][0]
        self.assertEqual(score["img_description"], "premium description")
        self.assertEqual(score["score"], 8.2)
        self.assertEqual(vlm_mock.await_args.args[0], content_node.ModelRoute.DEFAULT)


if __name__ == "__main__":
    unittest.main()
