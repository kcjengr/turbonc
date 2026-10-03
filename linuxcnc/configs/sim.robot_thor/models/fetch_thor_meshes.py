#!/usr/bin/env python3
"""Fetch the Thor per-link COLLADA meshes -> per-material glTF (.glb) + manifest.

    python3 models/fetch_thor_meshes.py

Downloads the official per-link COLLADA meshes from `AngelLM/Thor-ROS`
(`ws_thor/src/thor_urdf/meshes`) into a local cache, then writes one binary
glTF (.glb) per *material group* of each machine part, authored in the renderer
home pose (feedback = 0, i.e. physical q = -JOINT_OFFSET), and a manifest
`models/thor_meshes.json` describing each part's sub-meshes + colours.

Why per material: the upstream DAEs split a link into several materials (e.g.
red body + black detail).  The old STL export flattened everything into one
grey mesh; keeping the groups lets the machine-parts yaml render each sub-mesh
with its original diffuse colour (the VTK actor paints one colour per node).

VTK ships no COLLADA reader, so the GLB is written directly here (no Blender,
no pycollada).  `models/table.stl` is a generic table copied from sim.robot_ur
and is not fetched here; the spindle/tool meshes are generated separately.
"""
import glob
import json
import os
import struct
import sys
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import thor_dh as dh  # noqa: E402

NS = "{http://www.collada.org/2005/11/COLLADASchema}"
BASE_URL = ("https://raw.githubusercontent.com/AngelLM/Thor-ROS/main/"
            "ws_thor/src/thor_urdf/meshes")
CACHE = os.environ.get("THOR_DAE_CACHE", "/tmp/thor_dae")

# machine part -> upstream mesh file
SOURCE = {
    "base": "Base.dae",
    "link1": "Art1.dae", "link2": "Art2.dae", "link3": "Art3.dae",
    "link4": "Art4.dae", "link5": "Art5.dae", "link6": "Art6.dae",
    "tool": "GripperBase.dae",
}


def fetch(name):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        url = BASE_URL + "/" + name
        print("download", url)
        urllib.request.urlretrieve(url, path)
    return path


def _tag(e):
    return e.tag.split("}")[-1]


def _sources(mesh):
    src = {}
    for s in mesh.findall(NS + "source"):
        fa = s.find(NS + "float_array")
        if fa is None:
            continue
        acc = s.find(NS + "technique_common/" + NS + "accessor")
        ncol = int(acc.get("stride", "3")) if acc is not None else 3
        arr = np.fromstring(fa.text, sep=" ").astype(float)
        src[s.get("id")] = arr.reshape(-1, ncol)
    return src


def _geometry_polys(geom):
    """(positions Nx3, [(indices Mx3, material_symbol), ...])."""
    mesh = geom.find(NS + "mesh")
    src = _sources(mesh)
    vsrc = None
    for v in mesh.findall(NS + "vertices"):
        for inp in v.findall(NS + "input"):
            if inp.get("semantic") == "POSITION":
                vsrc = inp.get("source").lstrip("#")
    pos = src[vsrc][:, :3]
    prims = []
    for tri in list(mesh.findall(NS + "triangles")) + list(mesh.findall(NS + "polylist")):
        inputs = tri.findall(NS + "input")
        stride = max(int(i.get("offset")) for i in inputs) + 1
        voff = [int(i.get("offset")) for i in inputs
                if i.get("semantic") == "VERTEX"][0]
        p = np.fromstring(tri.find(NS + "p").text, sep=" ").astype(int)
        p = p.reshape(-1, stride)[:, voff]
        faces = []
        if _tag(tri) == "triangles":
            faces = p.reshape(-1, 3)
        else:
            vc = np.fromstring(tri.find(NS + "vcount").text, sep=" ").astype(int)
            k = 0
            for n in vc:
                face = p[k:k + n]
                k += n
                for t in range(1, n - 1):
                    faces.append([face[0], face[t], face[t + 1]])
            faces = np.array(faces, dtype=int)
        prims.append((faces, tri.get("material")))
    return pos, prims


def _material_colors(root):
    effects = {}
    for eff in root.iter(NS + "effect"):
        col = None
        for shader in ("lambert", "phong", "blinn"):
            for sh in eff.iter(NS + shader):
                d = sh.find(NS + "diffuse")
                if d is not None:
                    c = d.find(NS + "color")
                    if c is not None:
                        col = [float(x) for x in c.text.split()]
                        break
            if col:
                break
        effects[eff.get("id")] = col or [0.8, 0.8, 0.8, 1.0]
    mats = {}
    for m in root.iter(NS + "material"):
        ie = m.find(NS + "instance_effect")
        if ie is not None:
            mats[m.get("id")] = effects.get(ie.get("url").lstrip("#"),
                                            [0.8, 0.8, 0.8, 1.0])
    return mats


def parse_dae(path):
    """-> [(positions Nx3, faces Mx3, rgba), ...] grouped by material colour.

    Positions are transformed by each scene node's <matrix> (COLLADA stores it
    column-major) so multi-node parts and the Art6/GripperBase metre->mm scale
    (1000x node matrix) are handled correctly.
    """
    root = ET.parse(path).getroot()
    colors = _material_colors(root)
    geoms = {g.get("id"): g for g in root.iter(NS + "geometry")}

    by_color = {}
    for vscene in root.iter(NS + "visual_scene"):
        for node in vscene.findall(NS + "node"):
            ig = node.find(NS + "instance_geometry")
            if ig is None:
                continue
            mat = np.eye(4)
            m = node.find(NS + "matrix")
            if m is not None:
                v = np.fromstring(m.text, sep=" ").astype(float)
                if v.size == 16:
                    mat = v.reshape(4, 4).T
            gid = ig.get("url").lstrip("#")
            pos, prims = _geometry_polys(geoms[gid])
            posw = (mat @ np.hstack([pos, np.ones((len(pos), 1))]).T).T[:, :3]
            sym2mat = {im.get("symbol"): im.get("target").lstrip("#")
                       for im in ig.iter(NS + "instance_material")}
            for faces, symbol in prims:
                rgba = colors.get(sym2mat.get(symbol), [0.8, 0.8, 0.8, 1.0])
                key = tuple(round(v, 4) for v in rgba)
                g = by_color.setdefault(key, {"pos": [], "faces": [], "off": 0})
                g["faces"].append(faces + g["off"])
                g["pos"].append(posw)
                g["off"] += len(posw)
        break  # single visual scene
    if not by_color:
        raise SystemExit("no geometry in %s" % path)
    return [(np.vstack(v["pos"]), np.vstack(v["faces"]), list(k))
            for k, v in by_color.items()]


def _pad4(b):
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def write_glb(path, positions, indices, color):
    """Minimal glTF 2.0 binary with one primitive + a baseColor material."""
    pos = np.ascontiguousarray(positions, dtype="<f4")
    idx = np.ascontiguousarray(np.asarray(indices, dtype="<u4").reshape(-1))
    idx_bytes = _pad4(idx.tobytes())
    pos_bytes = _pad4(pos.tobytes())
    bin_data = idx_bytes + pos_bytes
    mn = [float(v) for v in pos.min(0)]
    mx = [float(v) for v in pos.max(0)]
    gltf = {
        "asset": {"version": "2.0", "generator": "fetch_thor_meshes.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": os.path.basename(path)}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 1},
                                    "indices": 0, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {
            "baseColorFactor": [float(c) for c in color[:3]] + [1.0],
            "metallicFactor": 0.2, "roughnessFactor": 0.5}}],
        "accessors": [
            {"bufferView": 0, "componentType": 5125,
             "count": int(idx.size), "type": "SCALAR"},
            {"bufferView": 1, "componentType": 5126, "count": int(len(pos)),
             "type": "VEC3", "min": mn, "max": mx},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(idx_bytes),
             "target": 34963},
            {"buffer": 0, "byteOffset": len(idx_bytes),
             "byteLength": len(pos_bytes), "target": 34962},
        ],
        "buffers": [{"byteLength": len(bin_data)}],
    }
    jb = json.dumps(gltf, separators=(",", ":")).encode()
    jb = jb + b" " * ((4 - len(jb) % 4) % 4)
    bin_padded = bin_data + b"\x00" * ((4 - len(bin_data) % 4) % 4)
    total = 12 + 8 + len(jb) + 8 + len(bin_padded)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(jb), 0x4E4F534A))
        fh.write(jb)
        fh.write(struct.pack("<II", len(bin_padded), 0x004E4942))
        fh.write(bin_padded)


def main():
    qref = [-dh.JOINT_OFFSET[j] for j in range(6)]
    manifest = {}
    for part, link_index, scale, viz in dh.mesh_inputs():
        path = fetch(SOURCE[part])
        groups = parse_dae(path)
        # drop this part's previous outputs so a material-count change leaves no
        # stale <part>.glb / <part>_N.glb behind
        for old in (glob.glob(os.path.join(HERE, part + ".glb")) +
                    glob.glob(os.path.join(HERE, part + "_*.glb"))):
            os.remove(old)
        if part == "base":
            world = dh.G4 @ dh._visual(viz) @ dh._scale(scale)
        elif part == "tool":
            world = (dh.urdf_frames(qref)[5] @ dh._tr_z(dh.GRIPPER_BASE_Z)
                     @ dh._visual(viz) @ dh._scale(scale))
        else:
            world = dh.urdf_frames(qref)[link_index] @ dh._visual(viz) @ dh._scale(scale)

        entries = []
        for k, (pos, faces, rgba) in enumerate(groups):
            used = np.unique(faces)
            remap = {int(o): i for i, o in enumerate(used)}
            f = np.vectorize(remap.get)(faces)
            verts = (world @ np.hstack([pos[used], np.ones((len(used), 1))]).T).T[:, :3]
            if len(groups) == 1:
                name = "%s.glb" % part
            else:
                name = "%s_%d.glb" % (part, k)
            out = os.path.join(HERE, name)
            write_glb(out, verts, f, rgba)
            entries.append({"model": "models/%s" % name,
                            "color": [round(c, 4) for c in rgba[:3]]})
            mn, mx = verts.min(0), verts.max(0)
            print("%-7s %-14s %6d tris  colour=%s  x[%.1f,%.1f] z[%.1f,%.1f]"
                  % (part, name, len(f), entries[-1]["color"],
                     mn[0], mx[0], mn[2], mx[2]))
        manifest[part] = entries

    mpath = os.path.join(HERE, "thor_meshes.json")
    with open(mpath, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print("wrote", mpath)


if __name__ == "__main__":
    main()
