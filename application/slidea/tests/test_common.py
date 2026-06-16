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


def _load_libreoffice_module():
    """Load core/utils/libreoffice.py in isolation for unit tests.

    LibreOffice helpers used to live in common.py and were extracted into their
    own module. The stub mirrors common's stubs closely enough that we can lean
    on the same minimal logger/config surface.
    """
    libreoffice_path = Path(__file__).resolve().parents[1] / "core" / "utils" / "libreoffice.py"
    spec = importlib.util.spec_from_file_location("test_libreoffice_isolated_module", libreoffice_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None

    stubbed_modules = {
        "core.utils.logger": _stub_module(
            "core.utils.logger",
            logger=type(
                "Logger",
                (),
                {
                    "debug": lambda *args, **kwargs: None,
                    "info": lambda *args, **kwargs: None,
                    "warning": lambda *args, **kwargs: None,
                    "error": lambda *args, **kwargs: None,
                },
            )(),
        ),
        "core.utils.config": _stub_module("core.utils.config", app_base_dir="/tmp"),
    }

    with patch.dict(sys.modules, stubbed_modules, clear=False):
        spec.loader.exec_module(module)
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
                    "debug": lambda *args, **kwargs: None,
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
        # common.py now imports pptx_postprocess at module load; stub it so the
        # stubbed flat "pptx" module above doesn't trip its `from pptx.dml.color
        # import RGBColor` import.
        "core.ppt_generator.utils.pptx_postprocess": _stub_module(
            "core.ppt_generator.utils.pptx_postprocess",
            remove_full_slide_solid_backdrops=lambda *_args, **_kwargs: None,
            flatten_all_groups=lambda *_args, **_kwargs: None,
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
        libreoffice = _load_libreoffice_module()

        with patch.object(libreoffice, "_get_local_libreoffice_executable", return_value=Path("/tmp/local-app-run")), \
            patch.object(
                libreoffice.shutil,
                "which",
                side_effect=lambda candidate: "/usr/bin/libreoffice26.2" if candidate == "libreoffice26.2" else None,
            ), \
            patch.object(libreoffice.platform, "system", return_value="Linux"):
            result = libreoffice.get_available_libreoffice_executable()

        self.assertEqual(result, Path("/usr/bin/libreoffice26.2"))

    def test_get_available_libreoffice_executable_prefers_system_binary_on_macos(self):
        libreoffice = _load_libreoffice_module()

        with patch.object(libreoffice, "_get_local_libreoffice_executable", return_value=Path("/tmp/local-app-run")), \
            patch.object(
                libreoffice.shutil,
                "which",
                side_effect=lambda candidate: "/usr/local/bin/soffice" if candidate == "soffice" else None,
            ), \
            patch.object(libreoffice.platform, "system", return_value="Darwin"):
            result = libreoffice.get_available_libreoffice_executable()

        self.assertEqual(result, Path("/usr/local/bin/soffice"))

    def test_get_local_libreoffice_executable_for_windows_uses_soffice_com(self):
        libreoffice = _load_libreoffice_module()

        with patch.object(libreoffice.platform, "system", return_value="Windows"), \
            patch.dict(libreoffice.os.environ, {"ProgramFiles": r"C:\Program Files"}, clear=True):
            result = libreoffice._get_local_libreoffice_executable()  # pylint: disable=protected-access

        self.assertEqual(result, Path(r"C:\Program Files") / "LibreOffice" / "program" / "soffice.com")

    def test_render_asset_candidate_urls_keep_original_cache_key_with_fallbacks(self):
        common = _load_common_module()

        result = common.get_render_asset_candidate_urls(
            "https://cdn.jsdmirror.com/npm/tailwindcss-cdn@3.4.10/tailwindcss.js"
        )

        self.assertEqual(
            result[:2],
            [
                "https://cdn.jsdmirror.com/npm/tailwindcss-cdn@3.4.10/tailwindcss.js",
                "https://cdn.jsdelivr.net/npm/tailwindcss-cdn@3.4.10/tailwindcss.js",
            ],
        )
        self.assertEqual(len(result), len(set(result)))

    def test_font_css_urls_are_cacheable_render_assets(self):
        common = _load_common_module()

        self.assertTrue(
            common.is_cacheable_render_asset_url(
                "https://fonts.googleapis.cn/css2?family=Noto+Sans+SC:wght@400;700&display=swap",
                None,
            )
        )

    def test_local_urls_are_not_cacheable_render_assets(self):
        common = _load_common_module()

        self.assertFalse(common.is_cacheable_render_asset_url("file:///tmp/demo.css", "stylesheet"))

    def test_rewrite_css_relative_urls_uses_download_source_url(self):
        common = _load_common_module()
        css_body = (
            b"@font-face{src:url(fonts/demo.woff2)}"
            b".icon{background:url('../img/a.png')}"
            b".x{background:url(data:image/png;base64,abc)}"
        )

        rewritten = common.rewrite_css_relative_urls(
            css_body,
            "https://fallback.example.com/npm/katex@0.16.9/dist/katex.min.css",
            "text/css",
        ).decode("utf-8")

        self.assertIn("url(https://fallback.example.com/npm/katex@0.16.9/dist/fonts/demo.woff2)", rewritten)
        self.assertIn("url('https://fallback.example.com/npm/katex@0.16.9/img/a.png')", rewritten)
        self.assertIn("url(data:image/png;base64,abc)", rewritten)


if __name__ == "__main__":
    unittest.main()
