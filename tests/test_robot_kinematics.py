"""Checks that the robot sim's kinematics agree everywhere.

The arm's DH table lives in ``models/thor_dh.py`` but is consumed by three
hand-maintained files:

    robot_thor-kinematics.hal   genserkins ALPHA/A/D
    models/robot_thor.yml       per-joint ``mount`` used by the 3D renderer
    sim-robot_thor.ini          [TRAJ]HOME = FK(joint HOME)

When those drift (they did: the HAL ran D[5]=119.284 while thor_dh.py had
105.5) the controller's IK and the jog reach guard disagree, so ordinary
teleop jogs fault the machine with "kinematicsInverse failed" right after the
guard trips.  These tests keep the three files in step.
"""

import configparser
import importlib.util
import math
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "linuxcnc" / "configs" / "sim.robot_thor"
MODELS = CONFIG / "models"
TOL = 1e-3


def _thor_dh():
    spec = importlib.util.spec_from_file_location("thor_dh", MODELS / "thor_dh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ini():
    parser = configparser.ConfigParser(strict=False,
                                       inline_comment_prefixes=("#", ";"))
    parser.read(CONFIG / "sim-robot_thor.ini")
    return parser


def _parts():
    with open(MODELS / "robot_thor.yml") as handle:
        data = yaml.safe_load(handle)
    out = {}

    def walk(node):
        for value in node.values():
            if isinstance(value, dict):
                if value.get("joint") is not None:
                    out.setdefault(int(value["joint"]), value)
                walk(value)

    walk(data)
    return out


class KinematicsConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.dh = _thor_dh()

    def test_hal_matches_dh_table(self):
        text = (CONFIG / "robot_thor-kinematics.hal").read_text()
        values = {}
        for line in text.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[0] == "setp":
                try:
                    values[fields[1]] = float(fields[2])
                except ValueError:
                    continue        # hex values such as max-iterations
        for j in range(6):
            with self.subTest(joint=j):
                self.assertAlmostEqual(
                    values["genserkins.ALPHA-%d" % j],
                    math.radians(self.dh.ALPHA[j]), places=6)
                self.assertAlmostEqual(values["genserkins.A-%d" % j],
                                       self.dh.A[j], delta=TOL)
                self.assertAlmostEqual(values["genserkins.D-%d" % j],
                                       self.dh.D[j], delta=TOL)

    def test_parts_mount_matches_dh_table(self):
        parts = _parts()
        for j in range(6):
            with self.subTest(joint=j):
                mount = parts[j]["mount"]
                self.assertAlmostEqual(mount[0], self.dh.A[j], delta=TOL)
                # mount = [a, -sin(alpha)*d, cos(alpha)*d, alpha, 0, 0]
                alpha = math.radians(self.dh.ALPHA[j])
                self.assertAlmostEqual(mount[1],
                                       -math.sin(alpha) * self.dh.D[j], delta=TOL)
                self.assertAlmostEqual(mount[2],
                                       math.cos(alpha) * self.dh.D[j], delta=TOL)
                self.assertAlmostEqual(mount[3], self.dh.ALPHA[j], delta=TOL)

    def test_traj_home_is_fk_of_joint_home(self):
        parser = _ini()
        home = [float(v) for v in parser.get("TRAJ", "HOME").split()[:6]]
        tcp = self.dh.dh_frames(self.dh.machine_home())[5]
        rpy = self.dh.rpy_deg(tcp[:3, :3])
        for got, want in zip(home[:3], tcp[:3, 3]):
            self.assertAlmostEqual(got, float(want), delta=0.01)
        for got, want in zip(home[3:], rpy):
            self.assertAlmostEqual(got, float(want), delta=0.01)

    def test_joint_home_matches_dh(self):
        parser = _ini()
        expected = self.dh.machine_home()
        for j in range(6):
            with self.subTest(joint=j):
                self.assertAlmostEqual(
                    parser.getfloat("JOINT_%d" % j, "HOME"), expected[j], delta=TOL)

    def test_joints_have_soft_limits(self):
        parser = _ini()
        for j in range(6):
            with self.subTest(joint=j):
                lo = parser.getfloat("JOINT_%d" % j, "MIN_LIMIT")
                hi = parser.getfloat("JOINT_%d" % j, "MAX_LIMIT")
                home = parser.getfloat("JOINT_%d" % j, "HOME")
                self.assertLess(lo, hi)
                self.assertTrue(lo <= home <= hi,
                                "HOME %s outside %s..%s" % (home, lo, hi))


if __name__ == "__main__":
    unittest.main()
