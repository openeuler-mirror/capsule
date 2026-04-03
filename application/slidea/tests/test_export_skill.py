import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.export_skill import bootstrap_skill, export_skill


class ExportSkillTests(unittest.TestCase):
    def test_export_skill_builds_clean_skill_layout(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir) / "slidea"

            exported_dir = export_skill(target_dir)

            self.assertEqual(exported_dir, target_dir)
            self.assertTrue((target_dir / "SKILL.md").exists())
            self.assertFalse((target_dir / "README.md").exists())
            self.assertTrue((target_dir / "INSTALL.md").exists())
            self.assertTrue((target_dir / "core").exists())
            self.assertTrue((target_dir / "scripts" / "run_ppt_pipeline.py").exists())
            self.assertTrue((target_dir / "scripts" / "patch_render_missing.py").exists())
            self.assertTrue((target_dir / "scripts" / "utils").exists())
            self.assertTrue((target_dir / "scripts" / "install" / "install.py").exists())
            self.assertTrue(
                (target_dir / "scripts" / "install" / "extra_install_linux_rhel.sh").exists()
            )
            self.assertFalse((target_dir / "scripts" / "export_skill.py").exists())

    def test_bootstrap_skill_runs_exported_installer(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir) / "slidea"
            export_skill(target_dir)

            with patch("scripts.export_skill.subprocess.run") as mock_run:
                bootstrap_skill(target_dir)

            mock_run.assert_called_once()
            command = mock_run.call_args.args[0]
            self.assertEqual(command[1], str(target_dir / "scripts" / "install" / "install.py"))
            self.assertEqual(mock_run.call_args.kwargs["cwd"], target_dir)
            self.assertTrue(mock_run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
