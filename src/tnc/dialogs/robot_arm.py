"""Unified robot arm viewer + workspace tuning dialog for TurboNC.

A free (frameless, translucent, draggable) dialog that merges the live robot
arm 3D view with the workspace / jogging controls:

* **Robot arm view** - shows the arm from the active config's
  ``[VTK] MACHINE_PARTS`` yaml + STL models as a live 3D view: each angular
  joint rotates in real time from the machine's joint feedback, so you see
  the arm follow every jog / program move.
* **Jog tabs** - Cartesian (teleop) and per-joint jogging, jog speed slider,
  and machine recovery (home all / override limits / e-stop).

It reuses qtpyvcp's ``MachinePartsASM`` to build the nested arm assembly and
replicates the per-part transform that the VTK backplot applies in
``vtk_backplot.VTKBackPlot.move_part`` (translate to the joint origin, rotate
about the joint axis, translate back). A ``QTimer`` (~25 fps) polls live joint
positions and refreshes the render.

Registered in ``config.yml`` under ``dialogs:`` and launched from the Tools
menu (see ``menubar.yml``).
"""

import copy
import math
import os
from typing import ClassVar

from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

import vtk
import yaml

IN_DESIGNER = os.getenv("DESIGNER", False)

import tnc.main as tnc_main
from tnc.dialogs import dh_analysis


def _accent():
    return getattr(tnc_main, "CURRENT_ACCENT", (0, 229, 255))


def _ink_active():
    return getattr(tnc_main, "CURRENT_STYLE", "Neon") == "Ink"


# Module-level jog speed state shared by the jog callbacks.
_JOG_SPEED = {"max": 4000.0, "pct": 50}


def _current_speed():
    """Current jog speed in mm/min from the slider state."""
    return _JOG_SPEED["max"] * max(1, min(100, _JOG_SPEED["pct"])) / 100.0


def _jog_axis(anum, direction):
    """Jog a cartesian axis (+/-) or stop (0) at the current speed."""
    try:
        from qtpyvcp import actions
        if not direction:
            actions.machine.jog.axis(anum, 0)
            return
        speed = _current_speed() / 60.0
        actions.machine.jog.axis(anum, direction, speed=speed)
    except Exception:
        tnc_main.LOG.exception("Workspace: failed to jog axis %d", anum)


def _jog_joint(jnum, direction):
    """Jog a single joint (+/-) or stop (0). True joint jogging runs in
    joint (free) mode with teleop disabled, so the robot arm moves around its
    own joint axes rather than in Cartesian space - the safe way to reposition
    it within its workspace without inverse-kinematics errors."""
    import linuxcnc
    from qtpyvcp.actions.base_actions import setTaskMode
    cmd = linuxcnc.command()
    try:
        if not direction:
            cmd.jog(linuxcnc.JOG_STOP, True, int(jnum))
            return
        setTaskMode(linuxcnc.MODE_MANUAL)
        cmd.teleop_enable(0)
        cmd.jog(linuxcnc.JOG_CONTINUOUS, True, int(jnum), _current_speed() / 60.0 * direction)
    except Exception:
        tnc_main.LOG.exception("Workspace: failed to jog joint %d", jnum)


def _home_all():
    try:
        from qtpyvcp import actions
        actions.machine.home.all()
    except Exception:
        tnc_main.LOG.exception("Workspace: home all failed")


def _override_limits():
    try:
        from qtpyvcp import actions
        actions.machine.override_limits()
    except Exception:
        tnc_main.LOG.exception("Workspace: override limits failed")


def _toggle_estop():
    try:
        from qtpyvcp import actions
        actions.machine.estop.toggle()
    except Exception:
        tnc_main.LOG.exception("Workspace: estop toggle failed")


def _rewrite_list_if_changed(line, new_values):
    """Rewrite a ``position: [ ... ]`` / ``origin: [ ... ]`` line only if its
    values changed, preserving the original numeric style otherwise."""
    old = _parse_inline_list(line)
    if old is not None and len(old) == len(new_values) \
            and all(_same(a, b) for a, b in zip(old, new_values)):
        return line
    return _fmt_list_line(line, new_values)


def _parse_inline_list(line):
    """Return the float values from a ``key: [a, b, c]`` line, or None."""
    try:
        start = line.index("[")
        end = line.index("]", start)
    except ValueError:
        return None
    body = line[start + 1:end]
    vals = []
    for tok in body.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            vals.append(float(tok))
        except ValueError:
            return None
    return vals


def _same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a == b


def _fmt_list_line(line, values):
    """Replace a ``position: [ ... ]`` / ``origin: [ ... ]`` line's in-bracket
    values while keeping its indentation and the ``key:`` prefix."""
    prefix, _, _rest = line.partition(":")
    body = ", ".join(_num(v) for v in values)
    return prefix + ": [" + body + "]" + ("\n" if line.endswith("\n") else "")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    # Keep an integer-looking value as an int to match the original style.
    if f == int(f):
        return str(int(f))
    return repr(round(f, 6))


def _view_scale(widget):
    """Factor to convert a QWidget logical coordinate to VTK window pixels.

    VTK pick/event coordinates are in physical device pixels, while Qt mouse
    positions are in device-independent (logical) pixels. Under a high-DPI
    display these differ. We prefer the render window's backing store size
    over the widget's logical size; falling back to the device pixel ratio.
    """
    try:
        rwin = widget.GetRenderWindow()
        w, h = rwin.GetSize()
        if w > 0 and h > 0:
            lw = max(float(widget.width()), 1.0)
            return max(float(w) / lw, float(h) / max(float(widget.height()), 1.0))
    except Exception:  # noqa: BLE001 - scale lookup is best-effort
        pass
    try:
        ratio = float(widget.devicePixelRatioF())
        if ratio > 0:
            return ratio
    except Exception:  # noqa: BLE001
        pass
    return 1.0


def _make_origin_marker(wx, wy, wz, color, length=70.0):
    """Build a 3-axis cross marker centered at (wx, wy, wz) showing a part's
    origin / pivot point. Longer arms (+/-length along each local axis) so the
    pivot is easy to see, plus a small center sphere. Returns a vtkAssembly,
    or None if it can't be built."""
    try:
        from vtkmodules.vtkFiltersSources import (vtkLineSource,
                                                  vtkSphereSource)
        from vtkmodules.vtkRenderingCore import (vtkActor, vtkPolyDataMapper)
    except Exception:  # noqa: BLE001 - VTK classes unavailable
        return None

    asm = vtk.vtkAssembly()
    ar, ag, ab = color
    arms = [(0, (1.0, 0.0, 0.0)),   # X red
            (1, (0.0, 1.0, 0.0)),   # Y green
            (2, (0.0, 0.0, 1.0))]   # Z blue
    for axis, rgb in arms:
        src = vtkLineSource()
        p0 = [wx, wy, wz]
        p1 = list(p0)
        p2 = list(p0)
        p1[axis] -= length
        p2[axis] += length
        src.SetPoint1(*p1)
        src.SetPoint2(*p2)
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())
        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*rgb)
        actor.GetProperty().SetLineWidth(3)
        asm.AddPart(actor)

    # a small accent sphere at the exact origin point
    sph = vtkSphereSource()
    sph.SetCenter(wx, wy, wz)
    sph.SetRadius(5.0)
    sph.SetThetaResolution(12)
    sph.SetPhiResolution(12)
    mapper = vtkPolyDataMapper()
    mapper.SetInputConnection(sph.GetOutputPort())
    dot = vtkActor()
    dot.SetMapper(mapper)
    dot.GetProperty().SetColor(
        max(ar / 255.0, 0.2), max(ag / 255.0, 0.2), max(ab / 255.0, 0.2))
    dot.GetProperty().SetSpecular(0.4)
    dot.GetProperty().SetSpecularPower(20)
    asm.AddPart(dot)
    return asm


def _resolve_machine_parts():
    """Locate and load the ``[VTK] MACHINE_PARTS`` yaml for the running INI.

    Returns ``(yaml_path, data, cfg_dir)``. ``data`` is the raw loaded yaml
    with its original (relative) ``model:`` paths preserved so it can be
    written back unchanged on save; ``cfg_dir`` is the *config directory*
    (dirname of the INI) that the relative ``model:`` paths are resolved
    against when the arm is built (see ``RobotArmDialog._build_asm``).
    Returns ``(None, None, None)`` if the INI has no MACHINE_PARTS or the
    file can't be read.
    """
    import linuxcnc

    inifile = linuxcnc.ini(os.getenv("INI_FILE_NAME", ""))
    raw = (inifile.find("VTK", "MACHINE_PARTS") or "").strip()
    if not raw:
        return None, None, None

    cfg_dir = os.path.dirname(os.getenv("INI_FILE_NAME", "") or "")
    if not os.path.isabs(raw):
        raw = os.path.normpath(os.path.join(cfg_dir, raw))

    if not os.path.isfile(raw):
        return None, None, None

    with open(raw, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    return raw, data, cfg_dir


def _rad2deg(v):
    import math
    return float(v) * 180.0 / math.pi


def _deg2rad(v):
    import math
    return float(v) * math.pi / 180.0


def _rewrite_hal_setp(text, values):
    """Rewrite ``setp genserkins.<KIND>-<n> <value>`` lines with new values,
    preserving the rest of the file. ``values`` maps e.g. ``ALPHA-0`` to the
    new scalar value. ``ALPHA`` is stored in radians (as in the .hal)."""
    import re

    def repl(m):
        key = "%s-%s" % (m.group(1), m.group(2))
        if key not in values:
            return m.group(0)
        v = values[key]
        if isinstance(v, int) or float(v).is_integer():
            return "setp genserkins.%s %d" % (key, int(v))
        s = str(round(float(v), 9))
        return "setp genserkins.%s %s" % (key, s)

    return re.sub(r"setp\s+genserkins\.([A-Za-z]+)-(\d+)\s+[^\s#]+",
                  repl, text)


# Fallback DH (alpha rad, a mm, d mm) per joint, matching the shipped
# ``robot_arm-kinematics.hal`` (STL-derived: d-0 186, a-2 224, d-3 230,
# d-5 76). Used when neither HAL nor the file is readable.
_PI2 = math.pi / 2.0
_DH_DEFAULTS = (
    (0.0,     0.0, 186.0),
    (-_PI2,   0.0,   0.0),
    (0.0,   224.0,   0.0),
    (-_PI2,   0.0, 230.0),
    (_PI2,    0.0,   0.0),
    (-_PI2,   0.0,  76.0),
)


def _dh_default_params():
    """Default DH (alpha, a, d) per joint in radians/mm."""
    return list(_DH_DEFAULTS)


def _dh_from_values(values):
    """Build the (alpha, a, d) per-joint list from a dict keyed like the
    kinematics hal (``ALPHA-0``, ``A-2``, ``D-5``, values in radians/mm)."""
    out = []
    for j, (al, a, d) in enumerate(_DH_DEFAULTS):
        out.append((float(values.get("ALPHA-%d" % j, al)),
                    float(values.get("A-%d" % j, a)),
                    float(values.get("D-%d" % j, d))))
    return out


def _np_to_vtk(mat):
    """Build a :class:`vtk.vtkMatrix4x4` from a 4x4 numpy array (row-major)."""
    import numpy as _np

    m = vtk.vtkMatrix4x4()
    if isinstance(mat, _np.ndarray):
        m.DeepCopy(mat.flatten().tolist())
    else:
        m.DeepCopy(list(mat))
    return m


_STL_CENTER_CACHE = {}


def _stl_center_abs(path):
    """Return the (cx, cy, cz) geometry centre (in the STL's own frame) of the
    mesh at ``path``, or ``(0, 0, 0)`` if it can't be read. Cached so the live
    renderer only pays for the read once per file."""
    key = os.path.abspath(path)
    if key in _STL_CENTER_CACHE:
        return _STL_CENTER_CACHE[key]
    try:
        from vtkmodules.vtkIOGeometry import vtkSTLReader
        reader = vtkSTLReader()
        reader.SetFileName(key)
        reader.Update()
        b = reader.GetOutput().GetBounds()
        center = ((b[0] + b[1]) * 0.5, (b[2] + b[3]) * 0.5, (b[4] + b[5]) * 0.5)
    except Exception:  # noqa: BLE001 - best-effort
        center = (0.0, 0.0, 0.0)
    _STL_CENTER_CACHE[key] = center
    return center


def _dh_link(alpha, a, d, theta):
    """Return the 4x4 rigid transform for one DH link, replicating
    ``go_dh_pose_convert`` in linuxcnc (gomath.c). Matrices are returned as a
    flat 16-element list suitable for setting a vtkMatrix4x4 (row-major)."""
    import math
    sth = math.sin(theta)
    cth = math.cos(theta)
    sal = math.sin(alpha)
    cal = math.cos(alpha)
    # row-major 4x4:
    #  [ cth, -sth, 0, a ]
    #  [ sth*cal, cth*cal, -sal, -sal*d ]
    #  [ sth*sal, cth*sal,  cal,  cal*d ]
    #  [ 0, 0, 0, 1 ]
    return [
        cth,        -sth,      0.0,     a,
        sth * cal,  cth * cal, -sal,   -sal * d,
        sth * sal,  cth * sal,  cal,    cal * d,
        0.0,        0.0,        0.0,    1.0,
    ]


def _euler_xyz_matrix(rx_deg, ry_deg, rz_deg):
    """4x4 rotation matrix (nested lists) for XYZ Tait-Bryan euler angles in
    degrees: R = Rz * Ry * Rx."""
    import math
    ax, ay, az = (math.radians(v) for v in (rx_deg, ry_deg, rz_deg))
    sx, cx = math.sin(ax), math.cos(ax)
    sy, cy = math.sin(ay), math.cos(ay)
    sz, cz = math.sin(az), math.cos(az)
    return [
        [cy*cz,              sx*sy*cz - cx*sz,  cx*sy*cz + sx*sz,  0.0],
        [cy*sz,              sx*sy*sz + cx*cz,  cx*sy*sz - sx*cz,  0.0],
        [-sy,                sx*cy,            cx*cy,             0.0],
        [0.0,                0.0,              0.0,               1.0],
    ]


def _mount_matrix(mount):
    """Build the 4x4 pose of a joint frame relative to its parent from a
    ``[tx, ty, tz, rx, ry, rz]`` mount (mm/deg, XYZ euler)."""
    T = _euler_xyz_matrix(mount[3], mount[4], mount[5])
    T[0][3] = mount[0]
    T[1][3] = mount[1]
    T[2][3] = mount[2]
    return T


def _frame_to_dh(rel):
    """Extract one modified-DH link (alpha deg, a mm, d mm) from the relative
    transform ``rel = F_{i-1}^{-1} * F_i`` of a DH-aligned frame chain at home.

    Matches ``_dh_link(alpha, a, d, 0)``: alpha about X, a along X, d along Z."""
    import math
    alpha = math.degrees(math.atan2(rel[2][1], rel[2][2]))
    sal = math.sin(math.radians(alpha))
    cal = math.cos(math.radians(alpha))
    a = rel[0][3]
    if abs(cal) > 1e-9:
        d = rel[2][3] / cal
    elif abs(sal) > 1e-9:
        d = -rel[1][3] / sal
    else:
        d = 0.0
    return alpha, a, d


class RobotWorkspaceDialog(QDialog):
    """Frameless translucent dialog unifying the live robot arm 3D view with
    the workspace / jogging controls.

    The VTK scene is built lazily on the first show (not at construction),
    because qtpyvcp constructs dialogs eagerly at startup when it registers
    them in ``loadDialogs`` - we don't want to spin up a whole VTK render
    window (and hit any MachineParts/linuxcnc setup) just by launching the
    app.
    """

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent)
        self.setWindowTitle("Robot Arm / Workspace")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setMinimumSize(640, 560)
        self.resize(1000, 760)
        self._drag_offset = None
        self._resize_offset = None   # set during bottom-right resize drag
        self._resize_grip = 20       # corner hit area for manual resize
        self._built = False
        self._timer = None
        self._joint_positions = None
        self._parts3d = []
        self._part_defs = []          # [{node, asm, id, type, axis, joint}]
        self._asm_to_def = {}         # id(asm) -> def
        self._actor_to_def = {}       # id(actor) -> def
        self._markers = []            # origin marker actors (vtkActor)
        self._tool_actor = None       # tool/spindle actor at the arm tip
        self._selected = None         # current def or None
        self._root_asm = None
        self._machine_parts_file = None
        self._machine_parts_data = None
        self._config_dir = None
        self._picker = None
        self._freeze_dh = False   # True = keep manual part positions (no DH override)
        self._loading_props = False  # guard so populating fields doesn't mark edits
        self._report = None          # last dh_analysis.Report

        title = QLabel("ROBOT ARM · WORKSPACE", self)
        title.setAlignment(Qt.AlignCenter)
        color = "#1a1a1a" if _ink_active() else "#%02x%02x%02x" % _accent()
        title.setStyleSheet(
            "color: %s; font-size: 16pt; font-weight: 700;"
            "letter-spacing: 2px;" % color)

        # Placeholder that is replaced by the VTK view on first show.
        self._view = QLabel("loading…", self)
        self._view.setAlignment(Qt.AlignCenter)
        self._view.setStyleSheet(
            "color: %s; background: %s; border-radius: 8px;" % (
                "#888" if _ink_active() else "#%02x%02x%02x" % _accent(),
                "#1a1a1a" if _ink_active() else "#0b1117"))

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.close_method)
        close_btn.setStyleSheet("font-size: 11pt;")
        status = QLabel("standby", self)
        status.setObjectName("robot_arm_status")
        status.setStyleSheet(
            "color: %s; font-size: 11pt;" % ("#555" if _ink_active()
                                            else "#%02x%02x%02x" % _accent()))
        self._status = status

        # ---- unified bottom panel: Jog / Part / DH tabs -----------------
        self._edit_panel = self._build_edit_panel()
        self._props_panel = self._build_props_panel()
        self._dh_panel = self._build_dh_panel()

        self._tabs = QTabWidget(self)
        from PySide6.QtGui import QFont
        _tabfont = QFont()
        _tabfont.setPointSize(11)
        self._tabs.setFont(_tabfont)
        self._build_jog_tab()
        part_tab = QWidget(self)
        part_tab.setAutoFillBackground(False)
        part_tab.setFont(_tabfont)
        pl = QVBoxLayout(part_tab)
        pl.setContentsMargins(0, 6, 0, 0)
        pl.setSpacing(8)
        # Wrap the (tall) Part panels in a scroll area, matching the Jog tab,
        # so the fields and buttons keep their natural size/style instead of
        # being squished into the capped tab height.
        part_scroll = QScrollArea(part_tab)
        part_scroll.setWidgetResizable(True)
        part_scroll.setFrameShape(QFrame.NoFrame)
        part_scroll.viewport().setAutoFillBackground(False)
        part_content = QWidget()
        part_content.setAutoFillBackground(False)
        pcl = QVBoxLayout(part_content)
        pcl.setContentsMargins(4, 4, 4, 4)
        pcl.setSpacing(10)
        pcl.addWidget(self._edit_panel)
        pcl.addWidget(self._props_panel)
        pcl.addStretch(1)
        part_scroll.setWidget(part_content)
        pl.addWidget(part_scroll)
        dh_tab = QWidget(self)
        dh_tab.setAutoFillBackground(False)
        dh_tab.setFont(_tabfont)
        # Jog tab also inherits the tab font via self._tabs, but force it too
        self._jog_tab.setFont(_tabfont)
        dl = QVBoxLayout(dh_tab)
        dl.setContentsMargins(0, 6, 0, 0)
        dl.addWidget(self._dh_panel)
        self._dh_tab = dh_tab
        analyze_tab = self._build_analyze_tab()
        analyze_tab.setFont(_tabfont)
        self._tabs.addTab(self._jog_tab, "Jog")
        self._tabs.addTab(part_tab, "Part")
        self._tabs.addTab(dh_tab, "DH")
        self._tabs.addTab(analyze_tab, "Analyze")
        # The Analyze tab holds tables, so give the panel more room when it is
        # selected and shrink back for the compact Jog / Part / DH tabs.
        self._tabs.currentChanged.connect(self._on_tab_changed)

        load_btn = QPushButton("Load…", self)
        load_btn.clicked.connect(self._on_load)
        load_btn.setStyleSheet("font-size: 11pt;")
        self._load_btn = load_btn
        save_btn = QPushButton("Save…", self)
        save_btn.clicked.connect(self._on_save)
        save_btn.setEnabled(False)
        save_btn.setStyleSheet("font-size: 11pt;")
        self._save_btn = save_btn

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self._view, 1)
        layout.addWidget(self._tabs)
        b = QHBoxLayout()
        b.addWidget(status, 1)
        b.addStretch(1)
        b.addWidget(load_btn)
        b.addWidget(save_btn)
        b.addWidget(close_btn)
        layout.addLayout(b)

        # Uniform font size for all controls inside the bottom panel (Part /
        # DH / Jog tabs), regardless of the app theme defaults.
        self._apply_uniform_font()

    def _apply_uniform_font(self):
        """Force one font size across the dialog's controls so the Part / DH /
        Jog tabs look consistent (labels, fields, buttons all the same size)."""
        qss = ('QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox, QPushButton, '
               'QCheckBox, QSlider, QLabel { font-size: 11pt; }')
        for w in (self._tabs,):
            try:
                w.setStyleSheet(qss)
            except Exception:  # noqa: BLE001 - styling is best-effort
                pass

    def _build_jog_tab(self):
        """Build the workspace / jogging tab (jog speed, cartesian + joint jog,
        machine recovery). Wrapped in a scroll area so 9-joint robots fit."""
        from PySide6.QtCore import Qt
        from qtpyvcp import actions
        from qtpyvcp.utilities.info import Info as _Info

        tab = QWidget(self)
        tab.setAutoFillBackground(False)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(8)

        scroll = QScrollArea(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setAutoFillBackground(False)

        content = QWidget()
        content.setAutoFillBackground(False)
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        # ---- Jog speed ---------------------------------------------------
        speed_group = QGroupBox("JOG SPEED", tab)
        speed_group.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 11pt; color: %s; border: 0; }"
            % ("#000" if _ink_active() else "#%02x%02x%02x" % _accent()))
        speed_layout = QHBoxLayout(speed_group)
        speed_layout.setContentsMargins(8, 4, 8, 4)
        self._jog_speed_slider = QSlider(Qt.Horizontal, speed_group)
        self._jog_speed_slider.setRange(1, 100)
        self._jog_speed_label = QLabel("--", speed_group)
        self._jog_speed_label.setMinimumWidth(80)
        self._jog_speed_label.setStyleSheet("font-size: 11pt;")
        self._jog_speed_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _sp = QLabel("Speed %", speed_group)
        _sp.setStyleSheet("font-size: 11pt;")
        speed_layout.addWidget(_sp)
        speed_layout.addWidget(self._jog_speed_slider, 1)
        speed_layout.addWidget(self._jog_speed_label)
        root.addWidget(speed_group)

        _JOG_SPEED["max"] = float(
            getattr(actions.machine.jog, 'max_linear_speed', 4000.0) or 4000.0)
        self._jog_speed_slider.valueChanged.connect(self._on_jog_speed)
        self._jog_speed_slider.setValue(_JOG_SPEED["pct"])

        # ---- Cartesian jog (teleop) --------------------------------------
        cart_group = QGroupBox("CARTESIAN JOG", tab)
        cart_group.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 11pt; color: %s; border: 0; }"
            % ("#000" if _ink_active() else "#%02x%02x%02x" % _accent()))
        cart_grid = QGridLayout(cart_group)
        cart_grid.setContentsMargins(8, 4, 8, 4)
        for row, (letter, anum) in enumerate(
                [("X", 0), ("Y", 1), ("Z", 2), ("A", 3), ("B", 4), ("C", 5)]):
            minus = QPushButton("-", cart_group)
            plus = QPushButton("+", cart_group)
            minus.setObjectName("workspace_minus_btn")
            plus.setObjectName("workspace_plus_btn")
            minus.setStyleSheet("font-size: 11pt;")
            plus.setStyleSheet("font-size: 11pt;")
            lbl = QLabel(letter, cart_group)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size: 11pt; font-weight: 700;")
            minus.pressed.connect(lambda a=anum: _jog_axis(a, -1))
            minus.released.connect(lambda a=anum: _jog_axis(a, 0))
            plus.pressed.connect(lambda a=anum: _jog_axis(a, 1))
            plus.released.connect(lambda a=anum: _jog_axis(a, 0))
            cart_grid.addWidget(lbl, row, 0)
            cart_grid.addWidget(minus, row, 1)
            cart_grid.addWidget(plus, row, 2)
        root.addWidget(cart_group)

        # ---- Joint jog (joint mode) --------------------------------------
        joint_group = QGroupBox("JOINT JOG", tab)
        joint_group.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 11pt; color: %s; border: 0; }"
            % ("#000" if _ink_active() else "#%02x%02x%02x" % _accent()))
        joint_layout = QVBoxLayout(joint_group)
        joint_layout.setContentsMargins(8, 4, 8, 4)
        joint_grid = QGridLayout()
        joint_count = 6
        try:
            joint_count = int(_Info().getNumberJoints() or 6)
        except Exception:
            joint_count = 6
        joint_count = max(1, min(9, joint_count))
        for j in range(joint_count):
            minus = QPushButton("-", joint_group)
            plus = QPushButton("+", joint_group)
            minus.setObjectName("workspace_minus_btn")
            plus.setObjectName("workspace_plus_btn")
            minus.setStyleSheet("font-size: 11pt;")
            plus.setStyleSheet("font-size: 11pt;")
            lbl = QLabel("J%d" % j, joint_group)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size: 11pt; font-weight: 700;")
            minus.pressed.connect(lambda jn=j: _jog_joint(jn, -1))
            minus.released.connect(lambda jn=j: _jog_joint(jn, 0))
            plus.pressed.connect(lambda jn=j: _jog_joint(jn, 1))
            plus.released.connect(lambda jn=j: _jog_joint(jn, 0))
            joint_grid.addWidget(lbl, j, 0)
            joint_grid.addWidget(minus, j, 1)
            joint_grid.addWidget(plus, j, 2)
        joint_layout.addLayout(joint_grid)
        root.addWidget(joint_group)

        # ---- Recovery ---------------------------------------------------
        rec_group = QGroupBox("RECOVERY", tab)
        rec_group.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 11pt; color: %s; border: 0; }"
            % ("#000" if _ink_active() else "#%02x%02x%02x" % _accent()))
        rec_layout = QHBoxLayout(rec_group)
        rec_layout.setContentsMargins(8, 4, 8, 4)
        home_btn = QPushButton("Home All", rec_group)
        home_btn.setObjectName("workspace_recovery_btn")
        home_btn.setStyleSheet("font-size: 11pt;")
        home_btn.clicked.connect(_home_all)
        ovr_btn = QPushButton("Override Limits", rec_group)
        ovr_btn.setObjectName("workspace_recovery_btn")
        ovr_btn.setStyleSheet("font-size: 11pt;")
        ovr_btn.clicked.connect(_override_limits)
        estop_btn = QPushButton("E-Stop", rec_group)
        estop_btn.setObjectName("workspace_recovery_btn")
        estop_btn.setStyleSheet("font-size: 11pt;")
        estop_btn.clicked.connect(_toggle_estop)
        rec_layout.addWidget(home_btn, 1)
        rec_layout.addWidget(ovr_btn, 1)
        rec_layout.addWidget(estop_btn, 1)
        root.addWidget(rec_group)

        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._jog_tab = tab

        # Cap the tab panel's height so the 3D view still dominates; the Jog
        # tab scrolls internally when there isn't room (e.g. 9-joint robots).
        self._tabs.setMaximumHeight(320)
        self._tabs.setMinimumHeight(180)

    def _on_tab_changed(self, index):
        """Give the Analyze tab more vertical room than the compact tabs."""
        try:
            analyzing = self._tabs.widget(index) is self._analyze_tab
        except (AttributeError, RuntimeError):
            return
        self._tabs.setMaximumHeight(560 if analyzing else 320)

    def _on_jog_speed(self, value):
        _JOG_SPEED["pct"] = value
        self._jog_speed_label.setText("%d mm/min" % _current_speed())
        try:
            from qtpyvcp.utilities.settings import setSetting
            setSetting('machine.jog.linear-speed', round(_current_speed(), 2))
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        # Rebuild the VTK view on every show: reusing a QVTKRenderWindowInteractor
        # across hide/show cycles leaves a black (context-lost) render window.
        self._build_view_widget()

    def _build_view_widget(self):
        """Tear down any previous view and build a fresh one, then start live
        updates."""
        try:
            self._teardown_view()
            index = self.layout().indexOf(self._view)
            self.layout().removeWidget(self._view)
            self._view.deleteLater()
            view = self._build_view()
            self._view = view
            self.layout().insertWidget(index, view, 1)
            self._interactor.Initialize()
            self._init_arm()
            self._setup_picking()
            self._built = True
        except Exception as exc:  # noqa: BLE001 - surface any loading problem
            import traceback
            tnc_main.LOG.exception("RobotArm: failed to build view")
            self._status.setText("error: %s" % exc)
            traceback.print_exc()

    def _teardown_view(self):
        """Release the previous VTK view and its render window/context so a
        fresh one can be created on the next show."""
        if self._timer is not None:
            self._timer.stop()
        try:
            if getattr(self, '_renderer_window', None) is not None:
                self._renderer_window.Finalize()
        except Exception:  # noqa: BLE001
            pass
        self._picker = None
        self._renderer = None
        self._renderer_window = None
        self._interactor = None
        self._root_asm = None
        self._joint_positions = None
        self._markers = []
        self._tool_actor = None

    # ------------------------------------------------------------------
    # Position edit panel + picking
    # ------------------------------------------------------------------
    def _build_edit_panel(self):
        from PySide6.QtCore import Qt

        box = QGroupBox("SELECTED PART", self)
        box.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 11pt; color: %s; border: 0; }"
            "QDoubleSpinBox { font-size: 11pt; }"
            "QPushButton { font-size: 11pt; }"
            % ("#000" if _ink_active() else "#%02x%02x%02x" % _accent()))
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 4, 8, 4)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        self._part_label = QLabel("click a part in the view…", box)
        self._part_label.setStyleSheet(
            "color: %s; font-size: 11pt; font-weight: 600;"
            % ("#444" if _ink_active() else "#%02x%02x%02x" % _accent()))
        grid.addWidget(self._part_label, 0, 0, 1, 6)

        # Secondary read-only info line: model + STL geometry centre.
        self._part_info = QLabel("", box)
        self._part_info.setStyleSheet(
            "color: %s; font-size: 11pt;"
            % ("#666" if _ink_active() else "#%02x%02x%02x" % _accent()))
        grid.addWidget(self._part_info, 0, 6, 1, 6)

        # position translate (PX/PY/PZ), position rotation (RX/RY/RZ), origin (OX/OY/OZ)
        self._spin = {}
        cols = [("PX", -20000.0, 20000.0, 0.1),
                ("PY", -20000.0, 20000.0, 0.1),
                ("PZ", -20000.0, 20000.0, 0.1),
                ("RX", -720.0, 720.0, 1.0),
                ("RY", -720.0, 720.0, 1.0),
                ("RZ", -720.0, 720.0, 1.0),
                ("OX", -20000.0, 20000.0, 0.1),
                ("OY", -20000.0, 20000.0, 0.1),
                ("OZ", -20000.0, 20000.0, 0.1)]
        for i, (name, lo, hi, step) in enumerate(cols):
            lbl = QLabel(name, box)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lbl.setStyleSheet(
                "color: %s; font-size: 11pt; padding: 0;"
                % ("#555" if _ink_active() else "#%02x%02x%02x" % _accent()))
            sp = QDoubleSpinBox(box)
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(3)
            sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
            sp.setEnabled(False)
            r, c = divmod(i, 3)
            grid.addWidget(lbl, r + 1, c * 2)
            grid.addWidget(sp, r + 1, c * 2 + 1)
            self._spin[name] = sp

        self._apply_btn = QPushButton("Apply", box)
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet("font-size: 11pt;")
        self._apply_btn.clicked.connect(self._on_apply_position)
        self._reset_btn = QPushButton("Reset", box)
        self._reset_btn.setEnabled(False)
        self._reset_btn.setStyleSheet("font-size: 11pt;")
        self._reset_btn.clicked.connect(self._on_reset_position)
        hint = QLabel("Left-click a part to select it; Apply previews.", box)
        hint.setStyleSheet("color: %s; font-size: 11pt;"
                           % ("#777" if _ink_active() else
                              "#%02x%02x%02x" % _accent()))
        grid.addWidget(self._apply_btn, 4, 0, 1, 1)
        grid.addWidget(self._reset_btn, 4, 1, 1, 1)
        grid.addWidget(hint, 4, 2, 1, 4)

        # Toggle between DH-driven animation and manual (frozen) positions. In
        # DH mode the arm follows the live kinematics every tick; in manual mode
        # the assembly keeps the positions you Applied (so the preview stays).
        from PySide6.QtWidgets import QCheckBox
        self._dh_toggle = QCheckBox("DH live", box)
        self._dh_toggle.setChecked(True)
        self._dh_toggle.setStyleSheet(
            "QCheckBox { color: %s; font-size: 11pt; }"
            % ("#555" if _ink_active() else "#%02x%02x%02x" % _accent()))
        self._dh_toggle.toggled.connect(self._on_dh_toggle)
        grid.addWidget(self._dh_toggle, 5, 0, 1, 2)
        return box

    def _build_props_panel(self):
        """Build an editor for the remaining ``robot_arm.yml`` part fields
        (id, type, axis, joint, power, color, model). ``position``/``origin``
        live in the SELECTED PART box above; the rest are here."""
        from PySide6.QtCore import Qt

        box = QGroupBox("PART PROPERTIES", self)
        box.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 11pt; color: %s; border: 0; }"
            "QDoubleSpinBox { font-size: 11pt; }"
            "QSpinBox { font-size: 11pt; }"
            "QLineEdit { font-size: 11pt; }"
            "QComboBox { font-size: 11pt; }"
            "QPushButton { font-size: 11pt; }"
            % ("#000" if _ink_active() else "#%02x%02x%02x" % _accent()))
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 4, 8, 4)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        def lbl(text, *_args):
            l = QLabel(text, box)
            l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            l.setStyleSheet(
                "color: %s; font-size: 11pt; font-weight: 600;"
                % ("#555" if _ink_active()
                   else "#%02x%02x%02x" % _accent()))
            return l

        # id
        grid.addWidget(lbl("id"), 0, 0)
        self._p_id = QLineEdit(box)
        self._p_id.setEnabled(False)
        grid.addWidget(self._p_id, 0, 1, 1, 3)

        # type
        grid.addWidget(lbl("type"), 1, 0)
        self._p_type = QComboBox(box)
        self._p_type.addItems(["table", "static", "angular"])
        self._p_type.setEnabled(False)
        grid.addWidget(self._p_type, 1, 1, 1, 3)

        # axis
        grid.addWidget(lbl("axis"), 2, 0)
        self._p_axis = QComboBox(box)
        self._p_axis.addItems(["(none)", "x", "y", "z", "-x", "-y", "-z"])
        self._p_axis.setEnabled(False)
        grid.addWidget(self._p_axis, 2, 1)

        # joint
        grid.addWidget(lbl("joint"), 2, 2)
        self._p_joint = QSpinBox(box)
        self._p_joint.setRange(-1, 8)
        self._p_joint.setSpecialValueText("(none)")
        self._p_joint.setValue(-1)
        self._p_joint.setEnabled(False)
        grid.addWidget(self._p_joint, 2, 3)

        # power
        grid.addWidget(lbl("power"), 3, 0)
        self._p_power = QDoubleSpinBox(box)
        self._p_power.setRange(0.0, 1e6)
        self._p_power.setDecimals(3)
        self._p_power.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self._p_power.setEnabled(False)
        grid.addWidget(self._p_power, 3, 1)

        # color (r,g,b) + color-picker swatch
        self._p_color = {}
        grid.addWidget(lbl("color r/g/b"), 3, 2)
        for i, ch in enumerate(("R", "G", "B")):
            sp = QDoubleSpinBox(box)
            sp.setRange(0.0, 1.0)
            sp.setSingleStep(0.05)
            sp.setDecimals(3)
            sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
            sp.setEnabled(False)
            sp.setToolTip(ch)
            self._p_color[ch] = sp
            grid.addWidget(sp, 3, 3 + i)
        self._p_color_btn = QPushButton(box)
        self._p_color_btn.setEnabled(False)
        self._p_color_btn.setToolTip("Pick color…")
        self._p_color_btn.setMaximumWidth(28)
        self._p_color_btn.clicked.connect(self._on_pick_color)
        grid.addWidget(self._p_color_btn, 3, 6)

        # model + browse
        grid.addWidget(lbl("model"), 4, 0)
        self._p_model = QLineEdit(box)
        self._p_model.setEnabled(False)
        grid.addWidget(self._p_model, 4, 1, 1, 3)
        self._p_model_btn = QPushButton("…", box)
        self._p_model_btn.setEnabled(False)
        self._p_model_btn.setMaximumWidth(28)
        self._p_model_btn.clicked.connect(self._on_browse_model)
        grid.addWidget(self._p_model_btn, 4, 4)

        # mount: pose of the part's joint frame relative to its parent
        # (tx,ty,tz mm; rx,ry,rz deg). Used by Compute DH.
        grid.addWidget(lbl("mount"), 5, 0)
        self._p_mount = {}
        mount_labels = [("tx", -20000.0, 20000.0), ("ty", -20000.0, 20000.0),
                        ("tz", -20000.0, 20000.0),
                        ("rx", -720.0, 720.0), ("ry", -720.0, 720.0),
                        ("rz", -720.0, 720.0)]
        for i, (ch, lo, hi) in enumerate(mount_labels):
            sp = QDoubleSpinBox(box)
            sp.setRange(lo, hi)
            sp.setSingleStep(0.1)
            sp.setDecimals(3)
            sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
            sp.setEnabled(False)
            sp.setToolTip(ch + (" mm" if i < 3 else " deg"))
            self._p_mount[ch] = sp
            grid.addWidget(sp, 5, 1 + i)

        hint = QLabel("Select a part to edit its yaml properties; Apply rebuilds the view.", box)
        hint.setStyleSheet("color: %s; font-size: 11pt;"
                           % ("#777" if _ink_active()
                              else "#%02x%02x%02x" % _accent()))
        grid.addWidget(hint, 6, 0, 1, 7)

        self._props_fields = {
            "id": self._p_id, "type": self._p_type, "axis": self._p_axis,
            "joint": self._p_joint, "power": self._p_power,
            "model": self._p_model,
        }
        # Any property edit applies to the selected part's node right away and
        # arms Save (so the user doesn't have to press Apply first).
        self._p_id.editingFinished.connect(self._on_props_changed)
        self._p_model.editingFinished.connect(self._on_props_changed)
        for w in (self._p_type, self._p_axis):
            w.currentIndexChanged.connect(self._on_props_changed)
        self._p_joint.valueChanged.connect(self._on_props_changed)
        for sp in (self._p_power,):
            sp.valueChanged.connect(self._on_props_changed)
        for sp in self._p_color.values():
            sp.valueChanged.connect(self._on_props_changed)
            sp.valueChanged.connect(lambda *_a: self._update_color_swatch())
        for sp in self._p_mount.values():
            sp.valueChanged.connect(self._on_props_changed)
        return box

    def _on_props_changed(self, *_args):
        """Persist property edits into the selected part's in-memory node and
        arm the Save button. Guarded so programmatic field population doesn't
        record a change."""
        if self._loading_props:
            return
        _def = self._selected
        if _def is None:
            return
        self._props_to_node(_def["node"])
        self._save_btn.setEnabled(True)
        self._update_color_swatch()

    def _on_browse_model(self):
        """Pick an STL/obj model file for the selected part."""
        from PySide6.QtWidgets import QFileDialog
        start = self._p_model.text() or (self._config_dir or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select model", start,
            "Models (*.stl *.obj *.ply);;All files (*)")
        if path:
            # Store relative to the config dir when possible, like the yml does.
            rel = path
            if self._config_dir and os.path.isabs(path):
                try:
                    rel = os.path.relpath(path, self._config_dir)
                except ValueError:
                    rel = path
            self._p_model.setText(os.path.normpath(rel))
            self._on_props_changed()

    def _on_pick_color(self):
        """Open a native color dialog and apply the chosen color, then write the
        current (R, G, B) fields to the part's node."""
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        current = QColor(
            int(self._p_color["R"].value() * 255),
            int(self._p_color["G"].value() * 255),
            int(self._p_color["B"].value() * 255))
        chosen = QColorDialog.getColor(
            current, self, "Part color", QColorDialog.ShowAlphaChannel)
        if not chosen.isValid():
            return
        self._p_color["R"].setValue(chosen.redF())
        self._p_color["G"].setValue(chosen.greenF())
        self._p_color["B"].setValue(chosen.blueF())
        self._on_props_changed()

    def _update_color_swatch(self):
        """Paint the swatch button to preview the current part color."""
        if not getattr(self, "_p_color_btn", None):
            return
        r = int(self._p_color["R"].value() * 255)
        g = int(self._p_color["G"].value() * 255)
        b = int(self._p_color["B"].value() * 255)
        self._p_color_btn.setStyleSheet(
            "QPushButton { background-color: rgb(%d,%d,%d);"
            " border: 1px solid #555; border-radius: 4px; }" % (r, g, b))

    def _disable_props_fields(self):
        """Disable all part-property editors (no part selected)."""
        for f in self._props_fields.values():
            f.setEnabled(False)
        for sp in self._p_color.values():
            sp.setEnabled(False)
        for sp in self._p_mount.values():
            sp.setEnabled(False)
        self._p_model_btn.setEnabled(False)
        if getattr(self, "_p_color_btn", None) is not None:
            self._p_color_btn.setEnabled(False)

    def _set_props_enabled(self, enabled):
        for f in self._props_fields.values():
            f.setEnabled(enabled)
        for sp in self._p_color.values():
            sp.setEnabled(enabled)
        for sp in self._p_mount.values():
            sp.setEnabled(enabled)
        self._p_model_btn.setEnabled(enabled)
        if getattr(self, "_p_color_btn", None) is not None:
            self._p_color_btn.setEnabled(enabled)
        if enabled:
            self._update_color_swatch()

    def _populate_props_fields(self, node):
        """Fill the property editors from a selected part's yaml node."""
        self._loading_props = True
        try:
            self._p_id.setText(str(node.get("id") or ""))
            self._p_type.setCurrentText(str(node.get("type") or "static"))

            axis = node.get("axis")
            if axis is None:
                self._p_axis.setCurrentText("(none)")
            else:
                self._p_axis.setCurrentText(str(axis))

            joint = node.get("joint")
            if joint is None:
                self._p_joint.setValue(-1)
            else:
                try:
                    self._p_joint.setValue(int(joint))
                except (TypeError, ValueError):
                    self._p_joint.setValue(-1)

            try:
                self._p_power.setValue(float(node.get("power") or 0.0))
            except (TypeError, ValueError):
                self._p_power.setValue(0.0)

            color = node.get("color") or [0.0, 0.0, 0.0]
            for i, ch in enumerate(("R", "G", "B")):
                try:
                    self._p_color[ch].setValue(float(color[i]))
                except (TypeError, ValueError, IndexError):
                    self._p_color[ch].setValue(0.0)

            self._p_model.setText(str(node.get("model") or ""))
            mount = node.get("mount") or [0.0] * 6
            for i, ch in enumerate(("tx", "ty", "tz", "rx", "ry", "rz")):
                try:
                    self._p_mount[ch].setValue(float(mount[i]))
                except (TypeError, ValueError, IndexError):
                    self._p_mount[ch].setValue(0.0)
            self._set_props_enabled(True)
        finally:
            self._loading_props = False

    def _props_to_node(self, node):
        """Push the current property-editor values into a yaml node."""
        _id = self._p_id.text().strip()
        if _id:
            node["id"] = _id
        elif "id" in node:
            del node["id"]

        node["type"] = self._p_type.currentText()

        axis = self._p_axis.currentText()
        node["axis"] = None if axis == "(none)" else axis

        joint = self._p_joint.value()
        node["joint"] = None if joint < 0 else joint

        node["power"] = float(self._p_power.value())
        node["color"] = [self._p_color[ch].value()
                          for ch in ("R", "G", "B")]
        model = self._p_model.text().strip()
        if model:
            node["model"] = model
        elif "model" in node:
            del node["model"]
        node["mount"] = [self._p_mount[ch].value()
                          for ch in ("tx", "ty", "tz", "rx", "ry", "rz")]

    def _on_dh_toggle(self, checked):
        self._freeze_dh = not checked
        if checked and self._renderer_window is not None:
            # re-apply DH frames immediately so the arm snaps back to live
            try:
                if self._joint_positions is not None:
                    self._apply_dh_frames(self._joint_positions)
                    self._renderer_window.Render()
            except Exception:  # noqa: BLE001 - best-effort
                pass
        elif self._renderer_window is not None:
            self._renderer_window.Render()

    # ------------------------------------------------------------------
    # DH parameter editor (realtime via HAL pins, save to .hal)
    # ------------------------------------------------------------------
    def _build_dh_panel(self):
        """Build the DH parameters editor: a grid of spinboxes for alpha / a /
        d and the unrotate flag per joint, bound to a TNC HAL QComponent so
        edits apply in realtime, with a Save that rewrites the .hal file."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QCheckBox

        box = QGroupBox("DH PARAMETERS", self)
        box.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 11pt; color: %s; border: 0; }"
            "QDoubleSpinBox { font-size: 11pt; }"
            "QCheckBox { font-size: 11pt; }"
            "QPushButton { font-size: 11pt; }"
            % ("#000" if _ink_active() else "#%02x%02x%02x" % _accent()))
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 4, 8, 4)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)

        header_style = "color: %s; font-size: 11pt; font-weight: 700;" % (
            "#555" if _ink_active() else "#%02x%02x%02x" % _accent())
        for c, title in enumerate(["", "α°", "a", "d", "unr"]):
            h = QLabel(title, box)
            h.setAlignment(Qt.AlignCenter)
            h.setStyleSheet(header_style)
            grid.addWidget(h, 0, c)

        # store spinboxes + the HAL pin they drive, per joint
        self._dh = {"comp": None, "alpha": {}, "a": {}, "d": {},
                    "unrotate": {}, "haspins": False}
        njoints = self._dh_joint_count()
        for j in range(njoints):
            jl = QLabel("J%d" % j, box)
            jl.setAlignment(Qt.AlignCenter)
            jl.setStyleSheet("color: %s; font-size: 11pt; font-weight: 600;" % (
                "#444" if _ink_active() else "#%02x%02x%02x" % _accent()))
            grid.addWidget(jl, j + 1, 0)

            sp = QDoubleSpinBox(box)
            sp.setRange(-360.0, 360.0)
            sp.setSingleStep(1.0)
            sp.setDecimals(4)
            sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
            grid.addWidget(sp, j + 1, 1)
            self._dh["alpha"][j] = sp

            sp = QDoubleSpinBox(box)
            sp.setRange(-10000.0, 10000.0)
            sp.setSingleStep(1.0)
            sp.setDecimals(4)
            sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
            grid.addWidget(sp, j + 1, 2)
            self._dh["a"][j] = sp

            sp = QDoubleSpinBox(box)
            sp.setRange(-10000.0, 10000.0)
            sp.setSingleStep(1.0)
            sp.setDecimals(4)
            sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
            grid.addWidget(sp, j + 1, 3)
            self._dh["d"][j] = sp

            cb = QCheckBox(box)
            cb.setStyleSheet("QCheckBox { color: %s; font-size: 11pt; }" % (
                "#555" if _ink_active() else "#%02x%02x%02x" % _accent()))
            grid.addWidget(cb, j + 1, 4)
            self._dh["unrotate"][j] = cb

        # small header row + buttons
        row = njoints + 1
        hint = QLabel("Realtime via HAL; Save writes robot_arm-kinematics.hal.", box)
        hint.setStyleSheet(
            "color: %s; font-size: 11pt;" % ("#777" if _ink_active() else
                                            "#%02x%02x%02x" % _accent()))
        grid.addWidget(hint, row, 0, 1, 3)
        apply_btn = QPushButton("Apply DH", box)
        apply_btn.clicked.connect(self._on_apply_dh)
        apply_btn.setStyleSheet("font-size: 11pt;")
        save_btn_dh = QPushButton("Save DH", box)
        save_btn_dh.clicked.connect(self._on_save_dh)
        save_btn_dh.setStyleSheet("font-size: 11pt;")
        grid.addWidget(apply_btn, row, 3, 1, 1)
        grid.addWidget(save_btn_dh, row, 4, 1, 1)
        compute_btn = QPushButton("Compute DH from parts", box)
        compute_btn.setToolTip(
            "Derive alpha/a/d from each part's \"mount\" placement and write "
            "them to robot_arm-kinematics.hal.\n\nThis only reads the mount "
            "values as written - use the Analyze tab to validate the result "
            "against the real STL geometry.")
        compute_btn.setStyleSheet("font-size: 11pt;")
        compute_btn.clicked.connect(self._on_compute_dh)
        grid.addWidget(compute_btn, row + 1, 0, 1, 5)

        # load current values from HAL (if reachable)
        self._dh_load_values()
        return box

    # ------------------------------------------------------------------
    # Analyze tab: STL + yaml geometry -> DH parameters, with validation
    # ------------------------------------------------------------------
    _SEVERITY_COLORS: ClassVar[dict] = {
        "error": "#ff5252",
        "warning": "#ffb300",
        "info": "#4fc3f7",
        "ok": "#66bb6a",
    }

    def _build_analyze_tab(self):
        """Build the geometry analysis tab.

        Reads the machine-parts yaml and its STL meshes, derives the joint axes
        and the DH parameters they imply, and validates the active table
        (Jacobian rank / conditioning / singularities) so a structurally
        impossible table is caught here instead of showing up as an opaque
        "kinematicsInverse failed" at runtime.
        """
        tab = QWidget(self)
        tab.setAutoFillBackground(False)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(6)

        # ---- action bar + status banner ---------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(6)
        run_btn = QPushButton("Analyze model", tab)
        run_btn.setToolTip(
            "Read the machine-parts yaml + STL meshes, derive the joint axes "
            "and DH parameters, and check the active table for rank / "
            "conditioning problems.")
        run_btn.clicked.connect(self._on_analyze)
        self._an_run_btn = run_btn

        copy_btn = QPushButton("Copy report", tab)
        copy_btn.setToolTip("Copy the full text report to the clipboard.")
        copy_btn.clicked.connect(self._on_copy_report)
        copy_btn.setEnabled(False)
        self._an_copy_btn = copy_btn

        apply_btn = QPushButton("Load derived → DH tab", tab)
        apply_btn.setToolTip(
            "Put the derived alpha/a/d values into the DH tab's fields so you "
            "can review them before applying or saving.")
        apply_btn.clicked.connect(self._on_use_derived)
        apply_btn.setEnabled(False)
        self._an_apply_btn = apply_btn

        bar.addWidget(run_btn)
        bar.addWidget(copy_btn)
        bar.addWidget(apply_btn)
        bar.addStretch(1)
        outer.addLayout(bar)

        banner = QLabel("not analyzed yet", tab)
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "color: #888; font-size: 11pt; padding: 4px 8px;"
            "border-radius: 6px; background: rgba(255,255,255,0.04);")
        self._an_banner = banner
        outer.addWidget(banner)

        # ---- result sub-tabs --------------------------------------------
        sub = QTabWidget(tab)
        sub.setFont(tab.font())
        self._an_tabs = sub

        self._an_findings = self._make_findings_tree(sub)
        sub.addTab(self._wrap_scroll(self._an_findings), "Findings")

        self._an_joints = self._make_table(
            sub, ["J", "part", "origin (mm)", "axis", "mesh", "size (mm)"])
        sub.addTab(self._wrap_scroll(self._an_joints), "Joints")

        self._an_geom = self._make_table(
            sub, ["pair", "distance (mm)", "twist (deg)", "relation"])
        sub.addTab(self._wrap_scroll(self._an_geom), "Geometry")

        self._an_dh = self._make_table(
            sub, ["i", "active α°", "active a", "active d",
                  "derived α°", "derived a", "derived d"])
        sub.addTab(self._wrap_scroll(self._an_dh), "DH compare")

        self._an_sweep = self._make_table(
            sub, ["joint angle (deg)", "rank", "conditioning σ_min"])
        sub.addTab(self._wrap_scroll(self._an_sweep), "Conditioning")

        self._an_text = QPlainTextEdit(sub)
        self._an_text.setReadOnly(True)
        self._an_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._an_text.setStyleSheet(
            "font-family: monospace; font-size: 10pt;")
        sub.addTab(self._an_text, "Report")

        outer.addWidget(sub, 1)
        self._analyze_tab = tab
        self._report = None
        return tab

    def _wrap_scroll(self, widget):
        """Put a result widget in a frameless scroll area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(widget)
        return scroll

    def _make_table(self, parent, headers):
        """A compact read-only table styled for the analysis panels."""
        t = QTableWidget(0, len(headers), parent)
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setAlternatingRowColors(True)
        t.setStyleSheet("font-size: 10pt;")
        head = t.horizontalHeader()
        head.setStretchLastSection(True)
        for c in range(len(headers)):
            head.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        return t

    def _make_findings_tree(self, parent):
        tree = QTreeWidget(parent)
        tree.setColumnCount(1)
        tree.setHeaderHidden(True)
        tree.setStyleSheet("font-size: 10pt;")
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        return tree

    def _on_analyze(self):
        """Run the geometry analysis and populate every result panel."""
        if not self._machine_parts_file:
            self._an_banner.setText(
                "No [VTK] MACHINE_PARTS yaml for this config - nothing to "
                "analyze.")
            self._an_banner.setStyleSheet(
                "color: %s; font-size: 11pt; padding: 4px 8px;"
                "border-radius: 6px; background: rgba(255,179,0,0.12);"
                % self._SEVERITY_COLORS["warning"])
            return

        current = None
        if self._config_dir:
            hal_path = os.path.join(self._config_dir,
                                    "robot_arm-kinematics.hal")
            if os.path.isfile(hal_path):
                current = dh_analysis.read_hal_table(
                    hal_path, self._dh_joint_count())

        self._an_run_btn.setEnabled(False)
        try:
            report = dh_analysis.analyse(
                self._machine_parts_file,
                data=self._machine_parts_data,
                config_dir=self._config_dir,
                current_table=current)
        except Exception as exc:  # noqa: BLE001 - surface analysis failures
            tnc_main.LOG.exception("RobotArm: analysis failed")
            self._an_banner.setText("analysis failed: %s" % exc)
            self._an_banner.setStyleSheet(
                "color: %s; font-size: 11pt; padding: 4px 8px;"
                "border-radius: 6px; background: rgba(255,82,82,0.12);"
                % self._SEVERITY_COLORS["error"])
            return
        finally:
            self._an_run_btn.setEnabled(True)

        self._report = report
        self._populate_analysis(report)
        self._an_copy_btn.setEnabled(True)
        self._an_apply_btn.setEnabled(bool(report.derived))

    def _populate_analysis(self, report):
        """Fill the findings / joints / geometry / DH / sweep panels."""
        # ---- banner ------------------------------------------------------
        counts = {}
        for f in report.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        worst = report.worst
        bits = ["%d %s" % (counts[k], k)
                for k in ("error", "warning", "info") if k in counts]
        summary = ", ".join(bits) if bits else "no problems found"
        rank_txt = ""
        if report.current_rank is not None:
            rank_txt = "   |   active table rank %d/%d, \u03c3min %.4f" % (
                report.current_rank, len(report.current or []) or 6,
                report.current_sigma or 0.0)
        self._set_banner(worst, "%d joints analyzed \u2014 %s%s"
                         % (len(report.joints), summary, rank_txt))

        # ---- findings ----------------------------------------------------
        tree = self._an_findings
        tree.clear()
        order = dh_analysis.SEVERITY_ORDER
        for f in sorted(report.findings,
                        key=lambda x: order.get(x.severity, 9)):
            item = QTreeWidgetItem(["[%s]  %s" % (f.severity.upper(), f.title)])
            item.setForeground(0, QColor(self._SEVERITY_COLORS.get(
                f.severity, "#cccccc")))
            if f.detail:
                child = QTreeWidgetItem([f.detail])
                child.setForeground(0, QColor("#aaaaaa"))
                item.addChild(child)
            tree.addTopLevelItem(item)
        tree.expandAll()

        # ---- joints ------------------------------------------------------
        t = self._an_joints
        t.setRowCount(len(report.joints))
        for r, j in enumerate(report.joints):
            mesh = report.meshes.get(j.index)
            if mesh is None or not mesh.exists:
                mesh_txt = mesh.error if mesh is not None else "not read"
                size_txt = ""
            else:
                mesh_txt = "%d tris" % mesh.triangles
                size_txt = " x ".join("%.1f" % v for v in mesh.size)
            cells = ["J%d" % j.index, str(j.part_id),
                     ", ".join("%.1f" % v for v in j.origin),
                     str(j.axis_name), mesh_txt, size_txt]
            for c, text in enumerate(cells):
                t.setItem(r, c, QTableWidgetItem(text))

        # ---- pairwise geometry -------------------------------------------
        t = self._an_geom
        t.setRowCount(len(report.pairs))
        for r, p in enumerate(report.pairs):
            cells = ["J%d \u2192 J%d" % (p.i, p.i + 1),
                     "%.3f" % p.distance, "%.2f" % p.angle_deg, p.relation]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 3 and p.relation == "coincident":
                    item.setForeground(QColor(self._SEVERITY_COLORS["error"]))
                t.setItem(r, c, item)

        # ---- DH comparison ------------------------------------------------
        t = self._an_dh
        rows = max(len(report.current or []), len(report.derived or []))
        t.setRowCount(rows)
        for r in range(rows):
            cur = report.current[r] if r < len(report.current or []) else None
            der = report.derived[r] if r < len(report.derived or []) else None
            t.setItem(r, 0, QTableWidgetItem(str(r)))
            for base, trip in ((1, cur), (4, der)):
                if trip is None:
                    continue
                alpha, a, d = trip
                for off, val in enumerate((_rad2deg(alpha), a, d)):
                    t.setItem(r, base + off, QTableWidgetItem("%.3f" % val))
            # highlight where the active table disagrees with the model
            if cur and der:
                for off in range(3):
                    cv = cur[off] if off else _rad2deg(cur[0])
                    dv = der[off] if off else _rad2deg(der[0])
                    if abs(float(cv) - float(dv)) > 1e-3:
                        for col in (1 + off, 4 + off):
                            it = t.item(r, col)
                            if it is not None:
                                it.setForeground(
                                    QColor(self._SEVERITY_COLORS["warning"]))

        # ---- conditioning sweep -------------------------------------------
        t = self._an_sweep
        t.setRowCount(len(report.sweep))
        njoints = len(report.current or []) or 6
        for r, (ang, rank, sigma) in enumerate(report.sweep):
            cells = ["%.1f" % ang, "%d" % rank, "%.4f" % sigma]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if rank < njoints:
                    item.setForeground(QColor(self._SEVERITY_COLORS["error"]))
                elif sigma < 0.05:
                    item.setForeground(QColor(self._SEVERITY_COLORS["warning"]))
                t.setItem(r, c, item)

        # ---- full text report ----------------------------------------------
        self._an_text.setPlainText(dh_analysis.format_report(report))

    def _set_banner(self, severity, text):
        """Colour the analysis banner according to the worst finding."""
        color = self._SEVERITY_COLORS.get(severity, "#888")
        rgba = {"error": "rgba(255,82,82,0.12)",
                "warning": "rgba(255,179,0,0.12)",
                "info": "rgba(79,195,247,0.12)",
                "ok": "rgba(102,187,106,0.12)"}.get(severity,
                                                   "rgba(255,255,255,0.04)")
        self._an_banner.setText(text)
        self._an_banner.setStyleSheet(
            "color: %s; font-size: 11pt; font-weight: 600; padding: 4px 8px;"
            "border-radius: 6px; background: %s;" % (color, rgba))

    def _on_copy_report(self):
        """Copy the plain-text analysis report to the clipboard."""
        if self._report is None:
            return
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(
                dh_analysis.format_report(self._report))
            self._status.setText("analysis report copied")
        except Exception as exc:  # noqa: BLE001 - clipboard is best-effort
            tnc_main.LOG.exception("RobotArm: copy report failed")
            self._status.setText("copy failed: %s" % exc)

    def _on_use_derived(self):
        """Load the derived alpha/a/d into the DH tab for review.

        Deliberately does *not* apply or save - the derived table comes from
        the drawn pose, which may not be a valid kinematic zero, so it needs a
        human look before it goes anywhere near the running kinematics.
        """
        if self._report is None or not self._report.derived:
            return
        n = self._dh_joint_count()
        for j, (alpha, a, d) in enumerate(self._report.derived[:n]):
            try:
                self._dh["alpha"][j].setValue(_rad2deg(alpha))
                self._dh["a"][j].setValue(a)
                self._dh["d"][j].setValue(d)
            except (KeyError, RuntimeError):
                continue
        # jump to the DH tab so the loaded values are visible straight away
        try:
            self._tabs.setCurrentIndex(self._tabs.indexOf(self._dh_tab))
        except Exception:  # noqa: BLE001 - tab lookup is cosmetic
            pass
        self._status.setText(
            "derived DH loaded into the DH tab - review, then Apply/Save")

    def _dh_joint_count(self):
        return 6

    def _dh_file_values(self):
        """Parse DH values from the on-disk robot_arm-kinematics.hal (used as a
        fallback when the live HAL pins aren't reachable, e.g. config editor /
        dialog opened before the sim's kinematics HAL is loaded). Returns a dict
        keyed like ``{KRIND}-{n}`` (e.g. ``"ALPHA-3"``) with raw .hal values
        (alpha in radians)."""
        if not self._config_dir:
            return {}
        path = os.path.join(self._config_dir, "robot_arm-kinematics.hal")
        if not os.path.isfile(path):
            return {}
        out = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if not s.startswith("setp genserkins."):
                        continue
                    body = line.split("genserkins.", 1)[1]
                    # body is like "ALPHA-0   0" -> name + value
                    name, _, val = body.partition(" ")
                    name = name.strip()
                    try:
                        out[name] = float(val.split()[0])
                    except (ValueError, IndexError):
                        continue
        except Exception:  # noqa: BLE001 - best-effort
            return {}
        return out

    def _dh_hal(self):
        """Return the linuxcnc ``hal`` module, or None if unavailable."""
        try:
            import hal  # noqa: F401
            return hal
        except Exception:  # noqa: BLE001
            return None

    def _dh_read_pin(self, kind, j):
        """Read a DH value from genserkins HAL pin (alive check optional)."""
        hal = self._dh_hal()
        if hal is None:
            return None
        name = "genserkins.%s-%d" % (kind, j)
        try:
            return hal.getp(name)
        except Exception:  # noqa: BLE001
            return None

    def _dh_load_values(self):
        """Populate the DH spinboxes from live HAL pins if reachable, else from
        the on-disk kinematics .hal so the fields are always editable. "alpha"
        is shown in degrees; a / d in mm; unrotate as a checkbox."""
        halo = self._dh_hal()
        file_vals = self._dh_file_values()
        self._dh["haspins"] = halo is not None
        live = halo is not None
        for j in range(self._dh_joint_count()):
            for kind, key, is_deg in (
                    ("ALPHA", "alpha", True),
                    ("A", "a", False),
                    ("D", "d", False)):
                sp = self._dh[key][j]
                v = self._dh_read_pin(kind, j) if live else None
                if v is None:
                    v = file_vals.get("%s-%d" % (kind, j))
                if v is None:
                    sp.setEnabled(False)
                    continue
                sp.setEnabled(True)
                sp.setValue(v if not is_deg else _rad2deg(v))
            cb = self._dh["unrotate"][j]
            v = self._dh_read_pin("unrotate", j) if live else None
            if v is None:
                v = file_vals.get("unrotate-%d" % j)
            if v is None:
                cb.setEnabled(False)
            else:
                cb.setEnabled(True)
                cb.setChecked(bool(v))

    def _on_apply_dh(self):
        """Write the DH values live to genserkins, via a TNC HAL QComponent's
        OUT pins netted to the kinematics pins (so it is realtime through the
        qtpyvcp pin layer). Falls back to ``hal.set_p`` if the component can't\
        be wired."""
        comp = self._dh_ensure_component()
        hal = self._dh_hal()
        if comp is None and hal is None:
            self._status.setText("DH: HAL unavailable")
            return
        ok = 0
        for j in range(self._dh_joint_count()):
            try:
                vals = {"ALPHA-%d" % j: _deg2rad(self._dh["alpha"][j].value()),
                        "A-%d" % j: self._dh["a"][j].value(),
                        "D-%d" % j: self._dh["d"][j].value(),
                        "unrotate-%d" % j: int(self._dh["unrotate"][j].isChecked())}
                for kind, val in vals.items():
                    pin = self._dh_pin_for(kind)
                    if pin is not None:
                        pin.value = val
                    elif hal is not None:
                        hal.set_p("genserkins.%s" % kind, val)
                    else:
                        return
                ok += 1
            except Exception as exc:  # noqa: BLE001
                tnc_main.LOG.exception("DH apply failed for joint %d", j)
                self._status.setText("DH apply error: %s" % exc)
                return
        self._status.setText("DH applied (live)")

    def _dh_ensure_component(self):
        """Create (once) the TNC ``tnc_kin`` HAL QComponent with OUT QPins for
        the DH params, and net each to the matching genserkins pin. Returns the
        component, or None if HAL/component creation isn't available."""
        if self._dh.get("comp") is not None:
            return self._dh["comp"]
        try:
            import hal as _hal
            from qtpyvcp import hal as qhal

            comp = qhal.getComponent("tnc_kin")
            self._dh["comp"] = comp
            self._dh.setdefault("pins", {})
            for j in range(self._dh_joint_count()):
                for kind, ptype in (("ALPHA-%d" % j, "float"),
                                    ("A-%d" % j, "float"),
                                    ("D-%d" % j, "float"),
                                    ("unrotate-%d" % j, "s32")):
                    pin_name = "dh_%s" % kind.replace("-", "_")
                    comp.addPin(pin_name, ptype, "out")
                    self._dh["pins"][kind] = comp.getPin(pin_name)
                    # net OUT pin -> signal -> genserkins IN pin
                    try:
                        sig = "tnc_kin_dh_%s" % kind.replace("-", "_")
                        sig_type = _hal.HAL_FLOAT if ptype == "float" else _hal.HAL_S32
                        _hal.new_signal(sig, sig_type)
                        _hal.connect(sig, "tnc_kin.%s" % pin_name)
                        _hal.connect(sig, "genserkins.%s" % kind)
                    except Exception as exc:  # noqa: BLE001 - best-effort net
                        tnc_main.LOG.debug("tnc_kin net %s: %s", kind, exc)
            comp.ready()
            return comp
        except Exception as exc:  # noqa: BLE001
            tnc_main.LOG.exception("DH component setup failed")
            self._dh["comp"] = None
            return None

    def _dh_pin_for(self, kind):
        return self._dh.get("pins", {}).get(kind) if self._dh.get("comp") else None

    def _on_save_dh(self):
        """Write the current DH values into robot_arm-kinematics.hal."""
        if not self._config_dir:
            return
        hal_path = os.path.join(self._config_dir, "robot_arm-kinematics.hal")
        if not os.path.isfile(hal_path):
            self._status.setText("DH: no kinematics hal")
            return
        vals = {}
        for j in range(self._dh_joint_count()):
            vals["ALPHA-%d" % j] = _deg2rad(self._dh["alpha"][j].value())
            vals["A-%d" % j] = self._dh["a"][j].value()
            vals["D-%d" % j] = self._dh["d"][j].value()
            vals["unrotate-%d" % j] = int(self._dh["unrotate"][j].isChecked())
        try:
            with open(hal_path, "r", encoding="utf-8") as fh:
                text = fh.read()
            text = _rewrite_hal_setp(text, vals)
            with open(hal_path, "w") as fh:
                fh.write(text)
        except Exception as exc:  # noqa: BLE001
            tnc_main.LOG.exception("DH save failed")
            self._status.setText("DH save error: %s" % exc)
            return
        self._status.setText("DH saved to %s" % os.path.basename(hal_path))

    def _collect_mount_frames(self):
        """Return ``{joint_idx: (alpha, a, d)}`` derived from each angular part's
        ``mount`` (the pose of its joint frame relative to its parent). Returns
        None if there are no angular parts with a usable mount. The channel
        follows ``_part_defs`` order (depth-first), so joints land in order."""
        angular = [_d for _d in self._part_defs
                   if _d.get("type") == "angular"
                   and _d.get("joint") is not None]
        if not angular:
            return {}
        out = {}
        for _d in angular:
            node = _d["node"]
            mount = list(node.get("mount") or [0.0] * 6)[:6]
            rel = _mount_matrix(mount)
            alpha, a, d = _frame_to_dh(rel)
            out[int(_d["joint"])] = (alpha, a, d)
        return out

    def _on_compute_dh(self):
        """Compute alpha/a/d from the parts' ``mount`` placements and write them
        to ``robot_arm-kinematics.hal``, then refresh the DH panel and view."""
        if not self._config_dir:
            self._status.setText("DH: no config dir")
            return
        hal_path = os.path.join(self._config_dir, "robot_arm-kinematics.hal")
        if not os.path.isfile(hal_path):
            self._status.setText("DH: no kinematics hal")
            return
        computed = self._collect_mount_frames()
        if not computed:
            self._status.setText("DH: select angular parts with a mount")
            return

        vals = {}
        for j in range(self._dh_joint_count()):
            if j not in computed:
                continue
            alpha, a, d = computed[j]
            vals["ALPHA-%d" % j] = _deg2rad(alpha)
            vals["A-%d" % j] = a
            vals["D-%d" % j] = d

        try:
            with open(hal_path, "r", encoding="utf-8") as fh:
                text = fh.read()
            text = _rewrite_hal_setp(text, vals)
            with open(hal_path, "w") as fh:
                fh.write(text)
        except Exception as exc:  # noqa: BLE001
            tnc_main.LOG.exception("DH compute/save failed")
            self._status.setText("DH compute error: %s" % exc)
            return

        # Update the DH tab spinboxes so the new values are shown/editable.
        try:
            for j in range(self._dh_joint_count()):
                if j not in computed:
                    continue
                alpha, a, d = computed[j]
                self._dh["alpha"][j].setValue(alpha)
                self._dh["a"][j].setValue(a)
                self._dh["d"][j].setValue(d)
        except Exception:  # noqa: BLE001 - panel may not be built yet
            pass

        self._status.setText("DH computed from parts and saved")

    def _setup_picking(self):
        """Pick parts from Qt mouse events on the view widget.

        We hook the widget's Qt mouse events directly (via an event filter)
        instead of relying on the VTK interactor's observers, which aren't
        firing reliably inside ``QVTKRenderWindowInteractor`` here. The
        camera orbit still works because the filter observes but doesn't
        consume the events; we only pick on a left click with little drag.
        """
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.01)
        picker.PickFromListOn()
        self._picker = picker
        self._pick_press = None
        self._rebuild_pick_list()
        if self._view is not None:
            self._view.installEventFilter(self)

    def eventFilter(self, watched, event):
        """Observe the VTK view's mouse events: record presses and pick on a
        left click (press+release with little movement). Returns False so the
        widget's own camera handling still runs."""
        from PySide6.QtCore import QEvent

        if watched is not self._view:
            return False
        if event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                self._pick_press = (
                    (event.position().x(), event.position().y()),
                    _view_scale(self._view),
                    float(self._view.height()),
                )
        elif event.type() == QEvent.MouseButtonRelease:
            entry = self._pick_press
            self._pick_press = None
            if entry is None or event.button() != Qt.LeftButton:
                return False
            press, scale, height = entry
            x0, y0 = press
            x1, y1 = event.position().x(), event.position().y()
            # Only treat as a click when the pointer barely moved.
            if abs(x1 - x0) * scale > 6 or abs(y1 - y0) * scale > 6:
                return False
            # Qt mouse Y is top-down; VTK pick Y is bottom-up, so flip it.
            vtk_x = x0 * scale
            vtk_y = (height - y0) * scale
            _def = self._pick_part(vtk_x, vtk_y)
            if _def is None:
                self._status.setText("no part under cursor")
            self._select_part(_def)
        return False

    def _rebuild_pick_list(self):
        """(Re)add the current parts to the picker's pick list."""
        if self._picker is None:
            return
        self._picker.InitializePickList()
        for _def in self._part_defs:
            self._picker.AddPickList(_def["asm"])
            actor = self._actor_for_asm(_def["asm"])
            if actor is not None:
                self._picker.AddPickList(actor)

    def _pick_part(self, x, y):
        if self._picker is None:
            return None
        if self._picker.Pick(x, y, 0.0, self._renderer) <= 0:
            return None
        # The assembly path runs from the top (root assembly) down to the leaf
        # actor. Prefer the DEEPEST registered part so clicking a joint selects
        # that joint, not an ancestor (e.g. the table/base).
        best = None
        path = self._picker.GetPath()
        if path is not None:
            path.InitTraversal()
            node = path.GetNextNode()
            while node is not None:
                p = node.GetViewProp()
                if p is not None and id(p) in self._asm_to_def:
                    best = self._asm_to_def[id(p)]
                node = path.GetNextNode()
        if best is not None:
            return best
        # Fall back to the leaf actor lookup.
        actor = self._picker.GetActor()
        if actor is not None and id(actor) in self._actor_to_def:
            return self._actor_to_def[id(actor)]
        prop = self._picker.GetViewProp()
        if prop is not None and id(prop) in self._actor_to_def:
            return self._actor_to_def[id(prop)]
        return None

    def _select_part(self, _def):
        # Single-selection: drop any previously selected part's highlight/state
        # before switching to the clicked part.
        if self._selected is not None or _def is None:
            self._clear_highlight()
        self._selected = _def
        if _def is None:
            self._part_label.setText("click a part in the view…")
            self._part_info.setText("")
            for sp in self._spin.values():
                sp.setEnabled(False)
                sp.setValue(0.0)
            self._apply_btn.setEnabled(False)
            self._reset_btn.setEnabled(False)
            self._disable_props_fields()
            return

        node = _def["node"]
        pos = node.get("position") or [0.0] * 6
        ori = node.get("origin") or [0.0] * 6
        for i, name in enumerate(["PX", "PY", "PZ", "RX", "RY", "RZ"]):
            self._spin[name].setValue(float(pos[i]))
        for i, name in enumerate(["OX", "OY", "OZ"]):
            self._spin[name].setValue(float(ori[i]))
        for sp in self._spin.values():
            sp.setEnabled(True)
        self._apply_btn.setEnabled(True)
        self._reset_btn.setEnabled(True)
        self._populate_props_fields(node)

        ident = _def.get("id") or "?"
        ptype = _def.get("type") or "?"
        extra = ""
        if _def.get("joint") is not None:
            extra = " (J%d, %s)" % (_def["joint"], _def.get("axis") or "?")
        self._part_label.setText("%s · %s%s" % (ident, ptype, extra))

        # Read-only info: model filename + STL geometry centre (mm).
        model = (_def.get("node") or {}).get("model") or ""
        if isinstance(model, str):
            model = os.path.basename(model)
        center = _def.get("center") or [0.0, 0.0, 0.0]
        self._part_info.setText(
            "%s%s center (%.1f, %.1f, %.1f)"
            % (model + " · " if model else "",
               "angular" if ptype == "angular" else "static",
               center[0], center[1], center[2]))
        self._apply_highlight(_def)

    def _current_values(self):
        """Read (position[6], origin[3]) from the spinboxes for the selected part."""
        pos = [self._spin[name].value()
               for name in ["PX", "PY", "PZ", "RX", "RY", "RZ"]]
        ori = [self._spin[name].value() for name in ["OX", "OY", "OZ"]]
        return pos, ori

    def _on_apply_position(self):
        _def = self._selected
        if _def is None:
            return
        pos, ori = self._current_values()
        node = _def["node"]
        node["position"] = pos
        origin = node.get("origin") or [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        origin = list(origin)
        origin[0], origin[1], origin[2] = ori
        node["origin"] = origin
        self._props_to_node(node)
        self._rebuild_arm(keep_node=node)
        # In DH-live mode ``position`` is now a per-joint mount offset that the
        # renderer applies (shifting the STL within its joint frame), so the
        # change stays visible without freezing. Re-apply DH so it takes effect.
        if not self._freeze_dh and self._joint_positions is not None \
                and self._renderer_window is not None:
            try:
                self._apply_dh_frames(self._joint_positions)
                self._renderer_window.Render()
            except Exception:  # noqa: BLE001 - best-effort
                pass
        self._status.setText("applied — position is a joint mount offset")
        self._save_btn.setEnabled(True)

    def _on_save(self):
        """Apply the current field values, then write the yaml back to disk.

        Uses a surgical text rewrite that only touches each part's editable
        field lines, so the hand-written comments and formatting in the file
        are preserved."""
        # Capture edits that haven't been Apply-previewed yet.
        from PySide6.QtWidgets import QMessageBox
        if self._selected is not None:
            self._sync_fields_to_node(self._selected)

        if not self._machine_parts_file or not self._machine_parts_data:
            return

        try:
            with open(self._machine_parts_file, "r", encoding="utf-8") as fh:
                text = fh.read()
            text = self._rewrite_fields(text)
            with open(self._machine_parts_file, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception as exc:  # noqa: BLE001 - surface write errors
            tnc_main.LOG.exception("RobotArm: failed to save")
            self._status.setText("save error: %s" % exc)
            return

        self._status.setText("saved to %s"
                             % os.path.basename(self._machine_parts_file))
        self._save_btn.setEnabled(False)
        QMessageBox.information(self, "Robot Arm",
                                "Saved %s" % self._machine_parts_file)

    def _node_paths(self, root=None):
        """Return ``{id(node): {"path": (keys...), "node": dict}}`` for every
        part node in the machine-parts yaml. ``id`` of the underlying dict is
        used so we can match yaml nodes regardless of their ``id:`` value."""
        root = self._machine_parts_data.get("root")
        out = {}

        def walk(node, path):
            if not isinstance(node, dict):
                return
            out[id(node)] = {"path": path, "node": node}
            for key, val in node.items():
                if isinstance(val, dict):
                    walk(val, path + (key,))

        walk(root, ("root",))
        return out

    def _rewrite_fields(self, text):
        """Replace each part's editable yaml lines (id, type, axis, joint,
        power, color, model, position, origin) with the current in-memory
        values, preserving the rest of the file (comments and formatting).

        Nodes are matched by their yaml key path (indentation-driven) rather
        than their editable ``id:``, so renaming a part's id still saves."""
        import re

        paths = self._node_paths()
        if not paths:
            return text
        # id(node) -> (current string values keyed by field name)
        vals = {}
        for nid, info in paths.items():
            node = info["node"]
            vals[nid] = {
                "id": node.get("id"),
                "type": node.get("type"),
                "axis": node.get("axis"),
                "joint": node.get("joint"),
                "power": node.get("power"),
                "color": list(node.get("color") or []),
                "model": node.get("model"),
                "position": list(node.get("position") or [])[:6],
                "origin": list(node.get("origin") or []),
                "mount": list(node.get("mount") or [])[:6],
            }

        lines = text.splitlines(keepends=True)
        out = []
        # Pre-scan which nodes already carry an explicit ``mount:`` line so the
        # injection below is idempotent (it used to append a duplicate mount
        # line after ``position:`` on every save).
        written_mount = set()
        _pre_stack = []
        for _line in lines:
            _stripped = _line.lstrip()
            _indent = len(_line) - len(_stripped)
            _m = re.match(r'^([A-Za-z_][\w-]*):', _stripped)
            _mkey = _m.group(1) if _m else None
            while _pre_stack and _pre_stack[-1][0] >= _indent:
                _pre_stack.pop()
            _cand = None
            if _pre_stack:
                _cand = _pre_stack[-1][2] + (_mkey,) if _mkey else None
            elif _mkey == "root":
                _cand = ("root",)
            if _cand is not None:
                for _nid, _info in paths.items():
                    if _info["path"] == _cand:
                        _pre_stack.append((_indent, _nid, _info["path"]))
                        break
            if _pre_stack and _mkey == "mount":
                written_mount.add(_pre_stack[-1][1])
        # stack of (indent, node-id, path) for the enclosing node dicts
        stack = []

        def fmt_value(v):
            if v is None:
                return "null"
            if isinstance(v, str):
                return '"%s"' % v
            return _num(v)

        for line in lines:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            m = re.match(r'^([A-Za-z_][\w-]*):', stripped)
            mkey = m.group(1) if m else None

            # pop stack to current indent level
            while stack and stack[-1][0] >= indent:
                stack.pop()

            # If this line's key opens a child node we know (by key path),
            # push it so subsequent same-indent scalar fields belong to it.
            candidate_path = None
            if stack:
                candidate_path = stack[-1][2] + (mkey,) if mkey else None
            elif mkey == "root":
                candidate_path = ("root",)
            if candidate_path is not None:
                for nid, info in paths.items():
                    if info["path"] == candidate_path:
                        stack.append((indent, nid, info["path"]))
                        break

            # rewrite scalar fields within the current (top) node
            if stack and mkey is not None:
                cur_nid = stack[-1][1]
                v = vals[cur_nid]
                if mkey in v or mkey in ("position", "origin", "mount"):
                    if mkey == "position":
                        line = _rewrite_list_if_changed(line, v["position"])
                        # Inject a ``mount:`` line after position if the part has
                        # a non-zero mount but the file doesn't carry one yet.
                        mnt = v["mount"]
                        if any(abs(float(x)) > 1e-12 for x in mnt) \
                                and cur_nid not in written_mount:
                            pad = line[:len(line)-len(stripped)]
                            out.append(line)
                            out.append("%smount: [%s]\n" % (
                                pad, ", ".join(_num(float(x)) for x in mnt)))
                            written_mount.add(cur_nid)
                            continue
                    elif mkey == "mount":
                        line = _rewrite_list_if_changed(
                            line, [float(x) for x in v["mount"]])
                        written_mount.add(cur_nid)
                    elif mkey == "origin":
                        line = _rewrite_list_if_changed(
                            line, [float(x) for x in v["origin"]])
                    elif mkey == "color":
                        if v["color"]:
                            line = _rewrite_list_if_changed(
                                line, [float(x) for x in v["color"]])
                    elif mkey == "id" and v["id"] is not None:
                        old = re.match(r'^id:\s*".*?"', stripped)
                        if old:
                            head = line.split(":", 1)[0]
                            nl = "\n" if line.endswith("\n") else ""
                            line = "%s: \"%s\"%s" % (head, v["id"], nl)
                    elif mkey in ("type", "axis", "joint", "power", "model"):
                        newval = fmt_value(v[mkey])
                        # head includes the original indentation + key name
                        head = line.split(":", 1)[0]
                        nl = "\n" if line.endswith("\n") else ""
                        line = "%s: %s%s" % (head, newval, nl)
            out.append(line)
        return "".join(out)

    def _sync_fields_to_node(self, _def):
        """Push the current field values into the selected part's yaml node
        (used by Save so it captures edits that weren't Apply-previewed)."""
        pos, ori = self._current_values()
        node = _def["node"]
        node["position"] = pos
        origin = node.get("origin") or [0.0] * 6
        origin = list(origin)
        origin[0], origin[1], origin[2] = ori
        node["origin"] = origin
        self._props_to_node(node)

    def _on_reset_position(self):
        _def = self._selected
        if _def is None:
            return
        node = _def["node"]
        node["position"] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self._rebuild_arm(keep_node=node)
        self._status.setText("reset")

    def _apply_highlight(self, _def):
        actor = self._actor_for_asm(_def.get("asm"))
        if actor is not None:
            actor.GetProperty().EdgeVisibilityOn()
            actor.GetProperty().SetEdgeColor(1.0, 0.4, 0.1)
            actor.GetProperty().SetLineWidth(2)
        self._renderer_window.Render()

    def _clear_highlight(self):
        for _def in self._part_defs:
            actor = self._actor_for_asm(_def.get("asm"))
            if actor is not None:
                actor.GetProperty().EdgeVisibilityOff()
        self._renderer_window.Render()

    @staticmethod
    def _actor_for_asm(asm):
        if asm is None:
            return None
        for child in asm.GetParts():
            if isinstance(child, vtk.vtkActor):
                return child
        return None

    def _rebuild_arm(self, keep_node=None):
        """Rebuild the arm assembly from the (possibly edited) yaml data."""
        if not self._machine_parts_file:
            return

        new_root = self._build_asm()

        self._renderer.RemoveActor(self._root_asm)
        self._root_asm = new_root
        self._renderer.AddActor(new_root)
        self._apply_arm_orientation()

        self._part_defs = []
        self._asm_to_def = {}
        self._actor_to_def = {}
        self._selected = None
        self._collect_defs(self._machine_parts_data["root"],
                           self._child_of(new_root))
        self._parts3d = [_d["asm"]
                         for _d in self._part_defs if _d["type"] == "angular"]

        self._add_origin_markers()
        self._add_tool_actor()
        self._renderer_window.Render()
        self._rebuild_pick_list()

        # Re-select the same part (node identities survive a rebuild).
        if keep_node is not None:
            for _d in self._part_defs:
                if _d["node"] is keep_node:
                    self._select_part(_d)
                    return
        self._part_label.setText("selection cleared — click a part")

    @staticmethod
    def _child_of(asm):
        for child in asm.GetParts():
            if isinstance(child, vtk.vtkAssembly):
                return child
        return None

    def _collect_defs(self, node, asm, parent_asm=None):
        """Walk yaml + assembly trees in parallel and record each part.

        ``parent_asm`` is the parent vtkAssembly in the tree (None for the
        root). Recorded on the def so the live DH renderer can compute
        parent-relative placements, plus the STL geometry centre (so each link
        can be mounted centred on its DH joint frame)."""
        if node is None or asm is None:
            return
        pos = node.get("position") or [0.0] * 6
        mount = node.get("mount") or [0.0] * 6
        model = node.get("model")
        center = (0.0, 0.0, 0.0)
        if isinstance(model, str) and model:
            center = _stl_center_abs(model) or (0.0, 0.0, 0.0)
        _def = {
            "node": node,
            "asm": asm,
            "parent_asm": parent_asm,
            "position": list(pos[:3]) if pos else [0.0, 0.0, 0.0],
            "mount": [float(x) for x in (mount or [0.0]*6)][:6],
            "center": list(center),
            "id": node.get("id"),
            "type": node.get("type"),
            "axis": node.get("axis"),
            "joint": node.get("joint"),
            # Best-effort fallback marker position (the real one comes from the
            # actor's matrix in _add_origin_markers).
            "world": (float(pos[0]), float(pos[1]), float(pos[2])),
        }
        self._part_defs.append(_def)
        self._asm_to_def[id(asm)] = _def
        actor = self._actor_for_asm(asm)
        if actor is not None:
            self._actor_to_def[id(actor)] = _def

        child_asms = [c for c in asm.GetParts() if isinstance(c, vtk.vtkAssembly)]
        sub_nodes = [v for v in node.values() if isinstance(v, dict)]
        for cn, ca in zip(sub_nodes, child_asms):
            self._collect_defs(cn, ca, asm)

    # ------------------------------------------------------------------
    # Origin markers (visualize each part's pivot point)
    # ------------------------------------------------------------------
    def _apply_arm_orientation(self):
        """Set the root orientation.

        With DH-exact rendering the arm is already placed in the machine's
        Z-up Cartesian frame by the forward kinematics, so no extra root
        rotation is desired (a rotation would swing the correctly-placed reach
        off its axis). Kept as an explicit no-op for clarity / salvage."""
        if self._root_asm is None:
            return
        try:
            t = vtk.vtkTransform()
            t.Identity()
            self._root_asm.SetUserTransform(t)
        except Exception as exc:  # noqa: BLE001 - orientation is best-effort
            tnc_main.LOG.exception("RobotArm: apply orientation failed")
            self._status.setText("orient error: %s" % exc)

    def _add_origin_markers(self):
        """Add a 3-axis cross at each part's origin / pivot point.

        Each marker is attached to its *own* part's assembly (at the part's
        local origin), so it moves with the arm: rotating that joint keeps the
        pivot fixed while spinning the cross to show orientation, and any
        upstream joint movement carries the marker along with the arm.
        """
        self._clear_origin_markers()
        ar, ag, ab = _accent()
        for _def in self._part_defs:
            asm = _def.get("asm")
            if asm is None:
                continue
            node = _def["node"]
            pivot = node.get("origin") or node.get("position") or [0.0] * 6
            marker = _make_origin_marker(
                float(pivot[0]), float(pivot[1]), float(pivot[2]), (ar, ag, ab))
            if marker is None:
                continue
            asm.AddPart(marker)
            self._markers.append(marker)

    def _clear_origin_markers(self):
        # Detach markers from their parent assemblies and drop references.
        for m in self._markers:
            try:
                parent = m.GetParent()
                if parent is not None:
                    parent.RemovePart(m)
            except Exception:  # noqa: BLE001
                pass
        self._markers = []

    # ------------------------------------------------------------------
    # Tool / spindle actor at the arm tip (nozzle)
    # ------------------------------------------------------------------
    def _add_tool_actor(self):
        """Attach the spindle STL as the end-effector/nozzle at the tip of the
        arm (joint_5), as a child of that part's assembly so it follows the
        arm as joints rotate."""
        self._clear_tool_actor()
        try:
            tip = self._find_tip_def()
            if tip is None or not self._config_dir:
                return
            from vtkmodules.vtkIOGeometry import vtkSTLReader
            from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

            spindle_path = os.path.join(self._config_dir, "models", "spindle.stl")
            if not os.path.isfile(spindle_path):
                return
            reader = vtkSTLReader()
            reader.SetFileName(spindle_path)
            reader.Update()
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(reader.GetOutputPort())
            spindle = vtkActor()
            spindle.SetMapper(mapper)
            spindle.GetProperty().SetColor(0.55, 0.55, 0.6)
            spindle.GetProperty().SetSpecular(0.4)
            spindle.GetProperty().SetSpecularPower(20)

            # Mount the spindle (its Y=0 face) at the wrist link's -Y tip.
            # j5's model arm_joint_5 spans Y[-12.7, 0], so offset 12.7mm toward
            # the nozzle, and place it alongside the part's own position so it
            # sits in the same world spot (parts render at their position here).
            pos = tip.get("position") or [0.0] * 6
            t = vtk.vtkTransform()
            t.Translate(float(pos[0]), float(pos[1]) - 12.7, float(pos[2]))
            spindle.SetUserTransform(t)

            tip_asm = tip["asm"]
            if tip_asm is not None:
                tip_asm.AddPart(spindle)
            self._tool_actor = spindle
        except Exception as exc:  # noqa: BLE001 - tool actor is optional
            tnc_main.LOG.exception("RobotArm: add tool actor failed")
            self._status.setText("tool error: %s" % exc)

    def _clear_tool_actor(self):
        if self._tool_actor is not None:
            try:
                parent = self._tool_actor.GetParent()
                if parent is not None:
                    parent.RemovePart(self._tool_actor)
            except Exception:  # noqa: BLE001
                pass
        self._tool_actor = None

    def _find_tip_def(self):
        """Return the def whose assembly is the deepest angular/non-angular part
        (the end effector of the arm chain), or None."""
        angular = [_d for _d in self._part_defs if _d.get("type") == "angular"]
        if angular:
            return angular[-1]
        return None

    # ------------------------------------------------------------------
    # VTK scene setup
    # ------------------------------------------------------------------
    def _build_view(self):
        if IN_DESIGNER:
            return QLabel("VTK unavailable in designer", self)
        import vtkmodules.qt as vtk_qt

        vtk_qt.QVTKRWIBase = "QWidget"
        from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

        widget = QVTKRenderWindowInteractor(self)

        self._renderer = vtk.vtkRenderer()
        self._renderer_window = widget.GetRenderWindow()
        self._renderer_window.AddRenderer(self._renderer)
        # MSAA is expensive when re-rendering ~25 fps; keep it off for this
        # diagnostic viewer.
        self._renderer_window.SetMultiSamples(0)

        camera = vtk.vtkCamera()
        camera.ParallelProjectionOn()
        camera.SetClippingRange(0.01, 10000.0)
        self._renderer.SetActiveCamera(camera)

        interactor = self._renderer_window.GetInteractor()
        interactor.SetInteractorStyle(vtk.vtkInteractorStyleTrackballCamera())

        # The frosted dialog is translucent; give the view itself an opaque
        # dark backdrop so the arm reads clearly.
        self._renderer.SetBackground(0.09, 0.11, 0.15)
        self._renderer.SetBackground2(0.03, 0.04, 0.06)
        self._renderer.GradientBackgroundOn()

        # Neutral axes (Z up) for orientation.
        axes = vtk.vtkAxesActor()
        axes.AxisLabelsOff()
        axes.SetTotalLength(200.0, 200.0, 200.0)
        self._renderer.AddActor(axes)

        # Simple ground grid in the XY plane.
        grid = self._make_grid()
        self._renderer.AddActor(grid)

        # Let the view receive keyboard/mouse for rotation.
        self._interactor = interactor
        return widget

    @staticmethod
    def _make_grid():
        """A simple XY ground grid so the arm's height/position reads clearly."""
        from vtkmodules.vtkFiltersSources import vtkLineSource
        from vtkmodules.vtkRenderingCore import vtkPolyDataMapper

        size = 300
        step = 50.0
        appender = vtk.vtkAppendPolyData()
        for i in range(-size, size + 1, int(step)):
            for (x0, y0, x1, y1) in (
                    (-size, i, size, i),     # lines parallel to X
                    (i, -size, i, size)):     # lines parallel to Y
                src = vtkLineSource()
                src.SetPoint1(x0, y0, 0.0)
                src.SetPoint2(x1, y1, 0.0)
                appender.AddInputConnection(src.GetOutputPort())

        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(appender.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.25, 0.27, 0.32)
        actor.GetProperty().SetAmbient(1.0)
        return actor

    # ------------------------------------------------------------------
    # Arm loading + live update
    # ------------------------------------------------------------------
    def _init_arm(self):
        """First-load entry point (called from ``_build_view_widget``)."""
        # A fresh (re)build starts in live-DH mode again.
        self._freeze_dh = False
        if getattr(self, "_dh_toggle", None) is not None:
            try:
                self._dh_toggle.setChecked(True)
            except Exception:  # noqa: BLE001
                pass
        self._load_arm()
        self._joint_positions = self._make_joint_reader()
        # Place the arm at its DH frames immediately (don't flash the raw
        # nested layout before the live timer's first tick).
        if self._parts3d:
            try:
                self._apply_dh_frames(self._joint_positions)
            except Exception:  # noqa: BLE001 - DH placement is best-effort
                pass
        # (Re)create and start the live-update timer.
        if self._timer is not None:
            self._timer.stop()
        self._timer = QTimer(self)
        self._timer.setInterval(40)   # ~25 fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _on_load(self):
        """Load button: pick a MACHINE_PARTS yaml (or re-load the current one)
        and rebuild the arm, re-framing the camera."""
        from PySide6.QtWidgets import QFileDialog

        start_dir = ""
        if self._machine_parts_file:
            start_dir = os.path.dirname(self._machine_parts_file)
        path, _ = QFileDialog.getOpenFileName(
            self, "Load robot arm YAML", start_dir,
            "YAML files (*.yml *.yaml);;All files (*)")
        if not path:
            return
        self._load_arm(path)

    def _load_arm(self, path=None):
        """(Re)load the arm from a MACHINE_PARTS yaml and build the assembly.

        If ``path`` is None the INI's ``[VTK] MACHINE_PARTS`` is used. Any
        previously loaded arm is removed first, and the camera is framed to
        the new arm.
        """
        try:
            if path:
                if not os.path.isfile(path):
                    self._status.setText("file not found")
                    return
                with open(path, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                # Relative model: paths in a MACHINE_PARTS yaml are resolved
                # against the running config dir (like the app does at runtime),
                # e.g. ``models/table.stl`` under the .ini dir.
                cfg_dir = os.path.dirname(os.getenv("INI_FILE_NAME", "") or "")
                if not cfg_dir:
                    cfg_dir = os.path.dirname(path)
            else:
                path, data, cfg_dir = _resolve_machine_parts()
                if not data:
                    self._status.setText("no [VTK] MACHINE_PARTS")
                    return

            # Drop the previous arm from the scene.
            if self._root_asm is not None:
                self._renderer.RemoveActor(self._root_asm)

            self._machine_parts_file = path
            self._machine_parts_data = data
            self._config_dir = cfg_dir

            # DH values now have a config dir to read the kinematics .hal from,
            # so (re)populate the panel (previously it had to wait for HAL).
            self._dh_load_values()

            root_asm = self._build_asm()
            self._root_asm = root_asm

            self._part_defs = []
            self._asm_to_def = {}
            self._actor_to_def = {}
            self._collect_defs(self._machine_parts_data["root"],
                               self._child_of(root_asm))
            self._parts3d = [_d["asm"]
                             for _d in self._part_defs if _d["type"] == "angular"]
            self._selected = None
            self._part_label.setText("click a part in the view…")

            self._renderer.AddActor(root_asm)
            self._apply_arm_orientation()
            self._add_origin_markers()
            self._add_tool_actor()

            # Re-point the pick list at the fresh assemblies/actors.
            self._rebuild_pick_list()

            if not self._parts3d:
                self._status.setText("loaded (no angular joints)")
            else:
                self._status.setText("live · %d joints" % len(self._parts3d))

            self._renderer_window.Render()
            self._refresh_camera_deferred()
        except Exception as exc:  # noqa: BLE001 - surface any loading problem
            tnc_main.LOG.exception("RobotArm: failed to load arm")
            self._status.setText("error: %s" % exc)

    def _refresh_camera_deferred(self):
        """Re-frame the camera on the next event-loop passes once the widget has
        a valid size (a fresh assembly / first show may not yet have one)."""
        from PySide6.QtCore import QTimer as _Timer
        _Timer.singleShot(0, self._frame_camera)
        _Timer.singleShot(60, self._frame_camera)
        _Timer.singleShot(250, self._frame_camera)

    def _frame_camera(self):
        """Frame the camera on the arm using the renderer's visible bounds."""
        if self._renderer is None:
            return
        try:
            bounds = list(self._renderer.ComputeVisiblePropBounds())
            # ComputeVisiblePropBounds returns (1,-1,1,-1,1,-1) when nothing
            # is visible; guard against that and against empty.
            if bounds[0] > bounds[1] or bounds[2] > bounds[3] \
                    or bounds[4] > bounds[5]:
                bounds = [-300.0, 300.0, -420.0, 80.0, 0.0, 580.0]
            self._renderer.ResetCamera(bounds)
            # A comfortable raised 3/4 view.
            cam = self._renderer.GetActiveCamera()
            if cam is not None:
                cx = (bounds[0] + bounds[1]) / 2.0
                cy = (bounds[2] + bounds[3]) / 2.0
                cz = (bounds[4] + bounds[5]) / 2.0
                diag = (max(bounds[1] - bounds[0], 1.0)
                        + max(bounds[3] - bounds[2], 1.0)
                        + max(bounds[5] - bounds[4], 1.0))
                dist = max(diag, 400.0)
                cam.SetFocalPoint(cx, cy, cz)
                cam.SetPosition(cx - dist, cy - dist, cz + dist * 0.6)
                cam.SetViewUp(0.0, 0.0, 1.0)
            self._renderer_window.Render()
        except Exception as exc:  # noqa: BLE001 - never let framing crash the view
            tnc_main.LOG.exception("RobotArm: camera framing failed")

    def _build_asm(self):
        """Build the MachinePartsASM from the (relative-path) yaml data.

        The stored ``self._machine_parts_data`` keeps the original relative
        ``model:`` paths so it can be written back unchanged on save. Here we
        build from a deep copy absolutized against the config dir so the STL
        files resolve regardless of the CWD.
        """
        from qtpyvcp.widgets.display_widgets.vtk_backplot.machine_actor \
            import MachinePartsASM

        build = copy.deepcopy(self._machine_parts_data)
        self._absolutize_models(build, self._config_dir or "")
        return MachinePartsASM(build)

    @classmethod
    def _absolutize_models(cls, node, base_dir):
        """Rewrite relative ``model:`` paths in-place against ``base_dir``."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "model" and isinstance(value, str) \
                        and not value.startswith("/"):
                    node[key] = os.path.normpath(os.path.join(base_dir, value))
                elif isinstance(value, (dict, list)):
                    cls._absolutize_models(value, base_dir)
        elif isinstance(node, list):
            for item in node:
                cls._absolutize_models(item, base_dir)

    @staticmethod
    def _make_joint_reader():
        """Return a callable that returns the live joint angles (degrees)."""
        import linuxcnc

        stat = linuxcnc.stat()

        def read(jnum):
            stat.poll()
            return stat.joint[int(jnum)]["input"]

        return read

    def _tick(self):
        if self._joint_positions is None:
            return
        # Don't pay for VTK renders while the dialog isn't visible (e.g.
        # parked behind the menu after Close).
        if not self.isVisible():
            return
        # In manual (frozen) mode we keep the applied part positions as-is, so
        # the DH renderer doesn't overwrite the user's preview each tick.
        if self._freeze_dh:
            return
        self._apply_dh_frames(self._joint_positions)
        self._renderer_window.Render()

    def _dh_link_params(self):
        """Return the (alpha, a, d) per-joint DH list in radians/mm for the live
        FK. Reads the real ``genserkins`` HAL pins when reachable (so live Apply
        DH edits take effect immediately), else the on-disk kinematics .hal
        (via the same parser the DH panel uses), else the Meca500 defaults."""
        vals = {}
        ok = 0
        hal = self._dh_hal()
        if hal is not None:
            try:
                for j in range(6):
                    vals["ALPHA-%d" % j] = hal.getp("genserkins.ALPHA-%d" % j)
                    vals["A-%d" % j] = hal.getp("genserkins.A-%d" % j)
                    vals["D-%d" % j] = hal.getp("genserkins.D-%d" % j)
                    ok += 1
            except Exception:  # noqa: BLE001 - fall back below
                ok = 0
        if ok == 6:
            return _dh_from_values(vals)
        file_vals = self._dh_file_values()
        if file_vals:
            return _dh_from_values(file_vals)
        return _dh_default_params()

    def _dh_world_frames(self, joints):
        """Return the world 4x4 matrices (as numpy arrays) of the 6 joint frames
        followed by the tool frame, computed from the DH params and the current
        joint angles (degrees). Uses the same composition genserkins does."""
        import numpy as np
        import math
        params = self._dh_link_params()
        frames = []
        acc = np.eye(4)
        for j in range(6):
            al, a, d = params[j]
            try:
                theta = math.radians(float(joints(j)))
            except Exception:  # noqa: BLE001 - joint not yet available
                theta = 0.0
            acc = acc @ np.array(_dh_link(al, a, d, theta)).reshape(4, 4)
            frames.append(acc.copy())
        frames.append(acc.copy())  # tool frame (after all six links)
        return frames

    def _apply_dh_frames(self, joints):
        """Place each arm part at its DH frame so the visual exactly tracks the
        machine kinematics (wrist included). Works around the nested-assembly
        limitation by computing each part's parent-relative placement from the
        DH world frames; the STL model origins land on the joint frames and the
        tool lands on the DH tool frame."""
        import numpy as np
        frames = self._dh_world_frames(joints)   # 6 joint + tool, current
        home = self._dh_world_frames(lambda _j: 0.0)  # 6 joint + tool, home
        world_by_asm = {}        # id(asm) -> world matrix of asm
        identity = np.eye(4)
        for _def in self._part_defs:
            asm = _def.get("asm")
            if asm is None:
                continue
            parent = _def.get("parent_asm")
            parent_world = world_by_asm.get(id(parent), identity)
            if _def.get("type") == "angular" and _def.get("joint") is not None:
                try:
                    jnum = int(_def["joint"])
                    current = frames[jnum]
                    home_frame = home[jnum]
                except (ValueError, IndexError):
                    world_by_asm[id(asm)] = parent_world
                    continue
                # Each STL is stored in home-pose world coordinates (its
                # geometry centre already sits on the DH home frame). Moving the
                # joint is therefore the *relative* rigid transform from the
                # home frame to the current frame; this rotates the part about
                # its own joint axis at the physical pivot rather than about its
                # bounding-box centre. The actor applies Translate(position),
                # which we cancel here; with position=[0,0,0] this is a no-op.
                pos = _def.get("position") or [0.0, 0.0, 0.0]
                target = current @ np.linalg.inv(home_frame)
                target = target.copy()
                target[:3, 3] = target[:3, 3] - target[:3, :3] @ np.array(pos)
                local = np.linalg.inv(parent_world) @ target
                world_by_asm[id(asm)] = target
                t = vtk.vtkTransform()
                t.SetMatrix(_np_to_vtk(local))
                asm.SetUserTransform(t)
            else:
                # non-angular parts (base/table) stay in their home-world pose.
                world_by_asm[id(asm)] = parent_world

        # tool at the DH tool frame, as a child of the tip (j5) assembly
        tip = self._find_tip_def()
        if self._tool_actor is not None and tip is not None \
                and id(tip.get("asm")) in world_by_asm:
            tip_world = world_by_asm[id(tip["asm"])]
            tool_world = frames[-1]   # DH tool frame
            # Centre the spindle geometry on the tool frame, like the links.
            spindle_center = [0.0, 0.0, 0.0]
            tip_node = tip.get("node") or {}
            if isinstance(tip_node.get("model"), str):
                pass  # the spindle actor is its own model, handled below
            try:
                import os as _os
                spindle_path = _os.path.join(self._config_dir or "", "models", "spindle.stl")
                if _os.path.isfile(spindle_path):
                    spindle_center = list(_stl_center_abs(spindle_path))
            except Exception:  # noqa: BLE001 - best-effort
                pass
            tw = tool_world.copy()
            tw[:3, 3] = tw[:3, 3] - tool_world[:3, :3] @ np.array(spindle_center)
            local = np.linalg.inv(tip_world) @ tw
            t = vtk.vtkTransform()
            t.SetMatrix(_np_to_vtk(local))
            self._tool_actor.SetUserTransform(t)

    # ------------------------------------------------------------------
    # Window chrome (glass/drag) - same treatment as workspace dialog
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        top, bottom = tnc_main.glass_fill()
        path = tnc_main.glass_path(rect)

        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        sheen = QLinearGradient(0, rect.y(), 0, rect.y() + rect.height() * 0.45)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 30))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(sheen)
        painter.drawPath(path)

        glow = QRadialGradient(
            QPointF(rect.x() + rect.width() * 0.2,
                    rect.y() + rect.height() * 0.2),
            rect.width() * 0.9)
        glow_start, glow_end = tnc_main.glass_glow()
        glow.setColorAt(0.0, glow_start)
        glow.setColorAt(1.0, glow_end)
        painter.setBrush(glow)
        painter.drawPath(path)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(tnc_main.glass_border(), 1))
        painter.drawPath(path)

        # resize grip in the bottom-right corner
        grip = 16
        x = self.width() - self._resize_grip - 4
        y = self.height() - self._resize_grip - 4
        pen = QPen(QColor(255, 255, 255, 120), 2)
        for i in range(3):
            s = grip - i * 5
            painter.setPen(pen)
            painter.drawLine(x + grip - s, y + grip,
                             x + grip, y + grip - s)
        painter.end()

    def _in_resize_grip(self, pos):
        """True when ``pos`` is over the bottom-right resize corner."""
        g = self._resize_grip
        return (pos.x() >= self.width() - g and pos.y() >= self.height() - g)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._in_resize_grip(event.position()):
                self._resize_offset = (
                    event.globalPosition().toPoint()
                    - QPointF(self.width(), self.height()).toPoint())
                self._drag_offset = None
            else:
                self._drag_offset = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft())
                self._resize_offset = None
            event.accept()

    def mouseMoveEvent(self, event):
        if self._resize_offset is not None:
            p = event.globalPosition().toPoint()
            self.resize(max(self.minimumWidth(), p.x() - self._resize_offset.x()),
                        max(self.minimumHeight(), p.y() - self._resize_offset.y()))
            event.accept()
        elif (event.buttons() & Qt.LeftButton) and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        self._resize_offset = None
        super().mouseReleaseEvent(event)

    def close_method(self):
        self._teardown_view()
        self.reject()
        self.close()

    def closeEvent(self, event):
        """Stop the live-update timer and release the VTK view on any close
        (Close button, Escape, ...) so the next launch builds a fresh render
        window instead of a black one."""
        self._teardown_view()
        super().closeEvent(event)


# Backward-compatible alias: the original dialog class name. Any external
# reference to ``RobotArmDialog`` (e.g. an older config.yml) still resolves.
RobotArmDialog = RobotWorkspaceDialog
