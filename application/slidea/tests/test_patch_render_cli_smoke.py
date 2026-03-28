import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "patch_render_missing.py"


class PatchRenderCliSmokeTests(unittest.TestCase):
    def _load_script_module(self):
        module = types.ModuleType("patch_render_missing_test_module")
        module.__file__ = str(SCRIPT_PATH)
        code = compile(SCRIPT_PATH.read_text(encoding="utf-8"), str(SCRIPT_PATH), "exec")
        exec(code, module.__dict__)
        return module

    def _make_state_module(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.state")

        class PPTPage:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class PageType:
            COVER_THANKS = 4
            TOC = 2
            SEPARATOR = 3
            CONTENT = 1

        module.PPTPage = PPTPage
        module.PageType = PageType
        return module

    def _make_page_generators_module(self):
        node_module = types.ModuleType("core.ppt_generator.thought_to_ppt.page_generators.node")

        async def prepare_generation_context_node(state, _writer):
            return {
                "save_dir": state["save_dir"],
                "ppt_prompt": "prompt",
                "language": "中文",
                "html_template": "<html></html>",
            }

        node_module.prepare_generation_context_node = prepare_generation_context_node
        return node_module

    def _make_graph_modules(self):
        cover_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph"
        )
        cover_module.generate_cover_thanks_pages_app = types.SimpleNamespace(ainvoke=self._noop_async)

        content_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph"
        )
        content_module.content_page_worker_app = types.SimpleNamespace(ainvoke=self._noop_async)
        return cover_module, content_module

    async def _noop_async(self, *_args, **_kwargs):
        return {}

    def _make_sep_module(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.node")

        async def generate_sep_template_node(_state):
            return {"sep_template": "template"}

        async def generate_sep_page_node(_state):
            return {}

        module.generate_sep_template_node = generate_sep_template_node
        module.generate_sep_page_node = generate_sep_page_node
        return module

    def _make_toc_module(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.node")

        async def generate_toc_page_node(_state):
            return {}

        module.generate_toc_page_node = generate_toc_page_node
        return module

    def _make_common_module(self):
        module = types.ModuleType("core.ppt_generator.utils.common")

        def sanitize_filename(name):
            return name.replace(" ", "_")

        async def htmls_to_pptx(_htmls, save_dir, filename):
            return str(Path(save_dir) / f"{filename}.pdf"), str(Path(save_dir) / f"{filename}.pptx")

        module.sanitize_filename = sanitize_filename
        module.htmls_to_pptx = htmls_to_pptx
        return module

    def _run_main(self, argv, cwd):
        cover_module, content_module = self._make_graph_modules()
        fake_modules = {
            "core.ppt_generator.thought_to_ppt.state": self._make_state_module(),
            "core.ppt_generator.thought_to_ppt.page_generators.node": self._make_page_generators_module(),
            "core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph": cover_module,
            "core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.node": self._make_sep_module(),
            "core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.node": self._make_toc_module(),
            "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph": content_module,
            "core.ppt_generator.utils.common": self._make_common_module(),
        }
        stdout = io.StringIO()

        def local_run_dir(_base_dir, run_id):
            out_dir = Path(cwd) / "output" / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            return str(out_dir)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, fake_modules))
            module = self._load_script_module()
            stack.enter_context(patch.object(sys, "argv", ["patch_render_missing.py", *argv]))
            stack.enter_context(patch.object(module, "run_dir", side_effect=local_run_dir))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            module.asyncio.run(module.main())

        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        return json.loads(lines[-1])

    def test_missing_outline_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = self._run_main(["--run-id", "missing"], cwd=tmp_dir)

        self.assertEqual(payload["stage"], "missing_outline")
        self.assertIn("outline", payload["output"]["message"].lower())

    def test_empty_outline_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            outline_dir = Path(tmp_dir) / "output" / "empty" / "outline"
            outline_dir.mkdir(parents=True, exist_ok=True)
            (outline_dir / "outline.json").write_text(json.dumps({"topic": "Demo", "outline": []}), encoding="utf-8")

            payload = self._run_main(["--run-id", "empty"], cwd=tmp_dir)

        self.assertEqual(payload["stage"], "empty_outline")
        self.assertIn("empty", payload["output"]["message"].lower())

    def test_success_returns_structured_completed_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "success"
            out_dir = Path(tmp_dir) / "output" / run_id
            outline_dir = out_dir / "outline"
            render_dir = Path(tmp_dir) / "rendered"
            outline_dir.mkdir(parents=True, exist_ok=True)
            render_dir.mkdir(parents=True, exist_ok=True)
            (outline_dir / "outline.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "topic": "Demo Topic",
                        "outline": [
                            {
                                "title": "Cover",
                                "abstract": "Intro",
                                "type": 1,
                                "index": 0,
                                "reference_doc": "",
                                "reference_images": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (out_dir / "ppt.json").write_text(
                json.dumps({"run_id": run_id, "topic": "Demo Topic", "render_dir": str(render_dir)}),
                encoding="utf-8",
            )
            (render_dir / "0.html").write_text("<html></html>", encoding="utf-8")

            payload = self._run_main(["--run-id", run_id, "--indices", "0"], cwd=tmp_dir)
            ppt_payload = json.loads((out_dir / "ppt.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["output"]["stage"], "completed")
        self.assertTrue(payload["output"]["pdf_path"].endswith(".pdf"))
        self.assertEqual(ppt_payload["run_id"], run_id)
        self.assertEqual(ppt_payload["render_dir"], str(render_dir))


if __name__ == "__main__":
    unittest.main()
