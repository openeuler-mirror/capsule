import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PipelineContractTests(unittest.TestCase):
    def test_readme_describes_run_dir_as_cache_and_index(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("`output/<run_id>/` is the run cache and metadata directory", content)
        self.assertIn("final rendered artifacts are written to the render directory recorded in `ppt.json`", content)

    def test_skill_describes_actual_ppt_json_location(self):
        content = (ROOT / "skill/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("`ppt.json`", content)
        self.assertIn("stored at `output/<run_id>/ppt.json`", content)

    def test_deep_research_context_declares_os_import(self):
        source = (ROOT / "core/deep_research/context.py").read_text(encoding="utf-8")
        module = ast.parse(source)

        imported_names = set()
        for node in module.body:
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)

        self.assertIn("os", imported_names)

    def test_ppt_thought_uses_shared_settings_singleton_for_forced_mode(self):
        source = (ROOT / "core/ppt_generator/ppt_thought/node.py").read_text(encoding="utf-8")

        self.assertIn("from core.utils.config import settings, app_base_dir", source)
        self.assertIn("forced_mode = settings.RESEARCH_MODE_FORCE.strip().lower()", source)
        self.assertNotIn("forced_mode = Settings().RESEARCH_MODE_FORCE.strip().lower()", source)

    def test_generate_thought_saves_cached_outputs_once(self):
        source = (ROOT / "core/ppt_generator/ppt_thought/node.py").read_text(encoding="utf-8")

        self.assertEqual(source.count('save_text(f"{run_dir}/thought/thought.md", thought or "")'), 1)
        self.assertEqual(source.count('save_text(f"{run_dir}/references/references_all.txt", references or "")'), 1)

    def test_deep_research_context_uses_shared_settings_singleton(self):
        source = (ROOT / "core/deep_research/context.py").read_text(encoding="utf-8")

        self.assertIn("from core.utils.config import settings, app_base_dir", source)
        self.assertNotIn("runtime_settings = Settings()", source)
        self.assertNotIn("if Settings().DISABLE_EMBEDDING:", source)
        self.assertIn("model = settings.EMBEDDING_MODEL", source)
        self.assertIn("if settings.DISABLE_EMBEDDING:", source)

    def test_outline_node_avoids_render_stack_imports_at_module_load(self):
        source = (ROOT / "core/ppt_generator/thought_to_ppt/node.py").read_text(encoding="utf-8")
        module_preamble = source.split("async def generate_outline_node", 1)[0]
        generate_pages_impl = source.split("async def generate_pages_node", 1)[1]

        self.assertNotIn(
            "from core.ppt_generator.thought_to_ppt.page_generators.graph import generate_pages_app",
            module_preamble,
        )
        self.assertNotIn("from core.ppt_generator.utils.common import get_markdown_images", module_preamble)
        self.assertIn("from core.ppt_generator.utils.markdown import get_markdown_images", module_preamble)
        self.assertIn(
            "from core.ppt_generator.thought_to_ppt.page_generators.graph import generate_pages_app",
            generate_pages_impl,
        )

    def test_outline_cache_includes_run_metadata(self):
        source = (ROOT / "core/ppt_generator/thought_to_ppt/node.py").read_text(encoding="utf-8")

        self.assertIn("run_id = get_run_id(config)", source)
        self.assertIn('"run_id": run_id', source)

    def test_ppt_cache_includes_render_metadata(self):
        source = (ROOT / "core/ppt_generator/thought_to_ppt/page_generators/node.py").read_text(encoding="utf-8")

        self.assertIn("run_id = get_run_id(config)", source)
        self.assertIn('"run_id": run_id', source)
        self.assertIn('"topic": state["topic"]', source)
        self.assertIn('"render_dir": save_dir', source)

    def test_cli_scripts_use_shared_payload_emitter(self):
        run_pipeline = (ROOT / "scripts/run_ppt_pipeline.py").read_text(encoding="utf-8")
        patch_render = (ROOT / "scripts/patch_render_missing.py").read_text(encoding="utf-8")

        self.assertIn("from scripts.utils.cli_output import emit_stage_payload", run_pipeline)
        self.assertIn("from scripts.utils.cli_output import emit_stage_payload", patch_render)
        self.assertNotIn("def _emit_stage_payload", run_pipeline)
        self.assertNotIn("def _emit_payload", patch_render)

    def test_run_pipeline_extracts_stage_orchestration_helpers(self):
        source = (ROOT / "scripts/run_ppt_pipeline.py").read_text(encoding="utf-8")

        self.assertIn("def _apply_runtime_overrides(args):", source)
        self.assertIn("def _build_run_metadata(args, run_id: str):", source)
        self.assertIn("async def _run_all_stages(args, run_id: str, out_dir: str):", source)
        self.assertIn("async def _run_staged_pipeline(args, stages: list[str], run_id: str, out_dir: str):", source)

    def test_patch_render_extracts_orchestration_helpers(self):
        source = (ROOT / "scripts/patch_render_missing.py").read_text(encoding="utf-8")

        self.assertIn("def _load_outline_or_emit(args, out_dir: str):", source)
        self.assertIn("def _resolve_save_dir(out_dir: str, topic: str):", source)
        self.assertIn("def _resolve_target_indices(args, save_dir: str, outline):", source)
        self.assertIn("async def _patch_render(args, out_dir: str, outline, topic: str, save_dir: str, target_indices: list[int]):", source)


if __name__ == "__main__":
    unittest.main()
