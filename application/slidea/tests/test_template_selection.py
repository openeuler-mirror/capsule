import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch, Mock


def _install_test_stubs():
    class HumanMessage:
        def __init__(self, content):
            self.content = content

    langchain_module = types.ModuleType("langchain")
    langchain_messages_module = types.ModuleType("langchain.messages")
    langchain_messages_module.HumanMessage = HumanMessage
    langchain_module.messages = langchain_messages_module

    async def makedirs(path, exist_ok=False):
        os.makedirs(path, exist_ok=exist_ok)

    aiofiles_module = types.ModuleType("aiofiles")
    aiofiles_os_module = types.ModuleType("aiofiles.os")
    aiofiles_os_module.makedirs = makedirs
    aiofiles_module.os = aiofiles_os_module

    json_repair_module = types.ModuleType("json_repair")
    json_repair_module.repair_json = Mock(return_value={})

    langgraph_module = types.ModuleType("langgraph")
    langgraph_graph_module = types.ModuleType("langgraph.graph")
    langgraph_types_module = types.ModuleType("langgraph.types")
    langgraph_graph_module.StateGraph = object
    langgraph_graph_module.START = "START"
    langgraph_graph_module.END = "END"
    langgraph_types_module.StreamWriter = object
    langgraph_module.types = langgraph_types_module
    langgraph_module.graph = langgraph_graph_module

    langchain_core_module = types.ModuleType("langchain_core")
    langchain_core_runnables_module = types.ModuleType("langchain_core.runnables")
    langchain_core_runnables_module.RunnableConfig = object
    langchain_core_module.runnables = langchain_core_runnables_module

    common_module = types.ModuleType("core.ppt_generator.utils.common")

    def sanitize_filename(name):
        return name.replace(" ", "_")

    async def download_image(url, images_dir):
        return str(Path(images_dir) / Path(url).name)

    def build_image_url(path):
        return path

    async def get_web_images_content(*_args, **_kwargs):
        return "", [], []

    def get_scale_step_value(*_args, **_kwargs):
        return 1

    async def wait_for_page_assets_ready(*_args, **_kwargs):
        return None

    common_module.htmls_to_pptx = Mock(return_value=None)
    common_module.sanitize_filename = sanitize_filename
    common_module.download_image = download_image
    common_module.build_image_url = build_image_url
    common_module.get_web_images_content = get_web_images_content
    common_module.get_scale_step_value = get_scale_step_value
    common_module.wait_for_page_assets_ready = wait_for_page_assets_ready

    llm_module = types.ModuleType("core.utils.llm")
    llm_module.default_llm = object()
    llm_module.default_vlm = object()
    llm_module.ModelRoute = types.SimpleNamespace(DEFAULT="default", PREMIUM="premium")

    async def llm_invoke(*_args, **_kwargs):
        raise AssertionError("test should patch llm_invoke")

    async def vlm_raw_invoke(*_args, **_kwargs):
        raise AssertionError("test should not call vlm_raw_invoke")

    async def vlm_invoke(*_args, **_kwargs):
        raise AssertionError("test should not call vlm_invoke")

    def can_vlm_invoke_route(*_args, **_kwargs):
        return False

    llm_module.llm_invoke = llm_invoke
    llm_module.vlm_raw_invoke = vlm_raw_invoke
    llm_module.vlm_invoke = vlm_invoke
    llm_module.can_vlm_invoke_route = can_vlm_invoke_route

    def make_graph_module(module_name, export_name):
        module = types.ModuleType(module_name)
        setattr(module, export_name, object())
        return module

    sys.modules.update(
        {
            "langchain": langchain_module,
            "langchain.messages": langchain_messages_module,
            "aiofiles": aiofiles_module,
            "aiofiles.os": aiofiles_os_module,
            "json_repair": json_repair_module,
            "langgraph": langgraph_module,
            "langgraph.graph": langgraph_graph_module,
            "langgraph.types": langgraph_types_module,
            "langchain_core": langchain_core_module,
            "langchain_core.runnables": langchain_core_runnables_module,
            "core.ppt_generator.utils.common": common_module,
            "core.utils.llm": llm_module,
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
            "core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph": make_graph_module(
                "core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph",
                "generate_ppt_page_app",
            ),
            "core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.graph": make_graph_module(
                "core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.graph",
                "generate_toc_page_app",
            ),
        }
    )


_install_test_stubs()

from core.ppt_generator.thought_to_ppt.page_generators.state import TemplateResult

page_node = importlib.import_module("core.ppt_generator.thought_to_ppt.page_generators.node")


class TemplateSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_select_ppt_template_reads_descriptions_from_style_json(self):
        with patch.object(
            page_node,
            "llm_invoke",
            return_value=TemplateResult(reason="fit", name="common_dark"),
        ) as llm_mock:
            selected = await page_node.select_ppt_template("技术分享", "章节大纲")

        self.assertEqual(selected, "common_dark")
        prompt = llm_mock.await_args.args[1][0].content
        self.assertIn("'name': 'academic'", prompt)
        self.assertIn("适用于市场调研、技术洞察等专业分享场合所需的PPT，深色风格", prompt)
        self.assertNotIn("<!DOCTYPE html>", prompt)

    async def test_select_ppt_template_falls_back_to_first_style_entry_when_llm_name_invalid(self):
        with patch.object(
            page_node,
            "llm_invoke",
            return_value=TemplateResult(reason="fit", name="missing_template"),
        ):
            selected = await page_node.select_ppt_template("任意主题", "任意大纲")

        self.assertEqual(selected, "academic")

    async def test_prepare_generation_context_node_reads_html_file_directly(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            state = {
                "query": "技术分享",
                "topic": "Template Demo",
                "outline": [],
                "save_dir": str(Path(tmp_dir) / "output"),
                "html_template_name": "cute",
            }
            writer_calls = []

            with patch.object(page_node, "download_outline_images", return_value=[]), patch.object(
                page_node,
                "llm_invoke",
                return_value="中文",
            ):
                result = await page_node.prepare_generation_context_node(state, writer_calls.append)

        self.assertEqual(result["html_template_name"], "cute")
        self.assertIn("<!DOCTYPE html>", result["html_template"])
        self.assertIn("可爱风格PPT", result["html_template"])
        self.assertEqual(result["outline"], [])
        self.assertEqual(len(writer_calls), 1)


if __name__ == "__main__":
    unittest.main()
