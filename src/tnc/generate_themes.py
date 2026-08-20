#!/usr/bin/env python3
"""Generate cyberpunk theme variants from the canonical dark.qss / light.qss.

Each theme is a base (dark/light) x accent (cyan/magenta/green/amber/blue/
orange). The canonical files are the cyan pair; every other accent is produced
by substituting the accent tokens AND the full secondary palette (bevels,
borders, text tints, power/home button), so the whole widget set carries the
accent hue. Edits to dark.qss / light.qss propagate by re-running:

    python3 generate_themes.py

Safety colors are kept constant (estop red, armed green). The frosted dialog
glass is painted in code and stays cyan.

The color scheme is consolidated per base: ``palette()`` clamps the accent's
lightness into a visibility band so every accent has the same visual weight
as the reference neon cyan. Dark base keeps accents bright (the canonical
dark.qss is untouched); light base deepens them so borders, bevels and text
tints don't wash out on the near-white background, and the on-accent text
flips to light. The canonical light.qss is regenerated through the same
substitution pipeline as the variants (cyan consolidated for light).
"""

import colorsys
import pathlib
import re

from PySide6.QtGui import QColor, QImage, QPainter

HERE = pathlib.Path(__file__).resolve().parent
THEMES = HERE / "themes"

# accent -> (main hex, pressed hex). The rest of the palette is derived.
ACCENTS = {
    "cyan":     ("#00e5ff", "#ff2bd6"),
    "magenta":  ("#ff2bd6", "#00e5ff"),
    "green":    ("#00ff9d", "#ff2bd6"),
    "amber":    ("#ffb300", "#00e5ff"),
    "blue":     ("#2979ff", "#ff2bd6"),
    "orange":   ("#ff7300", "#00e5ff"),
}

BASES = {"dark": "dark.qss", "light": "light.qss"}


# Background textures: the originals in themes/backgrounds/ are the source;
# dimmed copies are generated so the UI stays readable under the translucent
# cyberpunk panels (50% black overlay).
BACKGROUNDS_DIR = THEMES / "backgrounds"
BACKGROUND_DIM = 0.5


def write_background_variants():
    """Create the dimmed copies of the background textures."""
    BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    produced = []
    for src in sorted(BACKGROUNDS_DIR.glob("*.jpg")):
        if src.stem.endswith("-dim"):
            continue
        img = QImage(str(src))
        if img.isNull():
            print(f"  !! skipping unreadable image {src.name}")
            continue
        img = img.convertToFormat(QImage.Format_ARGB32)
        painter = QPainter(img)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.fillRect(img.rect(),
                         QColor(0, 0, 0, round(255 * BACKGROUND_DIM)))
        painter.end()
        out = src.with_name(src.stem + "-dim.jpg")
        img.save(str(out), "JPG", 92)
        produced.append(out.name)
    return produced

# canonical light base is DERIVED from dark.qss via this map, so the two
# canonical themes can never drift apart. Alpha values match the glassy
# fills in dark.qss.
LIGHT_MAP = {
    "#0a0e14": "#eef2f7",
    "#0d2333": "#dde9f3",
    "rgba(38, 56, 84, 170)": "rgba(244, 248, 253, 170)",
    "rgba(20, 32, 50, 170)": "rgba(240, 245, 251, 170)",
    "rgba(9, 14, 22, 190)": "rgba(214, 224, 236, 190)",
    "rgba(36, 60, 92, 180)": "rgba(232, 242, 250, 180)",
    "rgba(24, 44, 70, 180)": "rgba(228, 238, 248, 180)",
    "rgba(10, 18, 30, 200)": "rgba(205, 216, 230, 200)",
    "rgba(16, 6, 16, 160)": "rgba(228, 214, 224, 160)",
    "rgba(44, 10, 40, 160)": "rgba(238, 214, 230, 160)",
    "rgba(70, 16, 58, 180)": "rgba(244, 222, 236, 180)",
    "rgba(12, 17, 24, 160)": "rgba(228, 234, 242, 160)",
    "rgba(42, 30, 40, 235)": "rgba(250, 242, 244, 235)",
    "rgba(30, 22, 28, 235)": "rgba(250, 238, 240, 235)",
    "rgba(12, 10, 14, 235)": "rgba(224, 218, 222, 235)",
    "rgba(30, 40, 64, 170)": "rgba(243, 247, 253, 170)",
    "rgba(20, 26, 44, 170)": "rgba(242, 246, 252, 170)",
    "rgba(10, 12, 20, 190)": "rgba(216, 222, 232, 190)",
    "rgba(8, 13, 21, 235)": "rgba(238, 244, 250, 235)",
    "rgba(8, 13, 21, 245)": "rgba(238, 244, 250, 245)",
    "rgba(7, 12, 19, 245)": "rgba(240, 246, 252, 245)",
    "rgba(7, 11, 18, 220)": "rgba(226, 234, 244, 220)",
    "rgba(22, 34, 54, 205)": "rgba(246, 250, 254, 205)",
    "rgba(14, 22, 36, 205)": "rgba(242, 248, 254, 205)",
    "rgba(10, 16, 26, 215)": "rgba(222, 230, 240, 215)",
    "rgba(12, 18, 30, 240)": "rgba(226, 234, 244, 240)",
    "rgba(12, 18, 30, 175)": "rgba(226, 234, 244, 175)",
    "rgba(12, 18, 30, 248)": "rgba(232, 239, 248, 248)",
    "rgba(20, 32, 50, 190)": "rgba(240, 245, 251, 190)",
    "rgba(13, 20, 32, 190)": "rgba(233, 240, 248, 190)",
    "rgba(30, 46, 68, 240)": "rgba(230, 240, 250, 240)",
    "rgba(22, 34, 52, 240)": "rgba(224, 234, 246, 240)",
    "rgba(20, 30, 48, 230)": "rgba(220, 230, 242, 230)",
    "rgba(18, 27, 42, 235)": "rgba(224, 234, 246, 235)",
    "rgba(9, 14, 22, 248)": "rgba(216, 226, 238, 248)",
    "rgba(9, 14, 22, 240)": "rgba(222, 230, 242, 240)",
    "rgba(13, 20, 34, 250)": "rgba(238, 244, 250, 250)",
    "rgba(13, 20, 34, 252)": "rgba(240, 246, 252, 252)",
    "rgba(13, 20, 34, 248)": "rgba(238, 244, 250, 248)",
    "rgba(10, 16, 26, 220)": "rgba(218, 228, 240, 220)",
    "#0d2333": "#dde9f3",
    "#101622": "#e0e8f2",
    "#161e3c": "#d2ddf2",
    "#141c2a": "#e4ebf4",
    "#3c5a82": "#8fa9c8",
    "#13283d": "#b9c8da",
    "#2f6b9e": "#6fa2d6",
    "#081828": "#93a7bd",
    "#1e3a5f": "#b3c2d6",
    "#22303f": "#c9d4e2",
    "#dcefff": "#16222f",
    "#bfe8ff": "#1d3148",
    "#e6f7ff": "#15202c",
    "#ffffff": "#0b0f14",
    "#001418": "#f2fdff",
    "#8ba3c7": "#5a6f88",
    "#4a5a6e": "#7d8fa4",
    "#ffd9f7": "#9c2a76",
    "#ffd9d9": "#b0352c",
    "#c9ffe9": "#0d7a52",
    "rgb(255, 255, 255)": "rgb(20, 30, 44)",
    "rgb(11, 16, 24)": "rgb(240, 246, 252)",
    "rgb(16, 22, 34)": "rgb(224, 234, 246)",
    "rgb(22, 30, 60)": "rgb(210, 224, 250)",
    "rgb(220, 240, 255)": "rgb(20, 32, 48)",
    "rgb(0, 100, 150)": "rgb(0, 140, 200)",
    "rgb(140, 20, 40)": "rgb(190, 40, 60)",
    "rgb(20, 28, 42)": "rgb(228, 236, 246)",
    "rgb(180, 220, 255)": "rgb(30, 60, 90)",
    "rgb(60, 90, 130)": "rgb(140, 170, 200)",
    "rgb(90, 170, 220)": "rgb(30, 120, 180)",
}


def derive_light(dark_text):
    """Map the canonical dark.qss to the canonical light.qss."""
    text = dark_text
    for dark_tok, light_tok in LIGHT_MAP.items():
        text = text.replace(dark_tok, light_tok)
    text = text.replace(
        "/*  TurBoNC — cyberpunk theme: translucent panels + neon borders             */",
        "/*  TurBoNC — cyberpunk LIGHT theme (frosted light panels + neon edges)     */")
    return text


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _mix(c1, c2, t):
    """Blend rgb tuple c1 toward c2 by factor t (0..1)."""
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def palette(main_hex, pressed_hex, base):
    """Full color set for one accent x base combination.

    The accent's lightness is clamped into a per-base visibility band so all
    accents (cyan, magenta, green, amber, blue, orange) carry the same visual
    weight as the reference neon cyan:

    - dark base: [0.50, 0.70] - keeps accents bright (all current accents
      already sit in the band, so the dark themes are unchanged)
    - light base: [0.20, 0.34] - deepens bright hues (cyan, green, amber,
      orange, ...) that would otherwise wash out on the near-white base
    """
    main = _hex_to_rgb(main_hex)
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in main))
    if base == "dark":
        l = min(max(l, 0.50), 0.70)
    else:
        l = min(max(l, 0.20), 0.34)
    main = tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))
    main_hex = _rgb_to_hex(main)

    def col(sat, light):
        return _rgb_to_hex(tuple(c * 255 for c in colorsys.hls_to_rgb(h, light, sat)))

    if base == "dark":
        bevel_top = col(s * 0.6, min(l + 0.20, 0.75))
        bevel_bottom = col(s * 0.45, max(l - 0.30, 0.05))
        border = col(s * 0.55, max(l - 0.20, 0.08))
        border_line = col(s * 0.65, max(l - 0.10, 0.12))
        # neutral accents (s == 0) get a pure-gray disabled border; colored
        # accents keep the fixed 0.10 saturation as before
        border_disabled = (col(0.10, max(l - 0.22, 0.10)) if s
                           else col(0.0, max(l - 0.22, 0.10)))
        text_tint = col(s * 0.9, min(l + 0.44, 0.92))
    else:
        bevel_top = _rgb_to_hex(_mix(main, (255, 255, 255), 0.55))
        bevel_bottom = _rgb_to_hex(_mix(main, (0, 0, 0), 0.38))
        border = _rgb_to_hex(_mix(main, (0, 0, 0), 0.30))
        border_line = _rgb_to_hex(_mix(main, (0, 0, 0), 0.18))
        border_disabled = _rgb_to_hex(_mix(main, (255, 255, 255), 0.72))
        text_tint = _rgb_to_hex(_mix(main, (0, 0, 0), 0.42))

    return {
        "main": main_hex,
        "light": col(s, min(l + 0.32, 0.78)) if base == "dark" else _rgb_to_hex(_mix(main, (255, 255, 255), 0.35)),
        "mid": col(s, max(l - 0.04, 0.32)),
        "deep": col(s, max(l - 0.26, 0.12)),
        "pressed": pressed_hex,
        "bevel_top": bevel_top,
        "bevel_bottom": bevel_bottom,
        "border": border,
        "border_line": border_line,
        "border_disabled": border_disabled,
        "power": main_hex,
        "text_tint": text_tint,
        "rgb": main,
    }


def recolor_to_accent(base_rgb, accent_rgb):
    """Recolor a base color toward the accent hue.

    Colored accents rotate the base to the accent's hue (same lightness and
    saturation). Monochrome (r == g == b) accents desaturate the base to a
    neutral gray of the same lightness instead: gray has no hue (colorsys
    reports 0), which would otherwise tint every surface red.
    """
    ar, ag, ab = accent_rgb
    if ar == ag == ab:
        _, l, _ = colorsys.rgb_to_hls(*(c / 255 for c in base_rgb))
        v = round(l * 255)
        return (v, v, v)
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in base_rgb))
    ah, _, _ = colorsys.rgb_to_hls(ar / 255, ag / 255, ab / 255)
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(ah, l, s))


def axis_colors(accent_rgb):
    """X/Y/Z camera-gizmo axis colors for an accent.

    Colored accents: the accent plus 120/240 degree hue rotations so the
    three axes stay distinguishable and match the theme. Monochrome accents:
    three distinct neutral grays around the accent lightness so X/Y/Z still
    read apart on black/white themes.
    """
    ar, ag, ab = accent_rgb
    if ar == ag == ab:
        _, l, _ = colorsys.rgb_to_hls(ar / 255, ag / 255, ab / 255)

        def _gray(offset):
            return (round(min(1.0, max(0.0, l + offset)) * 255),) * 3

        return ((ar, ag, ab), _gray(-0.22), _gray(0.22))
    h, l, s = colorsys.rgb_to_hls(ar / 255, ag / 255, ab / 255)

    def _rotated(deg):
        h2 = (h + deg / 360.0) % 1.0
        return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h2, l, s))

    return ((ar, ag, ab), _rotated(120), _rotated(240))


# tokens to replace in the canonical files (cyan theme) per base
TOKENS = {
    "dark": {
        "main": "#00e5ff", "light": "#7df6ff", "mid": "#0091ff",
        "deep": "#0062d6", "pressed": "#ff2bd6",
        "bevel_top": "#2f6b9e", "bevel_bottom": "#081828",
        "border": "#13283d", "border_line": "#1e3a5f",
        "border_disabled": "#22303f", "power": "#2979ff",
        "text_tint": "#bfe8ff",
        "rgba_main": "rgba(0, 229, 255, ",
        "rgba_tint": "rgba(0, 184, 212, ",
    },
    "light": {
        "main": "#00e5ff", "light": "#7df6ff", "mid": "#0091ff",
        "deep": "#0062d6", "pressed": "#ff2bd6",
        "bevel_top": "#6fa2d6", "bevel_bottom": "#93a7bd",
        "border": "#b9c8da", "border_line": "#b3c2d6",
        "border_disabled": "#c9d4e2", "power": "#2979ff",
        "text_tint": "#1d3148",
        "rgba_main": "rgba(0, 229, 255, ",
        "rgba_tint": "rgba(0, 184, 212, ",
    },
}

# surface gradient stops that carry a cyan tint in the canonical files
# (token, alpha). They are recolored to the accent hue for each variant so
# the widget gradients match the theme instead of staying blue.
SURFACE_STOPS = {
    "dark": [
        ("rgba(38, 56, 84, 170)", 170),   # button default top
        ("rgba(20, 32, 50, 170)", 170),   # button default mid / combo
        ("rgba(9, 14, 22, 190)", 190),    # button default bottom / combo
        ("rgba(36, 60, 92, 180)", 180),   # button hover top
        ("rgba(24, 44, 70, 180)", 180),   # button hover mid
        ("rgba(10, 18, 30, 200)", 200),   # button hover bottom
        ("rgba(16, 6, 16, 160)", 160),    # button pressed top
        ("rgba(44, 10, 40, 160)", 160),   # button pressed mid
        ("rgba(70, 16, 58, 180)", 180),   # button pressed bottom
        ("rgba(30, 40, 64, 170)", 170),   # power/home top
        ("rgba(20, 26, 44, 170)", 170),   # power/home mid
        ("rgba(10, 12, 20, 190)", 190),   # power/home bottom
        ("rgba(22, 34, 54, 205)", 205),   # group box top
        ("rgba(14, 22, 36, 205)", 205),   # group box mid
        ("rgba(10, 16, 26, 215)", 215),   # group box bottom
        ("rgba(20, 32, 50, 190)", 190),   # tab top
        ("rgba(13, 20, 32, 190)", 190),   # tab bottom
        ("rgba(30, 46, 68, 240)", 240),   # header top
        ("rgba(12, 18, 30, 240)", 240),   # header bottom
        ("rgba(12, 18, 30, 175)", 175),   # tab pane
        ("rgba(12, 18, 30, 248)", 248),   # tool table bg
    ],
    "light": [
        ("rgba(244, 248, 253, 170)", 170),
        ("rgba(240, 245, 251, 170)", 170),
        ("rgba(214, 224, 236, 190)", 190),
        ("rgba(232, 242, 250, 180)", 180),
        ("rgba(228, 238, 248, 180)", 180),
        ("rgba(205, 216, 230, 200)", 200),
        ("rgba(228, 214, 224, 160)", 160),
        ("rgba(238, 214, 230, 160)", 160),
        ("rgba(244, 222, 236, 180)", 180),
        ("rgba(243, 247, 253, 170)", 170),
        ("rgba(242, 246, 252, 170)", 170),
        ("rgba(216, 222, 232, 190)", 190),
        ("rgba(246, 250, 254, 205)", 205),
        ("rgba(242, 248, 254, 205)", 205),
        ("rgba(222, 230, 240, 215)", 215),
        ("rgba(240, 245, 251, 190)", 190),
        ("rgba(233, 240, 248, 190)", 190),
        ("rgba(230, 240, 250, 240)", 240),
        ("rgba(226, 234, 244, 240)", 240),
        ("rgba(226, 234, 244, 175)", 175),
        ("rgba(232, 239, 248, 248)", 248),
    ],
}


# flat (non-gradient) surface fills that carry a cyan tint in the
# canonical files: (token, alpha). Recolored to the accent hue as well.
FLAT_STOPS = {
    "dark": [
        ("rgba(12, 17, 24, 160)", 160),   # disabled button bg
        ("rgba(8, 13, 21, 235)", 235),    # line edit bg
        ("rgba(8, 13, 21, 245)", 245),    # file system table bg
        ("rgba(7, 12, 19, 245)", 245),    # DRO chips
        ("rgba(7, 11, 18, 220)", 220),    # slider groove / check boxes
        ("rgba(9, 14, 22, 248)", 248),    # menu bar / status bar
        ("rgba(9, 14, 22, 240)", 240),    # combo popup
        ("rgba(13, 20, 34, 250)", 250),   # menus
        ("rgba(13, 20, 34, 252)", 252),   # tooltips
        ("rgba(13, 20, 34, 248)", 248),   # message boxes
        ("rgba(10, 16, 26, 220)", 220),   # header view bg
    ],
    "light": [
        ("rgba(228, 234, 242, 160)", 160),
        ("rgba(238, 244, 250, 235)", 235),
        ("rgba(238, 244, 250, 245)", 245),
        ("rgba(240, 246, 252, 245)", 245),
        ("rgba(226, 234, 244, 220)", 220),
        ("rgba(216, 226, 238, 248)", 248),
        ("rgba(222, 230, 242, 240)", 240),
        ("rgba(238, 244, 250, 250)", 250),
        ("rgba(240, 246, 252, 252)", 252),
        ("rgba(238, 244, 250, 248)", 248),
        ("rgba(218, 228, 240, 220)", 220),
    ],
}

# hex surface colors that carry a cyan tint (window bg, base, disabled text,
# gcode editor chrome)
HEX_STOPS = {
    "dark": ["#0a0e14", "#0d2333", "#4a5a6e",
             "#101622", "#161e3c", "#141c2a", "#3c5a82"],
    "light": ["#eef2f7", "#dde9f3", "#7d8fa4",
              "#e0e8f2", "#d2ddf2", "#e4ebf4", "#8fa9c8"],
}


def _recolor_rgba(token, alpha, hue):
    """Recolor an rgba surface stop to the given hue (same brightness)."""
    r, g, b = (int(c) for c in re.findall(r"\d+", token)[:3])
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nr, ng, nb = (round(c * 255)
                  for c in colorsys.hls_to_rgb(hue, l, s))
    return f"rgba({nr}, {ng}, {nb}, {alpha})"


def _recolor_hex(token, hue):
    """Recolor a hex color to the given hue (same brightness)."""
    r, g, b = _hex_to_rgb(token)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nr, ng, nb = (round(c * 255)
                  for c in colorsys.hls_to_rgb(hue, l, s))
    return _rgb_to_hex((nr, ng, nb))


def substitute(text, accent, base):
    tok = TOKENS[base]
    p = palette(accent[0], accent[1], base)
    keys = ("main", "light", "mid", "deep", "pressed",
            "bevel_top", "bevel_bottom", "border", "border_line",
            "border_disabled", "power", "text_tint")
    # Stage 1: swap every token for a unique sentinel. Chained in-place
    # replacement would collide when one accent's value equals another
    # token (e.g. cyan main == magenta pressed), flipping colors back.
    for i, key in enumerate(keys):
        text = text.replace(tok[key], f"\x00S{i}\x00")
    # Stage 2: sentinels -> this accent's values.
    for i, key in enumerate(keys):
        text = text.replace(f"\x00S{i}\x00", p[key])
    ar, ag, ab = p["rgb"]
    text = text.replace(tok["rgba_main"], f"rgba({ar}, {ag}, {ab}, ")
    text = text.replace(tok["rgba_tint"], f"rgba({ar}, {ag}, {ab}, ")
    if base == "light":
        # The canonical borders are drawn at 140-150 alpha (fine over the
        # near-black dark base, washed out over the near-white light base):
        # raise every rgba stop of the consolidated accent so the light
        # themes keep visible neon edges. All other (surface) alphas are
        # untouched - they are recolored by hue, not by the accent rgb.
        text = re.sub(
            r"rgba\((%d,\s*%d,\s*%d,\s*)(\d+)\)" % (ar, ag, ab),
            lambda m: "rgba(%s%d)" % (m.group(1),
                                       min(255, int(m.group(2)) + 60)),
            text)
    # recolor the surface gradients to the accent hue
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in p["rgb"]))
    for token, alpha in SURFACE_STOPS[base]:
        if token in text:
            text = text.replace(token, _recolor_rgba(token, alpha, h))
    for token, alpha in FLAT_STOPS[base]:
        if token in text:
            text = text.replace(token, _recolor_rgba(token, alpha, h))
    for token in HEX_STOPS[base]:
        if token in text:
            text = text.replace(token, _recolor_hex(token, h))
    return text


# ---------------------------------------------------------------------------
# Monochrome (black/white) themes
#
# The hue-recolor pipeline above cannot produce them: a neutral accent has no
# hue (colorsys reports 0), which would tint every surface red. So the mono
# themes run a dedicated grayscale substitution: the accent palette is a
# neutral gray and every remaining surface / text color is desaturated to
# gray (same lightness). Safety / error colors stay colored so the estop and
# armed lamps keep their real red / green meaning.
# ---------------------------------------------------------------------------

# output name -> (base name, accent main hex, pressed hex)
MONO_THEMES = {
    "dark-white": ("dark", "#f2f2f2", "#555555"),   # light-gray accent on black
    "light-black": ("light", "#1a1a1a", "#666666"),  # near-black accent on white
}

# lamp / error colors preserved in the monochrome themes (dark + light forms)
SAFETY_HEXES = frozenset(("#ff1744", "#00ff9d", "#ffd9d9", "#c9ffe9",
                          "#b0352c", "#0d7a52", "#8c1428"))


def _neutralize_hex(token):
    """Desaturate a hex color to gray, keeping its lightness."""
    r, g, b = _hex_to_rgb(token)
    _, l, _ = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return _rgb_to_hex((round(l * 255),) * 3)


def _neutralize_rgba(token, alpha):
    """Desaturate an rgba surface stop to gray, keeping its lightness."""
    r, g, b = (int(c) for c in re.findall(r"\d+", token)[:3])
    _, l, _ = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    v = round(l * 255)
    return f"rgba({v}, {v}, {v}, {alpha})"


def substitute_mono(text, accent, base):
    """Grayscale variant of substitute(): neutral accent palette and every
    remaining surface / text color desaturated to gray (safety lamps kept)."""
    tok = TOKENS[base]
    p = palette(accent[0], accent[1], base)
    keys = ("main", "light", "mid", "deep", "pressed",
            "bevel_top", "bevel_bottom", "border", "border_line",
            "border_disabled", "power", "text_tint")
    # Stage 1/2: token swap (same sentinel dance as substitute())
    for i, key in enumerate(keys):
        text = text.replace(tok[key], f"\x00S{i}\x00")
    for i, key in enumerate(keys):
        text = text.replace(f"\x00S{i}\x00", p[key])
    ar, ag, ab = p["rgb"]
    text = text.replace(tok["rgba_main"], f"rgba({ar}, {ag}, {ab}, ")
    text = text.replace(tok["rgba_tint"], f"rgba({ar}, {ag}, {ab}, ")
    if base == "light":
        # raise the accent-edge alphas (same as substitute()) so the dark-gray
        # neon edges stay visible over the near-white base
        text = re.sub(
            r"rgba\((%d,\s*%d,\s*%d,\s*)(\d+)\)" % (ar, ag, ab),
            lambda m: "rgba(%s%d)" % (m.group(1),
                                       min(255, int(m.group(2)) + 60)),
            text)
    text = text.replace(
        "/*  TurBoNC — cyberpunk theme: translucent panels + neon borders             */",
        "/*  TurBoNC — monochrome BLACK/WHITE theme (neutral grays, no hues)        */")
    text = text.replace(
        "/*  TurBoNC — cyberpunk LIGHT theme (frosted light panels + neon edges)     */",
        "/*  TurBoNC — monochrome LIGHT theme (black on white, no hues)            */")
    # desaturate every remaining hex (safety colors kept) and rgba fill
    for m in reversed(list(re.finditer(r"#[0-9a-fA-F]{6}", text))):
        if m.group(0).lower() in SAFETY_HEXES:
            continue
        text = text[:m.start()] + _neutralize_hex(m.group(0)) + text[m.end():]
    for m in reversed(list(re.finditer(
            r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", text))):
        text = (text[:m.start()]
                + _neutralize_rgba(m.group(0), int(m.group(4)))
                + text[m.end():])
    return text


def main():
    # derive the canonical light base from the dark base so they stay in sync
    dark_canon = (THEMES / BASES["dark"]).read_text(encoding="utf-8")
    light_derived = derive_light(dark_canon)
    # The canonical light theme is the cyan accent consolidated for the light
    # base (palette() deepens it + the accent alphas are raised), so it reads
    # as well as the dark neon.
    (THEMES / BASES["light"]).write_text(
        substitute(light_derived, ACCENTS["cyan"], "light"),
        encoding="utf-8")

    produced = []
    for base_name, base_file in BASES.items():
        # Variants are generated from the identity-literal text (freshly
        # derived for light: light.qss on disk no longer carries the cyan
        # literals after the consolidation above).
        base_text = (dark_canon if base_name == "dark" else light_derived)
        for accent_name, accent in ACCENTS.items():
            if accent_name == "cyan":
                continue  # the canonical files already are the cyan themes
            out = THEMES / f"{base_name}-{accent_name}.qss"
            out.write_text(substitute(base_text, accent, base_name),
                           encoding="utf-8")
            produced.append(out.name)
    # monochrome black/white themes (grayscale substitution, see above)
    for theme_name, (base_name, accent_hex, pressed_hex) in MONO_THEMES.items():
        base_text = (dark_canon if base_name == "dark" else light_derived)
        out = THEMES / f"{theme_name}.qss"
        out.write_text(substitute_mono(base_text, (accent_hex, pressed_hex),
                                       base_name), encoding="utf-8")
        produced.append(out.name)
    bg_files = write_background_variants()
    print(f"generated {len(produced)} theme variant(s):")
    for name in produced:
        print("  " + name)
    print(f"generated {len(bg_files)} dimmed background(s):")
    for name in bg_files:
        print("  " + name)
    # preview one palette for sanity
    p = palette(*ACCENTS["green"], "dark")
    print("green/dark palette:", {k: v for k, v in p.items() if k != "rgb"})


if __name__ == "__main__":
    main()
