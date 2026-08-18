"""View-style overlays for TurboNC.

View styles change *how* widgets are drawn (borders, corner shapes, fills)
on top of the base theme's colors. Each style is a QSS file under
``themes/styles/`` that uses accent tokens (``@main@``, ``@border@``, ...)
which are substituted at apply time with the palette of the currently
selected theme, so every style works with each dark/light x accent combo.

The palette math is shared with generate_themes.py (same ``palette()``), so
an overlay's colors always match the active theme.
"""

import re

from tnc.generate_themes import _hex_to_rgb, palette as _gen_palette

# Display name -> overlay file (relative to the themes/ dir). "Neon" is the
# default look: the base themes already are neon, so its overlay is empty.
VIEW_STYLE_NAMES = ["Neon", "Flat", "Sharp", "Rounded", "Glow", "Chamfer",
                    "Retro"]

VIEW_STYLE_FILES = {
    "Neon": "styles/neon.qss",
    "Flat": "styles/flat.qss",
    "Sharp": "styles/sharp.qss",
    "Rounded": "styles/rounded.qss",
    "Glow": "styles/glow.qss",
    "Chamfer": "styles/chamfer.qss",
    "Retro": "styles/retro.qss",
}

# Fixed pressed accent, matching generate_themes.py's cyan->magenta pairing.
PRESSED_HEX = "#ff2bd6"

# Background textures selectable from the settings dialog. Files are the
# dimmed copies generated from the originals in themes/backgrounds/ (see
# generate_themes.py write_background_variants). "Plain" restores the
# plain theme background color.
#
# NOTE: the option names must not be Python literal-like strings ("None",
# "True", "1", ...): qtpyvcp interpolates every config string through a
# jinja2 NativeEnvironment, which would turn e.g. "None" into a real null.
BACKGROUND_NAMES = ["Plain", "Brushed Metal", "Wood"]

BACKGROUND_FILES = {
    "Plain": None,
    "Brushed Metal": "backgrounds/brushed-metal-dim.jpg",
    "Wood": "backgrounds/wood-dim.jpg",
}


def tint_color(accent_rgb, dark, custom_hex=None):
    """Effective (r, g, b) for the tint overlay.

    Uses the custom color when given, otherwise a dark tone of the theme
    accent (blended toward black) so the wash darkens the background.
    """
    if custom_hex:
        try:
            return _hex_to_rgb(custom_hex)
        except Exception:
            pass
    factor = 0.45 if dark else 0.35
    return tuple(round(c * factor) for c in accent_rgb)


def tint_overlay(accent_rgb, dark, custom_hex=None):
    """Dark accent wash overlaid on the window background.

    Args:
        accent_rgb: (r, g, b) tuple of the active theme accent.
        dark: True for dark base themes, False for light.
        custom_hex: optional custom tint color (e.g. "#ff00ff"); when None
            a dark tone of the theme accent is used.

    Returns:
        (r, g, b, a): the wash color plus the alpha (default subtle; the
        Tint Strength setting overrides the alpha, up to near-opaque).
    """
    r, g, b = tint_color(accent_rgb, dark, custom_hex)
    a = 40 if dark else 32
    return r, g, b, a


_RGBA_RE = re.compile(
    r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")


def scale_rgba_alphas(qss_text, factor):
    """Multiply every rgba(...) alpha in a stylesheet by factor (0..1).

    Used by the panel-opacity setting: the whole composed sheet is scaled so
    every translucent fill (panels, tab panes, buttons, dialogs) follows the
    chosen opacity. Opaque hex colors are left untouched.
    """
    def _repl(match):
        r, g, b, a = (int(v) for v in match.groups())
        return "rgba(%d, %d, %d, %d)" % (r, g, b, round(a * factor))
    return _RGBA_RE.sub(_repl, qss_text)


def render_view_style(overlay, accent_rgb, dark, extra_tokens=None):
    """Substitute accent tokens in a view-style overlay.

    Args:
        overlay: raw QSS text with @token@ placeholders.
        accent_rgb: (r, g, b) tuple of the active theme accent.
        dark: True for dark base themes, False for light.
        extra_tokens: optional dict of extra @token@ -> value substitutions
            (e.g. the chamfer ring SVG path), applied last.

    Returns:
        The rendered QSS with the active theme's palette baked in.

    Supported tokens:
        @<key>@     palette hex (main, light, mid, deep, pressed,
                     bevel_top, bevel_bottom, border, border_line,
                     border_disabled, power, text_tint)
        @<key>_rgb@ the same colors as "r, g, b" for rgba() usage
        @text@      surface text color (dark/light aware)
        @text_bright@ hover/emphasis text color (dark/light aware)
        @on_accent@ text color for accent-filled surfaces
    """
    ar, ag, ab = accent_rgb
    main_hex = "#%02x%02x%02x" % (ar, ag, ab)
    p = _gen_palette(main_hex, PRESSED_HEX, "dark" if dark else "light")

    if dark:
        overlay = overlay.replace("@text@", "#dcefff")
        overlay = overlay.replace("@text_bright@", "#ffffff")
    else:
        overlay = overlay.replace("@text@", "#16222f")
        overlay = overlay.replace("@text_bright@", "#0b0f14")
    overlay = overlay.replace("@on_accent@", "#001418")

    for key, value in p.items():
        if key == "rgb":
            continue
        overlay = overlay.replace("@%s@" % key, value)
        r, g, b = _hex_to_rgb(value)
        overlay = overlay.replace(
            "@%s_rgb@" % key, "%d, %d, %d" % (r, g, b))
    if extra_tokens:
        for key, value in extra_tokens.items():
            overlay = overlay.replace("@%s@" % key, str(value))
    return overlay
