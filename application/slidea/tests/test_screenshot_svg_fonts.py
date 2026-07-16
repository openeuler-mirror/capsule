import importlib.util
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _load_screenshot_module():
    screenshot_path = (
        Path(__file__).resolve().parents[1]
        / "core"
        / "ppt_generator"
        / "utils"
        / "screenshot.py"
    )
    spec = importlib.util.spec_from_file_location("test_screenshot_isolated_module", screenshot_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None

    logger = type(
        "Logger",
        (),
        {
            "info": lambda *args, **kwargs: None,
            "warning": lambda *args, **kwargs: None,
        },
    )()
    stubbed_modules = {
        "core.utils.logger": _stub_module("core.utils.logger", logger=logger),
        "core.ppt_generator.utils.browser": _stub_module(
            "core.ppt_generator.utils.browser",
            BrowserManager=type("BrowserManager", (), {}),
        ),
        "core.ppt_generator.utils.common": _stub_module(
            "core.ppt_generator.utils.common",
            build_remote_asset_request_router=lambda: None,
            wait_for_page_assets_ready=lambda *_args, **_kwargs: None,
        ),
    }

    with patch.dict(sys.modules, stubbed_modules, clear=False):
        spec.loader.exec_module(module)
    return module


class ScreenshotSvgFontTests(unittest.TestCase):
    def test_add_cjk_font_fallbacks_prepends_portable_bundled_font(self):
        screenshot = _load_screenshot_module()
        svg = '''<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<text x="60" y="65" font-family="Microsoft YaHei, Arial, sans-serif">中文</text>
</svg>'''.encode("utf-8")

        with patch.object(screenshot, "detect_system_cjk_fonts", return_value=("Source Han Sans SC",)):
            patched = screenshot.add_cjk_font_fallbacks(svg)
        root = ET.fromstring(patched)
        text = next(elem for elem in root.iter() if elem.tag.rsplit("}", 1)[-1] == "text")

        self.assertTrue(text.get("font-family").startswith('"Noto Sans CJK SC"'))
        self.assertIn("Source Han Sans SC", text.get("font-family"))
        self.assertIn("Microsoft YaHei", text.get("font-family"))

    def test_add_cjk_font_fallbacks_keeps_existing_available_cjk_stack(self):
        screenshot = _load_screenshot_module()
        svg = '''<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<text x="60" y="65" font-family="Noto Sans CJK SC, sans-serif">中文</text>
</svg>'''.encode("utf-8")

        with patch.object(screenshot, "detect_system_cjk_fonts", return_value=("Noto Sans CJK SC",)):
            patched = screenshot.add_cjk_font_fallbacks(svg)
        root = ET.fromstring(patched)
        text = next(elem for elem in root.iter() if elem.tag.rsplit("}", 1)[-1] == "text")

        self.assertEqual(text.get("font-family"), "Noto Sans CJK SC, sans-serif")

    def test_detect_system_cjk_fonts_prefers_simplified_sans(self):
        screenshot = _load_screenshot_module()
        fc_list_output = "\n".join(
            [
                "Noto Sans CJK JP,Noto Sans CJK JP Regular",
                "Noto Serif CJK SC,Noto Serif CJK SC Regular",
                "Noto Sans CJK SC,Noto Sans CJK SC Regular",
            ]
        )

        with patch.object(screenshot.shutil, "which", return_value="/usr/bin/fc-list"), \
            patch.object(
                screenshot.subprocess,
                "run",
                return_value=types.SimpleNamespace(returncode=0, stdout=fc_list_output),
            ):
            result = screenshot.detect_system_cjk_fonts(False)

        self.assertEqual(result[0], "Noto Sans CJK SC")


if __name__ == "__main__":
    unittest.main()
