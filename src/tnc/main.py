#!/usr/bin/env python

import os
import re
import secrets
import socket

# Force qtpy to use PySide6
os.environ['QT_API'] = 'pyside6'

from PySide6.QtCore import QFileSystemWatcher, QProcess, QTimer, Slot
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QAbstractButton, QApplication, QMessageBox)

from qtpyvcp.widgets.form_widgets.main_window import VCPMainWindow
from qtpyvcp.utilities.settings import getSetting
from qtpyvcp.utilities.qt_safety import safe_qt_callback

# Setup logging
from qtpyvcp.utilities import logger

LOG = logger.getLogger("QtPyVCP." + __name__)

from qtpyvcp import actions

from qtpyvcp.widgets.dialogs import showDialog
from rdro_server.qtpyvcp.rdro_status import RdroStatusIndicator

import resources_rc

VCP_DIR = os.path.dirname(os.path.abspath(__file__))

LIGHT_STYLESHEET_FILE = "light.qss"
DARK_STYLESHEET_FILE = "dark.qss"

# Matches the classic "port in use" bind errors printed by uvicorn / the
# socket layer on Linux, e.g.
#   [Errno 98] error while attempting to bind on address ('0.0.0.0', 8765):
#   address already in use
_RDRO_PORT_IN_USE = re.compile(
    r'address already in use|errno\s*98|addrinuse', re.IGNORECASE)


def _rdro_port_in_use(port, timeout=0.3):
    """True if something is already listening on 127.0.0.1:port.

    On loopback a connect either succeeds or is refused immediately, so this
    returns well within the timeout in practice.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex(('127.0.0.1', port)) == 0
    finally:
        sock.close()


class MainWindow(VCPMainWindow):
    def __init__(self, *args, **kwargs):
        opts = kwargs.get('opts')
        self._develop_mode = bool(getattr(opts, 'develop', False))
        self._qss_watcher = None
        self._watched_stylesheet_path = None

        super(MainWindow, self).__init__(*args, **kwargs)

        # Diagnostic for the File menu RDRO actions: confirms which module
        # was actually loaded (a stale install/cache shows up here).
        LOG.info('tnc.main loaded from %s', __file__)
        LOG.info('RDRO menu methods present: start=%s stop=%s',
                 hasattr(self, 'startRdroServer'),
                 hasattr(self, 'stopRdroServer'))

        # RDRO server state must exist before the auto-start hook runs: the
        # setting's notify() fires immediately (and swallows exceptions).
        self._rdro_proc = None
        self._rdro_stopping = False   # user asked us to stop (vs. crash)
        self._rdro_restart = False    # stop was the first half of a restart
        self._rdro_port_conflict = False  # seen "address already in use"
        self._rdro_kill_timer = QTimer(self)
        self._rdro_kill_timer.setSingleShot(True)
        self._rdro_kill_timer.timeout.connect(self._rdro_force_kill)

        # Status indicator in the window's status bar.
        self._rdro_indicator = RdroStatusIndicator(
            self._rdro_port, self._rdro_running, self)
        self.statusBar().addPermanentWidget(self._rdro_indicator)

        self._setup_rdro_auto_start()

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

    # ------------------------------------------------------------------
    # RDRO server management (remote DRO / control bridge for the phone app)
    # ------------------------------------------------------------------

    def _setup_rdro_auto_start(self):
        """Auto-start the RDRO server on launch when the setting is enabled.

        The launch decision is deferred until the event loop starts, because
        the settings plugin restores persisted values *after* the window is
        created (postGuiInitialisePlugins). Reacting to the default value
        would ignore a previously unchecked toggle.
        """
        setting = getSetting('rdro.start-server-on-launch')
        if setting is None:
            LOG.warning('RDRO setting rdro.start-server-on-launch not found'
                        ' (settings plugin missing from config?)')
            return
        # React to File-menu toggles (and the persisted-value restore).
        setting.notify(
            safe_qt_callback(self, self._rdro_auto_start_changed),
            update=False)
        QTimer.singleShot(0, self._rdro_launch_auto_start)

    def _rdro_launch_auto_start(self):
        setting = getSetting('rdro.start-server-on-launch')
        if setting is not None:
            self._rdro_auto_start_changed(bool(setting.value))

    def _rdro_auto_start_changed(self, value):
        try:
            if value:
                LOG.info('RDRO auto-start enabled')
                self.startRdroServer()
            else:
                LOG.info('RDRO auto-start disabled (server keeps running until'
                         ' stopped)')
        except Exception:  # noqa: BLE001 - never hide startup failures
            LOG.exception('RDRO auto-start failed')

    def _rdro_script(self):
        return os.path.expanduser('~/Dev/CNC/RDRO-APP/server/start.sh')

    def _rdro_port(self):
        return int(os.environ.get('RDRO_PORT', '8765'))

    @Slot()
    def showRdroInfo(self):
        """Open the RDRO server info dialog (File menu entry).

        The dialog is registered in the screen config's ``dialogs:``
        section (provider ``rdro_server.qtpyvcp.rdro_dialog:RdroInfoDialog``);
        it wires itself to this window when shown.
        """
        showDialog("rdro_info")

    def _rdro_notify(self, message, error=False):
        if error:
            LOG.error(message)
            # Defer so the dialog appears once the window is up.
            QTimer.singleShot(500, lambda: self._rdro_show_error(message))
        else:
            LOG.info(message)

    def _rdro_show_error(self, message):
        try:
            self.showModalDialog(
                QMessageBox.critical, self, 'RDRO Server', message)
        except Exception:
            pass

    def _rdro_running(self):
        return (self._rdro_proc is not None
                and self._rdro_proc.state() != QProcess.NotRunning)

    @property
    def rdro_indicator(self):
        """The status indicator, for the RDRO info dialog."""
        return self._rdro_indicator

    @Slot()
    def startRdroServer(self):
        if self._rdro_running():
            self._rdro_notify('RDRO server is already running')
            return

        script = self._rdro_script()
        if not os.path.isfile(script):
            self._rdro_notify('RDRO start script not found: %s' % script,
                              error=True)
            return

        # Refuse to spawn a second server when another process (usually a
        # stale one from a previous session) already holds the port. Shows a
        # red LED + error dialog instead of silently failing.
        port = self._rdro_port()
        if _rdro_port_in_use(port):
            detail = 'Port %s already in use' % port
            self._rdro_indicator.setState('error', detail)
            self._rdro_notify('RDRO server: %s' % detail, error=True)
            return

        proc = QProcess(self)
        proc.setWorkingDirectory(os.path.dirname(script))
        proc.readyReadStandardOutput.connect(self._rdro_read_output)
        proc.readyReadStandardError.connect(self._rdro_read_output)
        proc.finished.connect(self._rdro_finished)
        proc.errorOccurred.connect(self._rdro_error)
        proc.stateChanged.connect(self._rdro_state_changed)
        proc.start(script, [])
        self._rdro_proc = proc
        self._rdro_notify('RDRO server starting...')

    @Slot()
    def stopRdroServer(self):
        if not self._rdro_running():
            self._rdro_notify('RDRO server is not running')
            return
        self._rdro_stopping = True
        pid = self._rdro_proc.processId()
        LOG.info('Stopping RDRO server (pid %s)', pid)
        self._rdro_proc.terminate()
        # Force kill if it doesn't shut down within 3 s.
        self._rdro_kill_timer.start(3000)

    @Slot()
    def renewRdroToken(self):
        """Generate a new API token, persist it, and restart the server.

        The running process keeps the old token in memory until restarted,
        so a running server is stopped and started again once it exits.
        """
        token = secrets.token_hex(24)
        token_file = os.environ.get(
            'RDRO_TOKEN_FILE', os.path.expanduser('~/.config/rdro/token'))
        try:
            os.makedirs(os.path.dirname(token_file), exist_ok=True)
            fd = os.open(token_file,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as fh:
                fh.write(token)
        except OSError as exc:
            self._rdro_notify('RDRO API token: could not write %s (%s)'
                              % (token_file, exc), error=True)
            return

        LOG.info('RDRO API token renewed (persisted in %s)', token_file)
        if os.environ.get('RDRO_TOKEN'):
            # start.sh only falls back to the token file when RDRO_TOKEN is
            # unset, so an explicit env token would win on the next start.
            LOG.warning('RDRO_TOKEN is set in the environment; the server '
                        'will keep using that token on restart')
            self._rdro_notify('RDRO_TOKEN env var overrides the token file')

        if self._rdro_running():
            self._rdro_restart = True
            self.stopRdroServer()
        else:
            self._rdro_notify('RDRO API token renewed - start the server '
                              'to apply it')

    def _rdro_read_output(self):
        if self._rdro_proc is None:
            return
        out = (self._rdro_proc.readAllStandardOutput()
               .data().decode(errors='replace'))
        err = (self._rdro_proc.readAllStandardError()
               .data().decode(errors='replace'))
        for line in out.splitlines():
            LOG.info('[rdro] %s', line)
        for line in err.splitlines():
            LOG.warning('[rdro] %s', line)
            if _RDRO_PORT_IN_USE.search(line):
                self._rdro_port_conflict = True

    def _rdro_error(self, error):
        if error == QProcess.FailedToStart:
            self._rdro_proc = None
            self._rdro_indicator.setState(
                'error', 'RDRO server failed to start')
            self._rdro_notify('RDRO server failed to start', error=True)

    def _rdro_state_changed(self, state):
        if state == QProcess.Running:
            self._rdro_indicator.setState(
                'starting', 'RDRO server running on port %s' %
                self._rdro_port())
        # NotRunning is intentionally NOT handled here: the indicator gets
        # its "off" (normal stop) or "error" (startup failure) state from
        # _rdro_finished / the status poll, so signal ordering between
        # stateChanged and finished cannot leave a stale state behind.

    def _rdro_finished(self, exit_code, _exit_status):
        self._rdro_kill_timer.stop()
        proc, self._rdro_proc = self._rdro_proc, None
        if proc is not None:
            proc.deleteLater()

        # A non-zero exit with no user-initiated stop is a startup failure;
        # the most common cause is the port being taken (bind error visible
        # in the captured output). Report it as a red LED, not a quiet "off".
        normal = (self._rdro_stopping
                  or (exit_code == 0 and not self._rdro_port_conflict))
        if normal:
            LOG.info('RDRO server stopped (exit code %s)', exit_code)
            self._rdro_indicator.setState('off')
        else:
            if self._rdro_port_conflict:
                detail = 'Port %s already in use' % self._rdro_port()
            else:
                detail = 'RDRO server exited unexpectedly (code %s)' \
                    % exit_code
            LOG.warning('RDRO server failed to start: %s', detail)
            self._rdro_indicator.setState('error', detail)
            self._rdro_notify('RDRO server: %s' % detail, error=True)

        self._rdro_stopping = False
        self._rdro_port_conflict = False

        # A token renewal stopped the server; bring it back up with the new
        # token now that the process has exited and released the port.
        if self._rdro_restart:
            self._rdro_restart = False
            LOG.info('Restarting RDRO server after token renewal')
            self.startRdroServer()

    def _rdro_force_kill(self):
        if self._rdro_running():
            LOG.warning('RDRO server did not stop in time, killing it')
            self._rdro_proc.kill()

    def closeEvent(self, event):
        # Stop the RDRO bridge when the screen closes.
        if self._rdro_running():
            self._rdro_stopping = True
            LOG.info('Stopping RDRO server on window close')
            self._rdro_proc.terminate()
            self._rdro_kill_timer.start(3000)
        super(MainWindow, self).closeEvent(event)
            
