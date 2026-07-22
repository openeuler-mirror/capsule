import contextlib
import copy
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
    def setUp(self):
        self.last_quality_state = None
        self.last_content_query = ""

    def _load_script_module(self):
        module = types.ModuleType("patch_render_missing_test_module")
        module.__file__ = str(SCRIPT_PATH)
        code = compile(SCRIPT_PATH.read_text(encoding="utf-8"), str(SCRIPT_PATH), "exec")
        exec(code, module.__dict__)
        return module

    async def _noop_async(self, *_args, **_kwargs):
        return {}

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

    def _make_html_page_generators_node(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.page_generators.node")

        async def prepare_generation_context_node(state, _writer):
            return {
                "outline": state["outline"],
                "save_dir": state["save_dir"],
                "ppt_prompt": "prompt",
                "language": "中文",
                "render_mode": "html",
                "template_name": "common_light",
                "template": "<html></html>",
            }

        module.prepare_generation_context_node = prepare_generation_context_node
        return module

    def _make_svg_page_generators_node(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.svg_page_generators.node")

        async def prepare_generation_context_node(state, _writer):
            prepared_outline = copy.deepcopy(state["outline"])
            for page in prepared_outline:
                page.style_reference_svg = "/runtime/bound-reference.svg"
            return {
                "outline": prepared_outline,
                "save_dir": state["save_dir"],
                "ppt_prompt": "prompt",
                "language": "中文",
                "render_mode": "svg",
                "template_name": "common_light",
                "template": "<svg></svg>",
            }

        async def quality_check_node(state, _writer):
            self.last_quality_state = state
            return {}

        module.prepare_generation_context_node = prepare_generation_context_node
        module.quality_check_node = quality_check_node
        return module

    def _make_html_subgraph_modules(self):
        cover_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph"
        )
        cover_module.generate_cover_thanks_pages_app = types.SimpleNamespace(ainvoke=self._noop_async)

        content_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph"
        )
        content_module.content_page_worker_app = types.SimpleNamespace(ainvoke=self._noop_async)

        sep_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.node"
        )

        async def generate_sep_template_node(_state):
            return {"sep_template": "template"}

        async def generate_sep_page_node(_state):
            return {}

        sep_module.generate_sep_template_node = generate_sep_template_node
        sep_module.generate_sep_page_node = generate_sep_page_node

        toc_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.node"
        )

        async def generate_toc_page_node(_state):
            return {}

        toc_module.generate_toc_page_node = generate_toc_page_node

        return cover_module, content_module, sep_module, toc_module

    def _make_cover_thanks_node_module(self, module_name):
        module = types.ModuleType(module_name)
        module.generate_cover_node = self._noop_async
        module.generate_thanks_node = self._noop_async
        return module

    def _make_svg_subgraph_modules(self):
        cover_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.graph"
        )
        cover_module.generate_cover_thanks_pages_app = types.SimpleNamespace(ainvoke=self._noop_async)

        content_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.graph"
        )

        async def generate_content_page(state):
            if getattr(state["content_page"], "style_reference_svg", "") != "/runtime/bound-reference.svg":
                raise AssertionError("patch render used the persisted outline instead of the prepared outline")
            self.last_content_query = state["query"]
            return {}

        content_module.content_page_worker_app = types.SimpleNamespace(ainvoke=generate_content_page)

        sep_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.svg_page_generators.sep_pages_generator.node"
        )

        async def generate_sep_template_node(_state):
            return {"sep_template": "template"}

        async def generate_sep_page_node(_state):
            return {}

        sep_module.generate_sep_template_node = generate_sep_template_node
        sep_module.generate_sep_page_node = generate_sep_page_node

        toc_module = types.ModuleType(
            "core.ppt_generator.thought_to_ppt.svg_page_generators.toc_page_generator.node"
        )

        async def generate_toc_page_node(_state):
            return {}

        toc_module.generate_toc_page_node = generate_toc_page_node

        return cover_module, content_module, sep_module, toc_module

    def _make_common_module(self):
        module = types.ModuleType("core.ppt_generator.utils.common")

        def sanitize_filename(name):
            return name.replace(" ", "_")

        async def htmls_to_pptx(_htmls, save_dir, filename):
            return str(Path(save_dir) / f"{filename}.pdf"), str(Path(save_dir) / f"{filename}.pptx")

        module.sanitize_filename = sanitize_filename
        module.htmls_to_pptx = htmls_to_pptx
        return module

    def _make_svg_export_module(self):
        module = types.ModuleType("core.ppt_generator.utils.svg_export")

        async def svgs_to_pptx(_svgs, save_dir, filename):
            return "", str(Path(save_dir) / f"{filename}.pptx")

        module.svgs_to_pptx = svgs_to_pptx
        return module

    def _run_main(self, argv, cwd, render_mode="html"):
        html_cover_node = "core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.node"
        svg_cover_node = "core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.node"
        cover_h, content_h, sep_h, toc_h = self._make_html_subgraph_modules()
        cover_s, content_s, sep_s, toc_s = self._make_svg_subgraph_modules()
        fake_modules = {
            "core.ppt_generator.thought_to_ppt.state": self._make_state_module(),
            "core.ppt_generator.thought_to_ppt.page_generators.node": self._make_html_page_generators_node(),
            "core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph": cover_h,
            html_cover_node: self._make_cover_thanks_node_module(html_cover_node),
            "core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.node": sep_h,
            "core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.node": toc_h,
            "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph": content_h,
            "core.ppt_generator.thought_to_ppt.svg_page_generators.node": self._make_svg_page_generators_node(),
            "core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.graph": cover_s,
            svg_cover_node: self._make_cover_thanks_node_module(svg_cover_node),
            "core.ppt_generator.thought_to_ppt.svg_page_generators.sep_pages_generator.node": sep_s,
            "core.ppt_generator.thought_to_ppt.svg_page_generators.toc_page_generator.node": toc_s,
            "core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.graph": content_s,
            "core.ppt_generator.utils.common": self._make_common_module(),
            "core.ppt_generator.utils.svg_export": self._make_svg_export_module(),
        }
        stdout = io.StringIO()

        def local_run_dir(run_id):
            out_dir = Path(cwd) / "output" / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            return str(out_dir)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, fake_modules))
            module = self._load_script_module()
            stack.enter_context(patch.object(sys, "argv", ["patch_render_missing.py", *argv]))
            stack.enter_context(patch.object(module, "run_dir", side_effect=local_run_dir))
            stack.enter_context(
                patch.object(module, "output_files_dir", str(Path(cwd) / "output"))
            )
            stack.enter_context(contextlib.redirect_stdout(stdout))
            module.asyncio.run(module.main())

        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        return json.loads(lines[-1])

    def test_missing_outline_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = self._run_main(["--run-id", "missing"], cwd=tmp_dir)

        self.assertEqual(payload["stage"], "missing_outline")
        self.assertIn("outline", payload["output"]["message"].lower())

    def test_style_pack_snapshot_is_reused_for_patch_render(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "run"
            style_pack = out_dir / "style_pack"
            style_pack.mkdir(parents=True)
            (style_pack / "style-pack.json").write_text("{}", encoding="utf-8")
            module = self._load_script_module()

            resolved = module._resolve_style_pack_dir(str(out_dir))  # pylint: disable=protected-access

        self.assertEqual(resolved, str(style_pack))

    def test_session_patch_inherits_original_request_and_style_quality_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "styled-incomplete"
            out_dir = Path(tmp_dir) / "output" / run_id
            outline_dir = out_dir / "outline"
            slides_dir = out_dir / "slides"
            style_pack = out_dir / "style_pack"
            outline_dir.mkdir(parents=True)
            slides_dir.mkdir()
            style_pack.mkdir()
            (style_pack / "style-pack.json").write_text("{}", encoding="utf-8")
            (out_dir / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "session_id": "styled-session",
                        "text": "original styled request",
                        "render_mode": "svg",
                        "style_pack_dir": str(style_pack),
                    }
                ),
                encoding="utf-8",
            )
            (outline_dir / "outline.json").write_text(
                json.dumps(
                    {
                        "topic": "Styled Demo",
                        "outline": [
                            {
                                "title": "Content",
                                "abstract": "Intro",
                                "type": 1,
                                "index": 0,
                                "reference_doc": "",
                                "reference_images": [],
                                "style_reference_id": "slide-1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (slides_dir / "01_Content.svg").write_text(
                '<svg width="1280" height="720" viewBox="0 0 1280 720" '
                'xmlns="http://www.w3.org/2000/svg"></svg>',
                encoding="utf-8",
            )

            payload = self._run_main(
                ["--session-id", "styled-session", "--indices", "0"],
                cwd=tmp_dir,
                render_mode="svg",
            )
            ppt_payload = json.loads((out_dir / "ppt.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(self.last_content_query, "original styled request")
        self.assertEqual(self.last_quality_state["outline"][0].style_reference_svg, "/runtime/bound-reference.svg")
        self.assertEqual(self.last_quality_state["style_pack_dir"], str(style_pack))
        self.assertEqual(ppt_payload["style_pack_dir"], str(style_pack))

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
            slides_dir = Path(tmp_dir) / "rendered"
            outline_dir.mkdir(parents=True, exist_ok=True)
            slides_dir.mkdir(parents=True, exist_ok=True)
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
                json.dumps({
                    "run_id": run_id,
                    "topic": "Demo Topic",
                    "render_mode": "html",
                    "slides_dir": str(slides_dir),
                }),
                encoding="utf-8",
            )
            (slides_dir / "0.html").write_text("<html></html>", encoding="utf-8")

            payload = self._run_main(["--run-id", run_id, "--indices", "0"], cwd=tmp_dir)
            ppt_payload = json.loads((out_dir / "ppt.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["output"]["stage"], "completed")
        self.assertTrue(payload["output"]["pdf_path"].endswith(".pdf"))
        self.assertEqual(ppt_payload["run_id"], run_id)
        self.assertEqual(ppt_payload["slides_dir"], str(slides_dir))

    def test_svg_success_returns_structured_completed_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "svg-success"
            out_dir = Path(tmp_dir) / "output" / run_id
            outline_dir = out_dir / "outline"
            slides_dir = Path(tmp_dir) / "rendered"
            outline_dir.mkdir(parents=True, exist_ok=True)
            slides_dir.mkdir(parents=True, exist_ok=True)
            (outline_dir / "outline.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "topic": "SVG Demo",
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
                json.dumps(
                    {
                        "run_id": run_id,
                        "topic": "SVG Demo",
                        "render_mode": "svg",
                        "slides_dir": str(slides_dir),
                    }
                ),
                encoding="utf-8",
            )

            svg_dir = slides_dir
            svg_dir.mkdir(parents=True, exist_ok=True)
            (svg_dir / "01_Cover.svg").write_text(
                '<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg"></svg>',
                encoding="utf-8",
            )

            payload = self._run_main(
                ["--run-id", run_id, "--indices", "0"],
                cwd=tmp_dir,
                render_mode="svg",
            )
            ppt_payload = json.loads((out_dir / "ppt.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["output"]["stage"], "completed")
        self.assertEqual(payload["output"]["target_indices"], [0])
        self.assertEqual(ppt_payload["render_mode"], "svg")
        self.assertTrue(ppt_payload["pptx_path"].endswith(".pptx"))


if __name__ == "__main__":
    unittest.main()
