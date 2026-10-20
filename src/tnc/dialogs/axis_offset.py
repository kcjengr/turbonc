"""Per-axis "set offset" dialog for the DRO.

Opened from the small button placed before each axis letter in the DRO.  It
lets the operator either

  * touch off - enter the position the axis should read, which adjusts the
    active work offset (``G10 L20``), or
  * set the raw coordinate-system offset value directly (``G10 L2``),

for a chosen coordinate system (P0 = the currently active WCS, or P1..P9).
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from qtpyvcp.actions.machine_actions import issue_mdi
from qtpyvcp.utilities import logger
from qtpyvcp.widgets.dialogs.base_dialog import BaseDialog

LOG = logger.getLogger(__name__)


def build_offset_command(mode, system, axis, value, *,
                         tool_offset=None, apply_tool_offset=True):
    """Build the ``G10`` MDI command for an axis offset.

    For a Z touch-off (``L20``) with *apply_tool_offset* false, the entered
    value is reduced by the active tool-length offset so the resulting
    coordinate-system offset does not include TLO (the zero is set at the
    spindle reference rather than the tool tip).
    """
    axis = str(axis).upper()
    value = float(value)
    if (mode == "L20" and axis == "Z" and not apply_tool_offset
            and tool_offset is not None):
        value -= float(tool_offset)
    return "G10 %s %s %s%.4f" % (mode, system, axis, value)

# (data, label)
SYSTEMS = [
    ("P0", "Current (active WCS)"),
    ("P1", "P1  G54"), ("P2", "P2  G55"), ("P3", "P3  G56"),
    ("P4", "P4  G57"), ("P5", "P5  G58"), ("P6", "P6  G59"),
    ("P7", "P7  G59.1"), ("P8", "P8  G59.2"), ("P9", "P9  G59.3"),
]


def active_wcs():
    """1-based active coordinate system number (1 == G54)."""
    try:
        from qtpyvcp.plugins import getPlugin
        st = getPlugin('status').stat
        st.poll()
        return max(1, min(9, int(st.g5x_index)))
    except Exception:
        return 1


def active_tool_z_offset():
    """Current applied tool-length offset for Z (0.0 when unavailable)."""
    try:
        from qtpyvcp.plugins import getPlugin
        st = getPlugin('status').stat
        st.poll()
        return float(st.tool_offset[2])
    except Exception:
        return 0.0


class AxisOffsetDialog(BaseDialog):
    def __init__(self, axis, parent=None):
        super(AxisOffsetDialog, self).__init__(parent=parent, stay_on_top=True)

        self.axis = str(axis).upper()
        self.setWindowTitle("Set %s offset" % self.axis)

        title = QLabel("Set %s offset" % self.axis)
        title.setFont(QFont("3270 Semi-Condensed", 16))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Touch off  -  set the position it reads", "L20")
        self.mode_combo.addItem("Set offset  -  raw coordinate-system value", "L2")

        self.system_combo = QComboBox()
        for key, label in SYSTEMS:
            self.system_combo.addItem(label, key)

        self.value_input = QDoubleSpinBox()
        self.value_input.setDecimals(4)
        self.value_input.setRange(-999999.0, 999999.0)
        self.value_input.setSingleStep(0.1)
        self.value_input.setValue(0.0)
        self.value_input.setMinimumWidth(160)

        grid = QGridLayout()
        grid.addWidget(QLabel("Action:"), 0, 0)
        grid.addWidget(self.mode_combo, 0, 1)
        grid.addWidget(QLabel("System:"), 1, 0)
        grid.addWidget(self.system_combo, 1, 1)
        grid.addWidget(QLabel("Value:"), 2, 0)
        grid.addWidget(self.value_input, 2, 1)

        # Tool-length-offset control is only meaningful for the Z touch-off.
        self.tool_offset_cb = None
        if self.axis == "Z":
            self.tool_offset_cb = QCheckBox("Apply tool Z offset (G43/TLO)")
            self.tool_offset_cb.setChecked(True)
            self.tool_offset_cb.setToolTip(
                "Checked: touch off at the tool tip (current tool length offset "
                "is included).\nUnchecked: set the zero ignoring the tool "
                "length offset (spindle reference).")
            grid.addWidget(self.tool_offset_cb, 3, 0, 1, 2)

        set_btn = QPushButton("Set")
        set_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        set_btn.clicked.connect(self._apply)
        cancel_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(set_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(grid)
        layout.addLayout(buttons)

    def _resolved_system(self):
        system = self.system_combo.currentData()
        if system == "P0":
            system = "P%d" % active_wcs()
        return system

    def _apply(self):
        mode = self.mode_combo.currentData()          # L20 or L2
        system = self._resolved_system()
        value = self.value_input.value()
        apply_tool_offset = (self.tool_offset_cb is None
                             or self.tool_offset_cb.isChecked())
        cmd = build_offset_command(
            mode, system, self.axis, value,
            tool_offset=active_tool_z_offset(),
            apply_tool_offset=apply_tool_offset)
        try:
            issue_mdi(cmd)
            LOG.info("axis offset set: %s", cmd)
        except Exception:
            LOG.exception("failed to issue %s", cmd)
        self.accept()

    def showEvent(self, event):
        super(AxisOffsetDialog, self).showEvent(event)
        self.value_input.setFocus()
        self.value_input.selectAll()
