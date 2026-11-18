"""Checks for the sim.robot_thor machine-parts definition.

QtPyVCP's renderer only animates nodes that are ``type: angular`` *and* carry
a ``joint``/``mount`` pair; every other node is drawn at its ``position`` and
stays there.  That means a sub-mesh emitted for an extra material group has to
copy its link's joint metadata, otherwise it keeps the DH-zero pose while its
link rotates and the arm falls apart.
"""

import importlib.util
import json
import struct
import unittest
from pathlib import Path

try:
    import numpy  # noqa: F401  (gen_thor_config.py -> thor_dh.py is numpy based)
    import yaml
except ImportError:      # pragma: no cover - CI installs PyYAML and NumPy
    numpy = None
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "linuxcnc" / "configs" / "sim.robot_thor" / "models"
PARTS_YML = MODELS / "robot_thor.yml"


def _walk(node, out, parent=None):
    """Collect ``(node, parent)`` pairs from a yaml tree or ``build_tree()``."""
    for value in node.values():
        if not isinstance(value, dict) or "id" not in value:
            continue
        out.append((value, parent))
        kids = value.get("children")
        if not isinstance(kids, dict):
            kids = {key: child for key, child in value.items()
                    if isinstance(child, dict) and "id" in child}
        _walk(kids, out, value)


def _load_parts():
    with open(PARTS_YML) as handle:
        data = yaml.safe_load(handle)
    parts = []
    _walk(data, parts)
    return parts


def _glb_bounds(path):
    """((minx, miny, minz), (maxx, maxy, maxz)) from the glTF accessors."""
    with open(path, "rb") as handle:
        data = handle.read()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + length].decode("utf-8"))
    lows, highs = [], []
    for accessor in document.get("accessors", []):
        if accessor.get("type") == "VEC3" and "min" in accessor:
            lows.append(accessor["min"])
            highs.append(accessor["max"])
    low = tuple(min(values[i] for values in lows) for i in range(3))
    high = tuple(max(values[i] for values in highs) for i in range(3))
    return low, high


@unittest.skipIf(yaml is None or numpy is None,
                 "PyYAML and NumPy are required for the machine-parts checks")
class MachinePartsTest(unittest.TestCase):
    def setUp(self):
        self.parts = _load_parts()

    def test_every_joint_node_is_animatable(self):
        for node, _parent in self.parts:
            joint = node.get("joint")
            if joint is None:
                continue
            with self.subTest(node=node.get("id")):
                self.assertEqual(node.get("type"), "angular")
                self.assertIsNotNone(node.get("mount"))
                self.assertIsNotNone(node.get("axis"))

    def test_children_of_moving_links_share_the_joint(self):
        # Extra material groups are emitted as ``<parent>_<n>`` children of the
        # link they belong to and must rotate with it.
        for node, parent in self.parts:
            if parent is None or parent.get("type") != "angular":
                continue
            if not str(node.get("id", "")).startswith(parent.get("id") + "_"):
                continue
            with self.subTest(node=node.get("id"), parent=parent.get("id")):
                self.assertEqual(node.get("joint"), parent.get("joint"))
                self.assertEqual(node.get("mount"), parent.get("mount"))

    def test_models_exist(self):
        for node, _parent in self.parts:
            model = node.get("model")
            if not model:
                continue
            with self.subTest(node=node.get("id"), model=model):
                self.assertTrue((MODELS / Path(model).name).is_file(), model)

    def test_meshes_are_exported_z_up(self):
        """The GLBs are baked into the renderer's Y-up frame ("Z+ up").

        machine.py hands QML a transform that is already converted to the Qt
        Quick 3D Y-up scene frame, so the vertices must be too - that is what
        Blender's "+Y Up" glTF option writes.  If a re-export drops that
        option, every part is drawn rotated 90 degrees about X.
        """
        low, high = _glb_bounds(MODELS / "table.glb")
        extents = [high[i] - low[i] for i in range(3)]
        # The floor plate is flat in the up axis: Y is its smallest extent.
        self.assertEqual(extents.index(min(extents)), 1,
                         "table.glb is not Y-up (extents %s)" % extents)

        low, high = _glb_bounds(MODELS / "link1.glb")
        # link1 is the upright base column: centred on X/Z and above Y=0.
        self.assertAlmostEqual((low[0] + high[0]) / 2.0, 0.0, delta=5.0)
        self.assertAlmostEqual((low[2] + high[2]) / 2.0, 0.0, delta=5.0)
        self.assertGreater(low[1], 0.0)

    def test_joint_count_and_order(self):
        joints = [node["joint"] for node, _parent in self.parts
                  if node.get("joint") is not None]
        self.assertEqual(sorted(set(joints)), [0, 1, 2, 3, 4, 5])

    def test_generator_marks_extra_material_groups_angular(self):
        spec = importlib.util.spec_from_file_location(
            "gen_thor_config", MODELS / "gen_thor_config.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        tree = module.build_tree()
        nodes = []
        _walk({"root": tree}, nodes)
        by_id = {node["id"]: node for node, _parent in nodes}
        # link2/link4 have several materials upstream -> split sub-meshes.
        for child_id, parent_id in (("link2_1", "link2"),
                                    ("link4_1", "link4")):
            self.assertIn(child_id, by_id)
            self.assertEqual(by_id[child_id]["type"], "angular")
            self.assertEqual(by_id[child_id]["joint"], by_id[parent_id]["joint"])
            self.assertEqual(by_id[child_id]["mount"], by_id[parent_id]["mount"])
        # The spindle hangs off link 6 and must rotate with it.
        self.assertEqual(by_id["spindle"]["type"], "angular")
        self.assertEqual(by_id["spindle"]["joint"], 5)
        # The static base keeps its sub-mesh static.
        self.assertEqual(by_id["base_1"]["type"], "static")


if __name__ == "__main__":
    unittest.main()
