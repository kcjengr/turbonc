#!/usr/bin/env python3
"""Assemble the sim.robot_thor arm from the fetched STLs -> models/asm_thor.blend.

    /home/turboss/Apps/Blender/blender --factory-startup --background \\
        --python models/build_thor_arm.py

The STLs (base/link1..6/spindle/table) are authored in the renderer home pose
(joint feedback = 0), so importing them at the origin already gives an assembled
arm.  This script adds a modified-DH joint rig on top:

  * one Empty per joint on its DH frame (matrix_basis = the DH link matrix),
  * each link mesh parented to its joint Empty,
  * the spindle parented to the last joint Empty,

so rotating ``A1..A6`` reproduces genserkins exactly.  The arm is posed at the
machine HOME ([0,100,20,180,100,180] deg) and a Cycles preview is rendered to
models/asm_thor.png.
"""
import math
import os
import sys

import numpy as np
import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import thor_dh as dh  # noqa: E402

# part mesh name -> parent joint index (None = static / root)
PART_JOINT = {
    "table": None, "base": None,
    "link1": 0, "link2": 1, "link3": 2,
    "link4": 3, "link5": 4, "link6": 5,
    "spindle": 5,
}
PARTS = ["table", "base"] + ["link%d" % i for i in range(1, 7)] + ["spindle"]

LINK = (0.80, 0.82, 0.85)
SPINDLE = (0.42, 0.43, 0.45)
DARK = (0.16, 0.16, 0.17)
COLOR = {"table": DARK, "base": LINK, "spindle": SPINDLE}
for i in range(1, 7):
    COLOR["link%d" % i] = LINK


def to_bl(m):
    """numpy 4x4 -> mathutils.Matrix."""
    return Matrix([[float(m[r][c]) for c in range(4)] for r in range(4)])


def link_matrix(j, theta_deg):
    return to_bl(dh.dh_link(dh.ALPHA[j], dh.A[j], dh.D[j], theta_deg))


def import_stl(path, name):
    before = set(bpy.data.objects)
    try:
        bpy.ops.wm.stl_import(filepath=path)
    except Exception:
        bpy.ops.import_mesh.stl(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    ob = new[0]
    ob.name = name
    return ob


def material(name, color):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1.0)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Metallic"].default_value = 0.6
        bsdf.inputs["Roughness"].default_value = 0.4
    return m


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    # --- import the authored meshes (world coords == renderer home pose) -----
    meshes = {}
    for p in PARTS:
        path = os.path.join(HERE, p + ".stl")
        if not os.path.exists(path):
            raise SystemExit("missing %s (run fetch_thor_meshes.py first)" % path)
        ob = import_stl(path, p)
        ob.data.materials.clear()
        ob.data.materials.append(material(p, COLOR[p]))
        meshes[p] = ob

    # --- joint rig: A1..A6 empties on the modified-DH frames -----------------
    root = bpy.data.objects.new("Thor", None)
    root.empty_display_size = 60
    scene.collection.objects.link(root)

    joints = []
    for j in range(6):
        e = bpy.data.objects.new("A%d" % (j + 1), None)
        e.empty_display_size = 40
        scene.collection.objects.link(e)
        e.parent = root if j == 0 else joints[j - 1]
        e.matrix_parent_inverse = Matrix.Identity(4)
        e.matrix_basis = link_matrix(j, 0.0)   # rest = DH zero pose
        joints.append(e)

    # --- parent each mesh to its joint, keeping its authored world pose ------
    # The rest frame of joint j is H_j = dh_frames(0)[j]; a mesh with vertices
    # already in world coords must sit at transform C_j @ inv(H_j) under pose.
    # Parent inverse = inv(H_j) (rest), basis = identity, so
    #     matrix_world = A_j.world @ inv(H_j) = C_j @ inv(H_j).
    H = dh.dh_frames([0.0] * 6)
    for p, jn in PART_JOINT.items():
        ob = meshes[p]
        parent = root if jn is None else joints[jn]
        ob.parent = parent
        ob.matrix_parent_inverse = (Matrix.Identity(4) if jn is None
                                    else to_bl(np.linalg.inv(H[jn])))
        ob.matrix_basis = Matrix.Identity(4)

    # --- pose the rig at the machine HOME -----------------------------------
    home = dh.machine_home()
    expected = {}
    for j in range(6):
        joints[j].matrix_basis = link_matrix(j, home[j])

    # expected world transform of each mesh: C_j @ inv(H_j) (renderer rule)
    C = dh.dh_frames(home)
    for p, jn in PART_JOINT.items():
        expected[p] = np.eye(4) if jn is None else C[jn] @ np.linalg.inv(H[jn])

    bpy.context.view_layer.update()

    # --- verify the rig equals the renderer's transform ---------------------
    worst = 0.0
    for p, jn in PART_JOINT.items():
        got = np.array([[meshes[p].matrix_world[r][c] for c in range(4)]
                        for r in range(4)])
        err = np.abs(got - expected[p]).max()
        worst = max(worst, err)
        print("  %-8s joint=%-4s  max|matrix error|=%.2e"
              % (p, jn, err))
    print("rig verification: worst error = %.2e mm (float32 mesh precision)"
          % worst)

    # --- camera / light / world ---------------------------------------------
    # frame the arm itself (skip the oversized floor table)
    pts = []
    for p, ob in meshes.items():
        if p == "table":
            continue
        pts += [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts),
                 min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts),
                 max(p.z for p in pts)))
    center = (lo + hi) / 2.0
    radius = max((hi - lo)) or 1.0

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 50
    # scene is in mm; push the far clip well past the ~2-3 m camera distance
    cam_data.clip_start = 1.0
    cam_data.clip_end = 100000.0
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    center.z += radius * 0.15
    direction = Vector((1.0, -1.0, 0.45)).normalized()
    cam.location = center + direction * radius * 2.4
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    sun_data = bpy.data.lights.new("Sun", type="SUN")
    sun_data.energy = 4.0
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = (math.radians(55), math.radians(15), math.radians(-40))
    scene.collection.objects.link(sun)

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.05, 0.05, 0.06, 1.0)
        bg.inputs[1].default_value = 0.8
    scene.world = world

    out = os.path.join(HERE, "asm_thor.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("wrote", out)

    # --- headless preview render (Cycles CPU) -------------------------------
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 32
    scene.render.resolution_x = 960
    scene.render.resolution_y = 720
    scene.render.filepath = os.path.join(HERE, "asm_thor.png")
    bpy.ops.render.render(write_still=True)
    print("wrote", scene.render.filepath)


if __name__ == "__main__":
    main()
