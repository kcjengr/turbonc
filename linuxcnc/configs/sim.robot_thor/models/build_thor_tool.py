#!/usr/bin/env python3
"""Build the active tool mesh (models/tool.stl) from the tool table.

    python3 models/build_thor_tool.py [--tool N] [--db mill_tools.db]

Reads the tool's **diameter and X/Y/Z/A/B/C offsets** from the SQLite tool
database and generates a parametric cutting tool clamped in the spindle collet
(gauge plane = build_thor_spindle.GAUGE_Z):

  * Z offset  -> nose-to-tip length past the gauge plane (the tool sticks out
    of the collet); the machine's controlled point is the spindle NOSE, so the
    tool-table Z is measured from there, not from the flange,
  * diameter -> cutting radius,
  * X/Y offset -> lateral shift of the tool off the spindle axis,
  * A/B/C offset -> tool tilt/rotation about the gauge point,
  * remark -> tip style (facemill / drill / V-bit / endmill).

The mesh is baked onto the renderer home pose, so the VTK machine-parts
renderer animates it with link 6 exactly like the spindle.  Re-run this and
gen_thor_config.py after changing the tool in the spindle, then restart.

With no --tool, the tool flagged `in_use` in the table is used.
"""
import argparse
import math
import os
import sqlite3
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import thor_dh as dh  # noqa: E402
from build_thor_spindle import GAUGE_Z, _ring  # noqa: E402

SEG = 48
COLS = ("diameter", "x_offset", "y_offset", "z_offset",
        "a_offset", "b_offset", "c_offset", "remark")


def cylinder(z0, z1, r0, r1=None, closed=(True, True)):
    r1 = r0 if r1 is None else r1
    a = _ring(z0, r0)
    b = _ring(z1, r1)
    tris = []
    for i in range(SEG):
        j = (i + 1) % SEG
        tris += [(a[i], a[j], b[j]), (a[i], b[j], b[i])]
    if closed[0]:
        c = np.array([0, 0, z0])
        for i in range(SEG):
            j = (i + 1) % SEG
            tris.append((c, a[j], a[i]))
    if closed[1]:
        c = np.array([0, 0, z1])
        for i in range(SEG):
            j = (i + 1) % SEG
            tris.append((c, b[i], b[j]))
    return tris


def build(t):
    d = max(float(t["diameter"]), 0.4)
    r = d / 2.0
    z0 = GAUGE_Z - 4.0                       # slightly inside the collet
    # The tool-table Z offset is measured from the controlled point (the spindle
    # NOSE at GAUGE_Z), so the tip sits z_offset beyond the gauge plane.
    tip = max(GAUGE_Z + float(t["z_offset"]), z0 + 5.0)
    vis = tip - z0
    kind = (t["remark"] or "").lower()

    if "facemill" in kind:
        body = tip - min(0.35 * vis, 1.2 * d, 14.0)
        tris = cylinder(z0, body, min(r, 10.0)) + cylinder(body, tip, r)
    elif "v-bit" in kind or "vbit" in kind or "chamfer" in kind:
        cone = min(0.5 * vis, 3.0 * d)
        tris = (cylinder(z0, tip - cone, min(r, 6.0))
                + cylinder(tip - cone, tip, r, 0.1, closed=(True, False)))
    elif "drill" in kind:
        pt = min(0.3 * r, 6.0)
        tris = (cylinder(z0, tip - 2 * pt, r)
                + cylinder(tip - 2 * pt, tip, r, 0.1, closed=(True, False)))
    else:  # endmill / default: shank then cutting flutes of the table diameter
        flutes = min(0.6 * vis, 4.0 * d)
        shank = max(r * 0.7, 0.6)
        tris = (cylinder(z0, tip - flutes, shank)
                + cylinder(tip - flutes, tip, r))

    # A/B/C offsets: rotate the tool about the gauge point (x=y=z=GAUGE).
    a, b, c = (math.radians(float(t[k])) for k in ("a_offset", "b_offset", "c_offset"))
    if abs(a) + abs(b) + abs(c) > 1e-9:
        ca, sa = math.cos(a), math.sin(a)
        cb, sb = math.cos(b), math.sin(b)
        cc, sc = math.cos(c), math.sin(c)
        R = (np.array([[cc, -sc, 0], [sc, cc, 0], [0, 0, 1]])
             @ np.array([[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]])
             @ np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]]))
        piv = np.array([0.0, 0.0, GAUGE_Z])
        tris = [(R @ (p - piv)) + piv for tri in tris for p in tri]
        tris = [tuple(tris[i:i + 3]) for i in range(0, len(tris), 3)]

    # X/Y offsets: lateral shift off the spindle axis.
    dx, dy = float(t["x_offset"]), float(t["y_offset"])
    if dx or dy:
        tris = [tuple(p + np.array([dx, dy, 0.0]) for p in tri) for tri in tris]
    return tris


def load_tool(db, tool_no):
    c = sqlite3.connect(db)
    try:
        if tool_no is None:
            row = c.execute(
                "select %s from tool where in_use=1 order by tool_no limit 1"
                % ", ".join(COLS)).fetchone()
            if row is None:
                row = c.execute("select %s from tool order by tool_no limit 1"
                                % ", ".join(COLS)).fetchone()
        else:
            row = c.execute("select %s from tool where tool_no=?" % ", ".join(COLS),
                            (tool_no,)).fetchone()
    finally:
        c.close()
    if row is None:
        raise SystemExit("tool not found in %s" % db)
    t = dict(zip(COLS, row))
    t["remark"] = t["remark"] or ""
    return t


def write_stl(path, pts, tris):
    with open(path, "wb") as fh:
        fh.write(b"thor tool - generated by build_thor_tool.py".ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(tris)))
        for tri in tris:
            p0, p1, p2 = (pts[i] for i in tri)
            n = np.cross(p1 - p0, p2 - p0)
            ln = np.linalg.norm(n)
            n = n / ln if ln > 1e-12 else np.zeros(3)
            fh.write(struct.pack("<3f", *n))
            for p in (p0, p1, p2):
                fh.write(struct.pack("<3f", *p))
            fh.write(struct.pack("<H", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", type=int, default=None,
                    help="tool number; default = the in_use tool")
    ap.add_argument("--db", default=os.path.join(HERE, "..", "mill_tools.db"))
    args = ap.parse_args()

    t = load_tool(args.db, args.tool)
    local = build(t)
    qref = [-dh.JOINT_OFFSET[j] for j in range(6)]
    world = dh.urdf_frames(qref)[5]
    pts = np.array([(world @ np.append(v, 1.0))[:3] for tri in local for v in tri])
    idx = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(local))]
    out = os.path.join(HERE, "tool.stl")
    write_stl(out, pts, idx)
    mn, mx = pts.min(0), pts.max(0)
    print("T? %-20s dia=%.2f len(Z)=%.1f  offsets XYZ=(%.1f,%.1f,%.1f) ABC=(%.1f,%.1f,%.1f)"
          % (t["remark"] or "tool", t["diameter"], t["z_offset"],
             t["x_offset"], t["y_offset"], t["z_offset"],
             t["a_offset"], t["b_offset"], t["c_offset"]))
    print("   -> %s  x[%.1f,%.1f] y[%.1f,%.1f] z[%.1f,%.1f] (%d tris)"
          % (out, mn[0], mx[0], mn[1], mx[1], mn[2], mx[2], len(local)))


if __name__ == "__main__":
    main()
