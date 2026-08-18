"""TNC settings dialog (menu: Settings -> Settings...).

Lets the user pick the UI theme (cyberpunk dark / light variants), the view
style (how buttons, frames and borders are drawn), the background texture
behind the panels, and the "instant win" settings that are also exposed
through the menu bar:

- DRO display units / lathe radius mode
- backplot toggles and default view
- on-screen keyboard
- RDRO server auto-start
- confirm-before-exit behavior

Every control is a qtpyvcp settings widget (``VCPSettingsComboBox`` /
``VCPSettingsCheckBox``) bound to the same persisted settings the menus use
via its ``settingName`` property, so toggling a checkbox here checks the menu
item and vice versa. qtpyvcp calls ``initialize()`` on all such widgets at
startup (dialogs are loaded first), which wires the two-way binding.

The dialog is registered in ``config.yml`` under ``dialogs:``, so qtpyvcp
instantiates it once at startup; ``win.showDialog`` re-shows it from the menu.
"""

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from qtpyvcp.widgets.input_widgets.setting_slider import (VCPSettingsCheckBox,
                                                         VCPSettingsComboBox,
                                                         VCPSettingsSlider)
from qtpyvcp.utilities.settings import getSetting, setSetting
from tnc.view_styles import tint_color
import tnc.main as tnc_main


def _accent():
    """(r, g, b) of the currently applied theme accent."""
    return getattr(tnc_main, "CURRENT_ACCENT", (0, 229, 255))


def _accent_hex():
    return "#%02x%02x%02x" % _accent()


class TncSettingsDialog(QDialog):
    def __init__(self, parent=None, **kwargs):
        super().__init__(parent)
        self.setWindowTitle("TNC Settings")
        # Frameless + translucent, frosted fill painted in paintEvent (same
        # treatment as the zero-xy / home-all dialogs).
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setMinimumWidth(440)
        self._drag_offset = None

        title = QLabel("TNC SETTINGS", self)
        title.setAlignment(Qt.AlignCenter)
        title.setObjectName("settings_title")
        self._title_label = title
        self._refresh_title_color()

        # -- scrollable content (dialog can grow past small screens) ------
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setAutoFillBackground(False)

        content = QWidget()
        content.setAutoFillBackground(False)
        groups = QVBoxLayout(content)
        groups.setContentsMargins(4, 4, 4, 4)
        groups.setSpacing(10)

        # -- Theme ---------------------------------------------------------
        theme_group = QGroupBox("THEME", content)
        theme_form = QFormLayout(theme_group)
        theme_form.setSpacing(8)
        # qtpyvcp fills the combo from the setting's options on initialize.
        self._theme_combo = VCPSettingsComboBox(theme_group)
        self._theme_combo.settingName = "theme.name"
        # Use `activated` (user picks only), not `currentTextChanged`: qtpyvcp
        # populates the combo during initialize() with addItem, which emits
        # currentTextChanged for the first item and would clobber the
        # restored setting with the default on every launch. PySide6 exposes
        # only the activated(int) overload on instances, so resolve the text
        # from the combo itself.
        self._theme_combo.activated[int].connect(
            lambda idx: self._on_theme_changed(
                self._theme_combo.itemText(idx)))
        theme_form.addRow("Theme", self._theme_combo)
        hint = QLabel("Applied immediately and remembered for the next "
                      "launch.", theme_group)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8ba3c7; font-size: 9pt; border: none; "
                           "background: transparent;")
        theme_form.addRow(hint)
        groups.addWidget(theme_group)

        # -- View style -----------------------------------------------------
        style_group = QGroupBox("VIEW STYLE", content)
        style_form = QFormLayout(style_group)
        style_form.setSpacing(8)
        # qtpyvcp fills the combo from the setting's options on initialize.
        self._style_combo = VCPSettingsComboBox(style_group)
        self._style_combo.settingName = "theme.style"
        self._style_combo.activated[int].connect(
            lambda idx: self._on_style_changed(
                self._style_combo.itemText(idx)))
        style_form.addRow("Widget Style", self._style_combo)
        style_hint = QLabel("How buttons, frames and borders are drawn. "
                            "Colors still follow the theme.", style_group)
        style_hint.setWordWrap(True)
        style_hint.setStyleSheet("color: #8ba3c7; font-size: 9pt; border: none; "
                                 "background: transparent;")
        style_form.addRow(style_hint)
        groups.addWidget(style_group)

        # -- Background -----------------------------------------------------
        bg_group = QGroupBox("BACKGROUND", content)
        bg_form = QFormLayout(bg_group)
        bg_form.setSpacing(8)
        # qtpyvcp fills the combo from the setting's options on initialize.
        self._bg_combo = VCPSettingsComboBox(bg_group)
        self._bg_combo.settingName = "theme.background"
        self._bg_combo.activated[int].connect(
            lambda idx: self._on_background_changed(
                self._bg_combo.itemText(idx)))
        bg_form.addRow("Texture", self._bg_combo)
        # accent tint overlays whatever background is selected (texture or
        # plain) with the theme accent color.
        self._bg_tint_check = VCPSettingsCheckBox(bg_group)
        self._bg_tint_check.setText("Tint with Theme Color")
        self._bg_tint_check.settingName = "theme.background-tint"
        self._bg_tint_check.toggled.connect(self._on_background_tint_changed)
        bg_form.addRow(self._bg_tint_check)
        bg_hint = QLabel("Texture behind the UI panels, dimmed so the "
                         "theme stays readable.", bg_group)
        bg_hint.setWordWrap(True)
        bg_hint.setStyleSheet("color: #8ba3c7; font-size: 9pt; border: none; "
                              "background: transparent;")
        bg_form.addRow(bg_hint)
        groups.addWidget(bg_group)

        # -- Opacity --------------------------------------------------------
        opacity_group = QGroupBox("OPACITY", content)
        opacity_form = QFormLayout(opacity_group)
        opacity_form.setSpacing(8)
        # qtpyvcp sets the slider range from the setting's min/max on init.
        tint_slider = VCPSettingsSlider(opacity_group)
        tint_slider.settingName = "theme.background-tint-opacity"
        tint_slider.valueChanged.connect(self._on_opacity_changed)
        opacity_form.addRow("Tint Strength", tint_slider)

        # custom tint color: swatch button opens a color picker; Auto
        # returns to the theme-derived dark accent tone.
        tint_color_row = QHBoxLayout()
        self._tint_color_btn = QPushButton(opacity_group)
        self._tint_color_btn.setObjectName("tint_color_btn")
        self._tint_color_btn.setToolTip("Pick a custom tint color")
        self._tint_color_btn.clicked.connect(self._pick_tint_color)
        tint_auto_btn = QPushButton("Auto", opacity_group)
        tint_auto_btn.setObjectName("tint_color_auto_btn")
        tint_auto_btn.setToolTip("Use the theme accent tone")
        tint_auto_btn.clicked.connect(self._reset_tint_color)
        tint_color_row.addWidget(self._tint_color_btn, 1)
        tint_color_row.addWidget(tint_auto_btn)
        opacity_form.addRow("Tint Color", tint_color_row)
        self._refresh_tint_button(self._effective_tint_color())
        panel_slider = VCPSettingsSlider(opacity_group)
        panel_slider.settingName = "theme.panel-opacity"
        panel_slider.valueChanged.connect(self._on_opacity_changed)
        opacity_form.addRow("Panel Opacity", panel_slider)
        op_hint = QLabel("Tint strength darkens and colors the background "
                         "overlay; panel opacity affects panels, dialogs "
                         "and fills.", opacity_group)
        op_hint.setWordWrap(True)
        op_hint.setStyleSheet("color: #8ba3c7; font-size: 9pt; border: none; "
                              "background: transparent;")
        opacity_form.addRow(op_hint)
        groups.addWidget(opacity_group)

        # -- DRO -----------------------------------------------------------
        dro_group = QGroupBox("DRO", content)
        dro_form = QFormLayout(dro_group)
        dro_form.setSpacing(8)
        units_combo = VCPSettingsComboBox(dro_group)
        units_combo.settingName = "dro.display-units"
        dro_form.addRow("Display Units", units_combo)
        lathe_combo = VCPSettingsComboBox(dro_group)
        lathe_combo.settingName = "dro.lathe-radius-mode"
        dro_form.addRow("Lathe Radius Mode", lathe_combo)
        groups.addWidget(dro_group)

        # -- Backplot ------------------------------------------------------
        bp_group = QGroupBox("BACKPLOT", content)
        bp_layout = QVBoxLayout(bp_group)
        bp_layout.setSpacing(6)
        bp_layout.addWidget(self._make_check("backplot.show-grid",
                                             "Show Grid", bp_group))
        bp_layout.addWidget(self._make_check("backplot.show-machine-bounds",
                                             "Show Machine Bounds", bp_group))
        bp_layout.addWidget(self._make_check("backplot.show-program-bounds",
                                             "Show Program Bounds", bp_group))
        bp_layout.addWidget(self._make_check("backplot.multitool-colors",
                                             "Use Colors for Motion Type",
                                             bp_group))
        bp_layout.addWidget(self._make_check("backplot.perspective-view",
                                             "Perspective View", bp_group))
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("View", bp_group))
        view_combo = VCPSettingsComboBox(bp_group)
        view_combo.settingName = "backplot.view"
        view_row.addWidget(view_combo)
        bp_layout.addLayout(view_row)
        groups.addWidget(bp_group)

        # -- Input ---------------------------------------------------------
        input_group = QGroupBox("INPUT", content)
        input_layout = QVBoxLayout(input_group)
        input_layout.setSpacing(6)
        input_layout.addWidget(self._make_check("virtual-input.enable",
                                                "Show On-screen Keyboard",
                                                input_group))
        groups.addWidget(input_group)

        # -- RDRO ----------------------------------------------------------
        rdro_group = QGroupBox("RDRO SERVER", content)
        rdro_layout = QVBoxLayout(rdro_group)
        rdro_layout.setSpacing(6)
        rdro_layout.addWidget(self._make_check("rdro.start-server-on-launch",
                                               "Start Server on Launch",
                                               rdro_group))
        groups.addWidget(rdro_group)

        # -- Behavior ------------------------------------------------------
        behavior_group = QGroupBox("BEHAVIOR", content)
        behavior_layout = QVBoxLayout(behavior_group)
        behavior_layout.setSpacing(6)
        behavior_layout.addWidget(self._make_check("app.confirm-exit",
                                                   "Confirm Before Exiting",
                                                   behavior_group))
        groups.addWidget(behavior_group)

        scroll.setWidget(content)

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)

    @staticmethod
    def _make_check(setting_name, label, parent):
        """VCPSettingsCheckBox bound to a persisted bool setting."""
        check = VCPSettingsCheckBox(parent)
        check.setText(label)
        check.settingName = setting_name
        return check

    # -- theme handling -----------------------------------------------------

    def _on_theme_changed(self, name):
        win = self._find_window()
        if win is not None and hasattr(win, 'setTheme'):
            win.setTheme(name)
        # The accent may have changed - repaint the glass with the new color.
        self._refresh_title_color()
        self.update()

    def _on_style_changed(self, name):
        win = self._find_window()
        if win is not None and hasattr(win, 'setViewStyle'):
            win.setViewStyle(name)

    def _on_background_changed(self, name):
        win = self._find_window()
        if win is not None and hasattr(win, 'setBackground'):
            win.setBackground(name)

    def _on_background_tint_changed(self, enabled):
        win = self._find_window()
        if win is not None and hasattr(win, 'setBackgroundTint'):
            win.setBackgroundTint(enabled)

    def _on_opacity_changed(self, _value):
        win = self._find_window()
        if win is not None and hasattr(win, 'reapplyStylesheet'):
            win.reapplyStylesheet()

    # -- tint color ---------------------------------------------------------

    def _effective_tint_color(self):
        """Current QColor for the tint (custom or theme-derived)."""
        from PySide6.QtGui import QColor
        setting = getSetting('theme.background-tint-color')
        if setting is not None:
            value = setting.getValue()
            if (isinstance(value, str) and value and value != "theme"
                    and QColor(value).isValid()):
                return QColor(value)
        r, g, b = tint_color(tnc_main.CURRENT_ACCENT, tnc_main.CURRENT_DARK)
        return QColor(r, g, b)

    def _pick_tint_color(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self._effective_tint_color(), self,
                                      "Tint Color")
        if color.isValid():
            try:
                setSetting('theme.background-tint-color', color.name())
            except Exception:
                pass
            self._refresh_tint_button(color)
            self._apply_theme_change()

    def _reset_tint_color(self):
        try:
            setSetting('theme.background-tint-color', "")
        except Exception:
            pass
        self._refresh_tint_button(self._effective_tint_color())
        self._apply_theme_change()

    def _refresh_tint_button(self, color):
        text = "#%02x%02x%02x" % (color.red(), color.green(), color.blue())
        fg = "#ffffff" if color.lightness() < 128 else "#000000"
        self._tint_color_btn.setText(text)
        self._tint_color_btn.setStyleSheet(
            "QPushButton { background-color: %s; color: %s; }" % (text, fg))

    def _apply_theme_change(self):
        win = self._find_window()
        if win is not None and hasattr(win, 'reapplyStylesheet'):
            win.reapplyStylesheet()

    @staticmethod
    def _find_window():
        app = QApplication.instance()
        if app is None:
            return None
        win = app.activeWindow()
        if win is not None and hasattr(win, 'setTheme'):
            return win
        for widget in app.topLevelWidgets():
            if hasattr(widget, 'setTheme'):
                return widget
        return None

    # -- show / paint / drag ------------------------------------------------

    def showEvent(self, event):
        self._refresh_title_color()
        self._refresh_tint_button(self._effective_tint_color())
        super().showEvent(event)

    def _refresh_title_color(self):
        self._title_label.setStyleSheet(
            f"color: {_accent_hex()}; font-size: 14pt; font-weight: 700;"
            "letter-spacing: 2px;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        ar, ag, ab = _accent()
        top, bottom = tnc_main.glass_fill()
        path = tnc_main.glass_path(rect)

        # frosted fill (diagonal gradient)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        # glass sheen across the top
        sheen = QLinearGradient(0, rect.y(), 0, rect.y() + rect.height() * 0.45)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 30))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(sheen)
        painter.drawPath(path)

        # soft neon glow from the top-left corner
        glow = QRadialGradient(
            QPointF(rect.x() + rect.width() * 0.2,
                    rect.y() + rect.height() * 0.2),
            rect.width() * 0.9)
        glow.setColorAt(0.0, QColor(ar, ag, ab, 40))
        glow.setColorAt(1.0, QColor(ar, ag, ab, 0))
        painter.setBrush(glow)
        painter.drawPath(path)

        # neon border
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(ar, ag, ab, 150), 1))
        painter.drawPath(path)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)
