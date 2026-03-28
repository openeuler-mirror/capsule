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
        self.assertEqual(run_payload["text"], "demo")
        self.assertFalse(run_payload["resume"])

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
