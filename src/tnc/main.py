#!/usr/bin/env python

import os

# Force qtpy to use PySide6
os.environ['QT_API'] = 'pyside6'

from PySide6.QtCore import QFileSystemWatcher, QTimer, Slot
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QAbstractButton, QApplication)

from qtpyvcp.widgets.form_widgets.main_window import VCPMainWindow

# Setup logging
from qtpyvcp.utilities import logger

LOG = logger.getLogger("QtPyVCP." + __name__)

from qtpyvcp import actions

import resources_rc

VCP_DIR = os.path.dirname(os.path.abspath(__file__))

LIGHT_STYLESHEET_FILE = "light.qss"
DARK_STYLESHEET_FILE = "dark.qss"


class MainWindow(VCPMainWindow):
    def __init__(self, *args, **kwargs):
        opts = kwargs.get('opts')
        self._develop_mode = bool(getattr(opts, 'develop', False))
        self._qss_watcher = None
        self._watched_stylesheet_path = None

        super(MainWindow, self).__init__(*args, **kwargs)

        self._connect_theme_tracking()
        self._apply_theme_stylesheet()
        
        self.initUi()

        # self.plot.camera.Zoom(0.002)
        # self.plot.setViewMachine()

    def _is_dark_theme(self):
        app = QApplication.instance()
        if app is None:
            return False
        palette = app.palette()
        window_lightness = palette.color(QPalette.Window).lightness()
        base_lightness = palette.color(QPalette.Base).lightness()
        return ((window_lightness + base_lightness) / 2) < 128

    def _diagnose_gcode_highlighting(self):
        """Ensure GCodeEditor syntax highlighting is properly configured.

        The framework applies YAML config via _apply_gcode_editor_yaml_config,
        but a timing issue can leave properties reset to their .ui file defaults
        on some editors. This runs late as a safety net, reading the same YAML
        config that the framework already loaded into qtpyvcp.CONFIG.
        """
        from PySide6.QtCore import QObject
        from PySide6.QtGui import QColor
        import qtpyvcp

        all_objs = self.findChildren(QObject)
        gcode_editors = [o for o in all_objs
                         if o.metaObject().className() in ('GCodeEditor', 'GcodeEditor')]

        if not gcode_editors:
            LOG.warning("No GCodeEditor widgets found")
            return

        # Read token→color map from YAML (same source as the framework uses)
        syntax_config = qtpyvcp.CONFIG.get('gcode_syntax_profile', {})
        token_color_map = {}
        for style in syntax_config.get('syntax_styles', []):
            color = style.get('color')
            if not color:
                continue
            qcolor = QColor(color)
            if not qcolor.isValid():
                continue
            for token in style.get('tokens', []):
                token_color_map[token] = qcolor

        # Read current-line style from YAML
        style_defaults = qtpyvcp.CONFIG.get('gcode_editor_style_defaults', {})
        cl_enabled = style_defaults.get('current_line_highlight_enabled', True)
        cl_bg = style_defaults.get('current_line_background', '#3a3a5a')
        cl_fg = style_defaults.get('current_line_color', '#d4d4d4')

        for editor in gcode_editors:
            name = editor.objectName()

            if token_color_map and hasattr(editor, 'setTokenColorMap'):
                se_enabled = editor.property('syntaxHighlightingEnabled')
                if not se_enabled:
                    LOG.warning("Editor '%s': syntaxHighlightingEnabled was False, forcing to True", name)
                editor.setTokenColorMap(token_color_map)
                editor.setProperty('syntaxHighlightingEnabled', True)
                LOG.info("Editor '%s': highlighting applied (%d token types)",
                         name, len(token_color_map))

            # Force-enable current-line highlight (same timing issue as above)
            cur_enabled = editor.property('currentLineHighlightEnabled')
            if not cur_enabled:
                LOG.warning("Editor '%s': currentLineHighlightEnabled was False, forcing to True", name)
            editor.setProperty('currentLineHighlightEnabled', bool(cl_enabled))
            editor.setProperty('currentLineBackground', QColor(cl_bg))
            editor.setProperty('currentLineColor', QColor(cl_fg))

    def _connect_theme_tracking(self):
        app = QApplication.instance()
        if app is None:
            return
        palette_changed = getattr(app, 'paletteChanged', None)
        if palette_changed is not None:
            try:
                palette_changed.connect(self._on_palette_changed)
            except Exception:
                LOG.exception("Failed to connect paletteChanged signal")

    def _on_palette_changed(self, *_args):
        QTimer.singleShot(0, self._apply_theme_stylesheet)

    def _apply_theme_stylesheet(self):
        dark_theme = self._is_dark_theme()
        stylesheet_file = DARK_STYLESHEET_FILE if dark_theme else LIGHT_STYLESHEET_FILE
        stylesheet_path = os.path.join(VCP_DIR, stylesheet_file)
        app = QApplication.instance()
        if app is None:
            return
        try:
            with open(stylesheet_path, 'r', encoding='utf-8') as style_file:
                app.setStyleSheet(style_file.read())
            if self._develop_mode:
                self._watch_stylesheet(stylesheet_path)
        except Exception:
            LOG.exception("Failed to load theme stylesheet: %s", stylesheet_path)

    def _watch_stylesheet(self, stylesheet_path):
        if self._qss_watcher is None:
            self._qss_watcher = QFileSystemWatcher(self)
            self._qss_watcher.fileChanged.connect(self._on_stylesheet_file_changed)

        if self._watched_stylesheet_path and self._watched_stylesheet_path != stylesheet_path:
            self._qss_watcher.removePath(self._watched_stylesheet_path)

        if stylesheet_path not in self._qss_watcher.files():
            self._qss_watcher.addPath(stylesheet_path)

        self._watched_stylesheet_path = stylesheet_path

    def _on_stylesheet_file_changed(self, _path):
        # QFileSystemWatcher may drop paths on save/rename, so reload then re-arm.
        QTimer.singleShot(50, self._reload_watched_stylesheet)

    def _reload_watched_stylesheet(self):
        if self._watched_stylesheet_path and os.path.isfile(self._watched_stylesheet_path):
            if self._watched_stylesheet_path not in self._qss_watcher.files():
                self._qss_watcher.addPath(self._watched_stylesheet_path)
        self._apply_theme_stylesheet()


    def initUi(self):
        super().initUi()
        # Diagnostic: verify GCodeEditor YAML config was applied
        QTimer.singleShot(1000, self._diagnose_gcode_highlighting)

    @Slot(QAbstractButton)
    def on_probeTabGroup_buttonClicked(self, button):
        self.probe_tab_widget.setCurrentIndex(button.property('page'))

    @Slot(QAbstractButton)
    def on_sidebarTabGroup_buttonClicked(self, button):
        self.sidebar_widget.setCurrentIndex(button.property('page'))

    # Fwd/Back buttons off the stacked widget
    def on_probe_help_next_released(self):
        lastPage = 5
        currentIndex = self.probe_help_widget.currentIndex()
        if currentIndex == lastPage:
            self.probe_help_widget.setCurrentIndex(0)
        else:
            self.probe_help_widget.setCurrentIndex(currentIndex + 1)

    def on_probe_help_prev_released(self):
        lastPage = 5
        currentIndex = self.probe_help_widget.currentIndex()
        if currentIndex == 0:
            self.probe_help_widget.setCurrentIndex(lastPage)
        else:
            self.probe_help_widget.setCurrentIndex(currentIndex - 1)
            
