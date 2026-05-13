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
SCRIPT_PATH = ROOT / "scripts" / "run_ppt_pipeline.py"


def _extract_text_resume_input(payload):
    return payload.get("text")


class CliStageSmokeTests(unittest.TestCase):
    def _load_script_module(self):
        module = types.ModuleType("ppt_pipeline_test_module")
        module.__file__ = str(SCRIPT_PATH)
        code = compile(SCRIPT_PATH.read_text(encoding="utf-8"), str(SCRIPT_PATH), "exec")
        exec(code, module.__dict__)
        return module

    def _make_outline_module(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.node")

        async def generate_outline_node(state, config=None):
            return {
                "outline": [
                    {
                        "title": "Cover",
                        "abstract": "Intro",
                        "type": 4,
                        "index": 0,
                        "reference_doc": "",
                        "reference_images": [],
                    }
                ],
                "topic": "Demo Topic",
            }

        async def generate_pages_node(state):
            return {
                "final_pdf_path": "/tmp/demo.pdf",
                "final_pptx_path": "/tmp/demo.pptx",
            }

        module.generate_outline_node = generate_outline_node
        module.generate_pages_node = generate_pages_node
        return module

    def _make_render_mode_probe_module(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.node")

        async def generate_outline_node(state, config=None):
            return {}

        async def generate_pages_node(state):
            mode = state.get("render_mode", "missing")
            return {
                "final_pdf_path": f"/tmp/{mode}.pdf",
                "final_pptx_path": f"/tmp/{mode}.pptx",
            }

        module.generate_outline_node = generate_outline_node
        module.generate_pages_node = generate_pages_node
        return module

    def _make_state_module(self):
        module = types.ModuleType("core.ppt_generator.thought_to_ppt.state")

        class PPTPage:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        module.PPTPage = PPTPage
        module.PPTState = dict
        return module

    def _make_ppt_thought_module(self):
        module = types.ModuleType("core.ppt_generator.ppt_thought.node")

        async def _unused(*_args, **_kwargs):
            return {}

        module.parse_query_node = _unused
        module.get_reference_node = _unused
        module.gather_content_router_node = _unused
        module.simple_search_node = _unused
        module.deep_research_node = _unused
        module.generate_thought_node = _unused
        return module

    def _make_missing_info_module(self):
        module = types.ModuleType("core.ppt_generator.ppt_thought.node")

        class Parsed:
            missing_info = "Need audience"

        async def parse_query_node(*_args, **_kwargs):
            return {"parsed_requirements": Parsed()}

        async def _unused(*_args, **_kwargs):
            return {}

        module.parse_query_node = parse_query_node
        module.get_reference_node = _unused
        module.gather_content_router_node = _unused
        module.simple_search_node = _unused
        module.deep_research_node = _unused
        module.generate_thought_node = _unused
        return module

    def _run_main(self, argv, extra_modules=None, cwd=None):
        module = self._load_script_module()
        stdout = io.StringIO()
        fake_modules = {
            "core.ppt_generator.thought_to_ppt.node": self._make_outline_module(),
            "core.ppt_generator.thought_to_ppt.state": self._make_state_module(),
            "core.ppt_generator.ppt_thought.node": self._make_ppt_thought_module(),
            "core.ppt_generator.ppt_thought.state": types.ModuleType("core.ppt_generator.ppt_thought.state"),
        }
        fake_modules["core.ppt_generator.ppt_thought.state"].ThoughtState = dict
        if extra_modules:
            fake_modules.update(extra_modules)

        def local_run_dir(_base_dir, run_id):
            base = Path(cwd or ROOT)
            out_dir = base / "output" / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            return str(out_dir)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, fake_modules))
            stack.enter_context(patch.object(sys, "argv", ["run_ppt_pipeline.py", *argv]))
            stack.enter_context(patch.object(module, "run_preflight", return_value={"status": "ok", "checks": []}))
            stack.enter_context(patch.object(module, "run_dir", side_effect=local_run_dir))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            module.asyncio.run(module.main())

        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        payload = json.loads(lines[-1])
        return payload

    def test_outline_stage_returns_completed_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "outline-smoke"
            payload = self._run_main(
                ["--text", "demo", "--stages", "outline", "--run-id", run_id],
                cwd=tmp_dir,
            )
            run_payload = json.loads(
                (Path(tmp_dir) / "output" / run_id / "run.json").read_text(encoding="utf-8")
            )

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["output"]["stage"], "completed")
        self.assertEqual(payload["run_id"], run_id)
        self.assertTrue(payload["output_dir"].endswith(f"/output/{run_id}"))
        self.assertEqual(run_payload["run_id"], run_id)
        self.assertEqual(run_payload["session_id"], "local")
        self.assertEqual(run_payload["stages"], "outline")
        self.assertEqual(run_payload["render_mode"], "html")
        self.assertEqual(run_payload["text"], "demo")
        self.assertFalse(run_payload["resume"])

    def test_outline_stage_persists_svg_render_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "outline-svg"
            payload = self._run_main(
                ["--text", "demo", "--stages", "outline", "--render-mode", "svg", "--run-id", run_id],
                cwd=tmp_dir,
            )
            run_payload = json.loads(
                (Path(tmp_dir) / "output" / run_id / "run.json").read_text(encoding="utf-8")
            )

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(run_payload["render_mode"], "svg")

    def test_render_stage_returns_files_from_cached_outline(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "render-smoke"
            out_dir = Path(tmp_dir) / "output" / run_id / "outline"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "outline.json").write_text(
                json.dumps(
                    {
                        "topic": "Demo Topic",
                        "outline": [
                            {
                                "title": "Cover",
                                "abstract": "Intro",
                                "type": 4,
                                "index": 0,
                                "reference_doc": "",
                                "reference_images": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = self._run_main(
                ["--text", "demo", "--stages", "render", "--run-id", run_id],
                cwd=tmp_dir,
            )

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["output"]["files"], ["/tmp/demo.pdf", "/tmp/demo.pptx"])

    def test_svg_render_stage_can_export_cached_svg_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "svg-cache"
            out_dir = Path(tmp_dir) / "output" / run_id
            outline_dir = out_dir / "outline"
            render_dir = Path(tmp_dir) / "rendered"
            svg_output = render_dir / "svg_output"
            outline_dir.mkdir(parents=True, exist_ok=True)
            svg_output.mkdir(parents=True, exist_ok=True)
            (outline_dir / "outline.json").write_text(
                json.dumps(
                    {
                        "topic": "SVG Cache",
                        "outline": [
                            {
                                "title": "Cover",
                                "abstract": "Intro",
                                "type": 4,
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
                        "topic": "SVG Cache",
                        "render_mode": "svg",
                        "render_dir": str(render_dir),
                    }
                ),
                encoding="utf-8",
            )
            (svg_output / "01_Cover.svg").write_text(
                '<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg"></svg>',
                encoding="utf-8",
            )

            quality_module = types.ModuleType("core.ppt_generator.utils.svg_pipeline.quality_checker")

            def check_svg_files(paths):
                return [
                    {"file": Path(path).name, "path": path, "passed": True, "errors": [], "warnings": []}
                    for path in paths
                ]

            def format_quality_issues(_results):
                return ""

            quality_module.check_svg_files = check_svg_files
            quality_module.format_quality_issues = format_quality_issues

            finalize_module = types.ModuleType("core.ppt_generator.utils.svg_pipeline.finalize_svg")

            def finalize_svg_files(paths, _save_dir):
                return paths

            finalize_module.finalize_svg_files = finalize_svg_files

            export_module = types.ModuleType("core.ppt_generator.utils.svg_export")

            async def svgs_to_pptx(_svgs, save_dir, filename):
                return "", str(Path(save_dir) / f"{filename}.pptx")

            export_module.svgs_to_pptx = svgs_to_pptx

            payload = self._run_main(
                ["--text", "demo", "--stages", "render", "--run-id", run_id],
                extra_modules={
                    "core.ppt_generator.utils.svg_pipeline.quality_checker": quality_module,
                    "core.ppt_generator.utils.svg_pipeline.finalize_svg": finalize_module,
                    "core.ppt_generator.utils.svg_export": export_module,
                },
                cwd=tmp_dir,
            )
            ppt_payload = json.loads((out_dir / "ppt.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "completed")
        self.assertTrue(payload["output"]["used_cache"])
        self.assertEqual(ppt_payload["render_mode"], "svg")
        self.assertTrue(ppt_payload["pptx_path"].endswith(".pptx"))

    def test_render_stage_inherits_svg_render_mode_from_run_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "render-inherit-svg"
            out_dir = Path(tmp_dir) / "output" / run_id
            outline_dir = out_dir / "outline"
            outline_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "session_id": "local",
                        "stages": "outline",
                        "render_mode": "svg",
                    }
                ),
                encoding="utf-8",
            )
            (outline_dir / "outline.json").write_text(
                json.dumps(
                    {
                        "topic": "SVG Inherit",
                        "outline": [
                            {
                                "title": "Cover",
                                "abstract": "Intro",
                                "type": 4,
                                "index": 0,
                                "reference_doc": "",
                                "reference_images": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = self._run_main(
                ["--text", "demo", "--stages", "render", "--run-id", run_id],
                extra_modules={
                    "core.ppt_generator.thought_to_ppt.node": self._make_render_mode_probe_module(),
                },
                cwd=tmp_dir,
            )
            run_payload = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["output"]["files"], ["/tmp/svg.pdf", "/tmp/svg.pptx"])
        self.assertEqual(run_payload["render_mode"], "svg")

    def test_resume_preserves_svg_render_mode_from_run_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "resume-svg"
            out_dir = Path(tmp_dir) / "output" / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "session_id": "local",
                        "stages": "all",
                        "render_mode": "svg",
                    }
                ),
                encoding="utf-8",
            )

            pipeline_module = types.ModuleType("scripts.utils.pipeline")
            pipeline_module.extract_resume_input = _extract_text_resume_input

            async def run_thinkflow_app(_app, graph_input, _config, *, emit_ctx):
                return {"stage": "input_required"}

            pipeline_module.run_thinkflow_app = run_thinkflow_app

            graph_module = types.ModuleType("core.ppt_generator.graph")
            graph_module.ppt_workflow = types.SimpleNamespace(
                compile=lambda checkpointer=None: object()
            )

            sqlite_module = types.ModuleType("langgraph.checkpoint.sqlite.aio")

            class FakeSaver:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return False

                @classmethod
                def from_conn_string(cls, _db_name):
                    return cls()

            sqlite_module.AsyncSqliteSaver = FakeSaver

            payload = self._run_main(
                ["--resume", "continue", "--run-id", run_id],
                extra_modules={
                    "scripts.utils.pipeline": pipeline_module,
                    "core.ppt_generator.graph": graph_module,
                    "langgraph.checkpoint.sqlite.aio": sqlite_module,
                },
                cwd=tmp_dir,
            )
            run_payload = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "input_required")
        self.assertEqual(run_payload["render_mode"], "svg")
        self.assertTrue(run_payload["resume"])

    def test_resume_allows_matching_explicit_render_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "resume-svg-explicit"
            out_dir = Path(tmp_dir) / "output" / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "session_id": "local",
                        "stages": "all",
                        "render_mode": "svg",
                    }
                ),
                encoding="utf-8",
            )

            pipeline_module = types.ModuleType("scripts.utils.pipeline")
            pipeline_module.extract_resume_input = _extract_text_resume_input

            async def run_thinkflow_app(_app, graph_input, _config, *, emit_ctx):
                return {"stage": "input_required"}

            pipeline_module.run_thinkflow_app = run_thinkflow_app

            graph_module = types.ModuleType("core.ppt_generator.graph")
            graph_module.ppt_workflow = types.SimpleNamespace(
                compile=lambda checkpointer=None: object()
            )

            sqlite_module = types.ModuleType("langgraph.checkpoint.sqlite.aio")

            class FakeSaver:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return False

                @classmethod
                def from_conn_string(cls, _db_name):
                    return cls()

            sqlite_module.AsyncSqliteSaver = FakeSaver

            payload = self._run_main(
                ["--resume", "continue", "--run-id", run_id, "--render-mode", "svg"],
                extra_modules={
                    "scripts.utils.pipeline": pipeline_module,
                    "core.ppt_generator.graph": graph_module,
                    "langgraph.checkpoint.sqlite.aio": sqlite_module,
                },
                cwd=tmp_dir,
            )
            run_payload = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "input_required")
        self.assertEqual(run_payload["render_mode"], "svg")
        self.assertTrue(run_payload["resume"])

    def test_resume_ignores_conflicting_render_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_id = "resume-svg-conflict"
            out_dir = Path(tmp_dir) / "output" / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "session_id": "local",
                        "stages": "all",
                        "render_mode": "svg",
                    }
                ),
                encoding="utf-8",
            )

            pipeline_module = types.ModuleType("scripts.utils.pipeline")
            pipeline_module.extract_resume_input = _extract_text_resume_input

            async def run_thinkflow_app(_app, graph_input, _config, *, emit_ctx):
                return {"stage": "input_required"}

            pipeline_module.run_thinkflow_app = run_thinkflow_app

            graph_module = types.ModuleType("core.ppt_generator.graph")
            graph_module.ppt_workflow = types.SimpleNamespace(
                compile=lambda checkpointer=None: object()
            )

            sqlite_module = types.ModuleType("langgraph.checkpoint.sqlite.aio")

            class FakeSaver:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return False

                @classmethod
                def from_conn_string(cls, _db_name):
                    return cls()

            sqlite_module.AsyncSqliteSaver = FakeSaver

            payload = self._run_main(
                ["--resume", "continue", "--run-id", run_id, "--render-mode", "html"],
                extra_modules={
                    "scripts.utils.pipeline": pipeline_module,
                    "core.ppt_generator.graph": graph_module,
                    "langgraph.checkpoint.sqlite.aio": sqlite_module,
                },
                cwd=tmp_dir,
            )
            run_payload = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["stage"], "input_required")
        self.assertEqual(run_payload["render_mode"], "svg")
        self.assertTrue(run_payload["resume"])

    def test_render_stage_without_outline_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = self._run_main(
                ["--text", "demo", "--stages", "render", "--run-id", "missing-outline"],
                cwd=tmp_dir,
            )

        self.assertEqual(payload["stage"], "missing_outline")
        self.assertEqual(payload["output"]["stage"], "missing_outline")
        self.assertIn("outline", payload["output"]["message"].lower())

    def test_parse_stage_missing_info_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = self._run_main(
                ["--text", "demo", "--stages", "parse", "--run-id", "parse-missing"],
                extra_modules={
                    "core.ppt_generator.ppt_thought.node": self._make_missing_info_module(),
                },
                cwd=tmp_dir,
            )

        self.assertEqual(payload["stage"], "missing_required_info")
        self.assertEqual(payload["output"]["stage"], "missing_required_info")
        self.assertEqual(payload["output"]["failed_stage"], "parse")
        self.assertIn("audience", payload["output"]["message"].lower())

    def test_research_stage_missing_info_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = self._run_main(
                ["--text", "demo", "--stages", "research", "--run-id", "research-missing"],
                extra_modules={
                    "core.ppt_generator.ppt_thought.node": self._make_missing_info_module(),
                },
                cwd=tmp_dir,
            )

        self.assertEqual(payload["stage"], "missing_required_info")
        self.assertEqual(payload["output"]["stage"], "missing_required_info")
        self.assertEqual(payload["output"]["failed_stage"], "research")
        self.assertIn("audience", payload["output"]["message"].lower())


if __name__ == "__main__":
    unittest.main()
