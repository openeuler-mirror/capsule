import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _load_common_module():
    common_path = Path(__file__).resolve().parents[1] / "core" / "ppt_generator" / "utils" / "common.py"
    spec = importlib.util.spec_from_file_location("test_common_isolated_module", common_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None

    stubbed_modules = {
        "httpx": _stub_module("httpx"),
        "PyPDF2": _stub_module("PyPDF2", PdfWriter=type("PdfWriter", (), {})),
        "pptx": _stub_module("pptx", Presentation=type("Presentation", (), {})),
        "PIL": _stub_module("PIL", Image=type("Image", (), {})),
        "core.utils.logger": _stub_module(
            "core.utils.logger",
            logger=type(
                "Logger",
                (),
                {
                    "info": lambda *args, **kwargs: None,
                    "warning": lambda *args, **kwargs: None,
                    "error": lambda *args, **kwargs: None,
                },
            )(),
        ),
        "core.utils.config": _stub_module("core.utils.config", app_base_dir="/tmp"),
        "core.utils.image_payload": _stub_module("core.utils.image_payload", build_image_url=lambda value: value),
        "core.ppt_generator.utils.browser": _stub_module(
            "core.ppt_generator.utils.browser",
            BrowserManager=type("BrowserManager", (), {}),
        ),
    }

    with patch.dict(sys.modules, stubbed_modules, clear=False):
        spec.loader.exec_module(module)
    return module


class CommonUtilsTests(unittest.TestCase):
    def test_build_libreoffice_pdf_to_pptx_command_uses_pdf_import_filter(self):
        common = _load_common_module()
        command = common._build_libreoffice_pdf_to_pptx_command(
            Path("/tmp/libreoffice-app/AppRun"),
            "/tmp/demo.pdf",
            "/tmp",
        )

        self.assertEqual(
            command,
            [
                "/tmp/libreoffice-app/AppRun",
                "--headless",
                "--nologo",
                "--nolockcheck",
                "--nodefault",
                "--infilter=impress_pdf_import",
                "--convert-to",
                "pptx:Impress MS PowerPoint 2007 XML",
                "--outdir",
                "/tmp",
                "/tmp/demo.pdf",
            ],
        )

    def test_get_available_libreoffice_executable_prefers_system_binary(self):
        common = _load_common_module()

        with patch.object(common, "_get_local_libreoffice_executable", return_value=Path("/tmp/local-app-run")), \
            patch.object(
                common.shutil,
                "which",
                side_effect=lambda candidate: "/usr/bin/libreoffice26.2" if candidate == "libreoffice26.2" else None,
            ), \
            patch.object(common.platform, "system", return_value="Linux"):
            result = common._get_available_libreoffice_executable()

        self.assertEqual(result, Path("/usr/bin/libreoffice26.2"))

    def test_get_available_libreoffice_executable_prefers_system_binary_on_macos(self):
        common = _load_common_module()

        with patch.object(common, "_get_local_libreoffice_executable", return_value=Path("/tmp/local-app-run")), \
            patch.object(
                common.shutil,
                "which",
                side_effect=lambda candidate: "/usr/local/bin/soffice" if candidate == "soffice" else None,
            ), \
            patch.object(common.platform, "system", return_value="Darwin"):
            result = common._get_available_libreoffice_executable()

        self.assertEqual(result, Path("/usr/local/bin/soffice"))

    def test_get_local_libreoffice_executable_for_windows_uses_soffice_com(self):
        common = _load_common_module()

        with patch.object(common.platform, "system", return_value="Windows"), \
            patch.dict(common.os.environ, {"ProgramFiles": r"C:\Program Files"}, clear=True):
            result = common._get_local_libreoffice_executable()

        self.assertEqual(result, Path(r"C:\Program Files") / "LibreOffice" / "program" / "soffice.com")


if __name__ == "__main__":
    unittest.main()
