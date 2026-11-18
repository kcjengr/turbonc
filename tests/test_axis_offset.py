"""Unit tests for the pure G10 command builder in tnc.dialogs.axis_offset.

The dialog module imports PySide6 and QtPyVCP at module level. Those imports
are stubbed here so the tests can run in a plain Python CI job without Qt or
a LinuxCNC installation.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "tnc" / "dialogs" / "axis_offset.py"


class _Stub:
    def __init__(self, *args, **kwargs):
        pass


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    module.__path__ = []
    sys.modules[name] = module
    return module


def _load_module():
    pyside = _stub("PySide6")
    qtcore = _stub("PySide6.QtCore", Qt=types.SimpleNamespace(
        AlignmentFlag=types.SimpleNamespace(AlignCenter=0)))
    qtgui = _stub("PySide6.QtGui", QFont=_Stub)
    qtwidgets = _stub(
        "PySide6.QtWidgets",
        QCheckBox=_Stub, QComboBox=_Stub, QDoubleSpinBox=_Stub,
        QGridLayout=_Stub, QHBoxLayout=_Stub, QLabel=_Stub,
        QPushButton=_Stub, QVBoxLayout=_Stub)
    pyside.QtCore = qtcore
    pyside.QtGui = qtgui
    pyside.QtWidgets = qtwidgets

    qtpyvcp = _stub("qtpyvcp")
    actions = _stub("qtpyvcp.actions")
    machine_actions = _stub("qtpyvcp.actions.machine_actions",
                            issue_mdi=lambda _cmd: None)
    utilities = _stub("qtpyvcp.utilities",
                      logger=types.SimpleNamespace(
                          getLogger=lambda _name: types.SimpleNamespace(
                              info=lambda *a, **k: None,
                              exception=lambda *a, **k: None)))
    widgets = _stub("qtpyvcp.widgets")
    dialogs = _stub("qtpyvcp.widgets.dialogs")
    base_dialog = _stub("qtpyvcp.widgets.dialogs.base_dialog",
                        BaseDialog=_Stub)
    qtpyvcp.actions = actions
    qtpyvcp.utilities = utilities
    qtpyvcp.widgets = widgets
    actions.machine_actions = machine_actions
    widgets.dialogs = dialogs
    dialogs.base_dialog = base_dialog

    spec = importlib.util.spec_from_file_location("axis_offset_under_test",
                                                  MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildOffsetCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()
        cls.build = staticmethod(cls.mod.build_offset_command)

    def test_touch_off_at_tool_tip(self):
        self.assertEqual(
            self.build("L20", "P1", "Z", 10.0, tool_offset=2.5),
            "G10 L20 P1 Z10.0000")

    def test_touch_off_ignores_tlo_when_unchecked(self):
        self.assertEqual(
            self.build("L20", "P1", "Z", 10.0, tool_offset=2.5,
                       apply_tool_offset=False),
            "G10 L20 P1 Z7.5000")

    def test_raw_offset_never_subtracts_tlo(self):
        self.assertEqual(
            self.build("L2", "P1", "Z", 10.0, tool_offset=2.5,
                       apply_tool_offset=False),
            "G10 L2 P1 Z10.0000")

    def test_only_z_touch_off_uses_tlo(self):
        self.assertEqual(
            self.build("L20", "P1", "X", 10.0, tool_offset=2.5,
                       apply_tool_offset=False),
            "G10 L20 P1 X10.0000")

    def test_axis_is_uppercased(self):
        self.assertEqual(self.build("L20", "P1", "z", 0),
                         "G10 L20 P1 Z0.0000")

    def test_systems_cover_current_and_all_wcs(self):
        keys = [key for key, _label in self.mod.SYSTEMS]
        self.assertEqual(keys, ["P%d" % i for i in range(10)])


if __name__ == "__main__":
    unittest.main()
