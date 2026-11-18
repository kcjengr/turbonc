"""Tests for the theme generator and view-style token renderer.

PySide6 is stubbed because ``generate_themes`` only needs Qt for its
background-texture helper; the palette/substitution functions used at runtime
are pure Python.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEMES = ROOT / "src" / "tnc" / "themes"


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    module.__path__ = []
    sys.modules[name] = module
    return module


def _load_generate():
    pyside = _stub("PySide6")
    qtgui = _stub("PySide6.QtGui", QColor=object, QImage=object,
                  QPainter=object)
    pyside.QtGui = qtgui
    tnc = _stub("tnc")
    spec = importlib.util.spec_from_file_location(
        "tnc.generate_themes", ROOT / "src" / "tnc" / "generate_themes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tnc.generate_themes"] = module
    tnc.generate_themes = module
    spec.loader.exec_module(module)
    return module


def _load_view_styles(generate):
    spec = importlib.util.spec_from_file_location(
        "tnc.view_styles", ROOT / "src" / "tnc" / "view_styles.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tnc.view_styles"] = module
    spec.loader.exec_module(module)
    return module


class ThemeRegistrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gen = _load_generate()
        cls.styles = _load_view_styles(cls.gen)

    def test_generated_theme_files_are_in_sync(self):
        dark = (THEMES / "dark.qss").read_text(encoding="utf-8")
        light = self.gen.derive_light(dark)
        expected = {
            "dark.qss": dark,
            "light.qss": self.gen.substitute(light, self.gen.ACCENTS["cyan"],
                                             "light"),
        }
        for base_name, base_text in (("dark", dark), ("light", light)):
            for accent_name, accent in self.gen.ACCENTS.items():
                if accent_name == "cyan":
                    continue
                expected["%s-%s.qss" % (base_name, accent_name)] = (
                    self.gen.substitute(base_text, accent, base_name))
        for theme_name, (base_name, accent, pressed) in self.gen.MONO_THEMES.items():
            base_text = dark if base_name == "dark" else light
            expected["%s.qss" % theme_name] = self.gen.substitute_mono(
                base_text, (accent, pressed), base_name)

        mismatched = [name for name, text in expected.items()
                      if (THEMES / name).read_text(encoding="utf-8") != text]
        self.assertEqual(mismatched, [],
                         "run python src/tnc/generate_themes.py")

    def test_view_style_files_exist(self):
        for name, filename in self.styles.VIEW_STYLE_FILES.items():
            self.assertTrue((THEMES / filename).is_file(), name)

    def test_render_substitutes_palette_tokens(self):
        rendered = self.styles.render_view_style(
            "@main@ / @main_rgb@ / @text@", (0, 229, 255), True)
        self.assertEqual(rendered, "#00e5ff / 0, 229, 255 / #dcefff")

    def test_unknown_tokens_are_left_for_caller(self):
        rendered = self.styles.render_view_style("@check_icon@", (0, 229, 255), True)
        self.assertEqual(rendered, "@check_icon@")

    def test_scale_rgba_alphas(self):
        self.assertEqual(
            self.styles.scale_rgba_alphas("rgba(1,2,3,100)", 0.5),
            "rgba(1, 2, 3, 50)")
        self.assertEqual(
            self.styles.scale_rgba_alphas("#abcdef", 0.5), "#abcdef")

    def test_tint_overlay_is_rgba(self):
        self.assertEqual(len(self.styles.tint_overlay((200, 100, 50), True)), 4)


if __name__ == "__main__":
    unittest.main()
