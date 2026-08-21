"""Geometry analysis for the robot arm: STL + machine-parts yaml -> DH params.

This module is the analytical half of the Robot Arm dialog's **Analyze** tab.
It is deliberately free of Qt so it can be unit tested / run headless.

What it does
------------
1. Reads the ``[VTK] MACHINE_PARTS`` yaml tree and pulls out, for every angular
   joint, its pivot ``origin`` and rotation ``axis`` **expressed in home-pose
   world coordinates** (that is how the STLs are authored - see the header of
   ``models/robot_arm.yml``).
2. Optionally inspects each ``model:`` STL to report mesh bounds / centre and
   to sanity check that the declared pivot actually lies on the part.
3. Derives the pairwise geometry between consecutive joint axes (perpendicular
   distance + twist angle), which *is* the Denavit-Hartenberg data.
4. Converts that into a modified-DH (Craig) table in the exact convention
   ``genserkins`` uses, verified against a live LinuxCNC instance:

       link_i = Rx(alpha_i) . Tx(a_i) . Rz(theta_i) . Tz(d_i)

   with ``params[i]`` paired with ``theta[i]``.
5. **Validates** the result - forward kinematics, Jacobian rank, conditioning
   and a wrist-singularity sweep - because a DH table can be syntactically fine
   and still describe a mechanism that cannot reach 6 DOF. A rank < 6 table
   makes ``genserkins`` fail every inverse-kinematics call with
   "kinematicsInverse failed", at *every* pose, which is otherwise very hard to
   diagnose from the UI.

The checks in step 5 exist because that exact failure was hit in this config:
``ALPHA-0``/``ALPHA-1``/``ALPHA-2`` were all ``0``, which declared J0, J1 and
J2 to rotate about mutually parallel axes (parallelism is transitive), and
``A-1`` was ``0``, which put J1 and J2 on the *same line*. The Jacobian was
rank 4 of 6 no matter how the arm was posed.
"""

import math
import os

import numpy as np
import yaml

# Angles closer than this to 0 / 90 / 180 degrees are reported as
# parallel / perpendicular rather than as an arbitrary skew angle.
ANGLE_TOL_DEG = 1e-6
# Distances below this (mm) count as "intersecting" axes.
DIST_TOL_MM = 1e-6
# Jacobian singular values below this are treated as a lost degree of freedom.
RANK_TOL = 1e-6


# ----------------------------------------------------------------------
# small vector helpers
# ----------------------------------------------------------------------
def unit(v):
    """Normalised copy of ``v`` (returns the zero vector unchanged)."""
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


AXIS_VECTORS = {
    "x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
    "z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0),
}


def axis_to_vector(axis):
    """Map a yaml ``axis:`` string (``"z"``, ``"-y"``...) to a unit vector."""
    if axis is None:
        return None
    key = str(axis).strip().lower()
    v = AXIS_VECTORS.get(key)
    return np.array(v, dtype=float) if v else None


def vector_to_axis(vec):
    """Inverse of :func:`axis_to_vector`; nearest principal axis label."""
    v = unit(vec)
    best, best_dot = None, -2.0
    for name, ref in AXIS_VECTORS.items():
        d = float(np.dot(v, ref))
        if d > best_dot:
            best, best_dot = name, d
    return best


# ----------------------------------------------------------------------
# yaml model tree -> joints
# ----------------------------------------------------------------------
class Joint(object):
    """One angular joint read out of the machine-parts yaml."""

    __slots__ = ("index", "part_id", "origin", "axis_name", "axis",
                 "mount", "model", "model_abs", "node")

    def __init__(self, index, part_id, origin, axis_name, axis, mount,
                 model, model_abs, node):
        self.index = index
        self.part_id = part_id
        self.origin = origin          # np(3,) pivot point, world/home coords
        self.axis_name = axis_name    # e.g. "-z"
        self.axis = axis              # np(3,) unit rotation axis, world/home
        self.mount = mount            # raw [a, ., ., alpha_deg, ., .] or None
        self.model = model            # model path as written in the yaml
        self.model_abs = model_abs    # resolved absolute path (may not exist)
        self.node = node              # the raw yaml dict

    def __repr__(self):
        return "<Joint %d %s origin=%s axis=%s>" % (
            self.index, self.part_id, np.round(self.origin, 1).tolist(),
            self.axis_name)


def iter_parts(node, path=()):
    """Depth-first walk of the machine-parts tree yielding ``(key, node)``."""
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if not isinstance(value, dict):
            continue
        if "id" in value or "type" in value or "model" in value:
            yield path + (key,), value
            for sub in iter_parts(value, path + (key,)):
                yield sub


def resolve_model_path(model, search_dirs):
    """Resolve a yaml ``model:`` path against the likely base directories.

    Model paths are written relative to the *config* directory, but the yaml
    itself often lives in a ``models/`` subdirectory, so a naive join against
    the yaml's own folder doubles the prefix. Try each candidate base and fall
    back to the first one so the caller still gets a usable path to report.
    """
    if not model:
        return None
    if os.path.isabs(model):
        return model
    tried = []
    for base in search_dirs:
        if not base:
            continue
        cand = os.path.normpath(os.path.join(base, model))
        tried.append(cand)
        if os.path.isfile(cand):
            return cand
        # also try the bare filename inside this base (yaml next to its STLs)
        cand2 = os.path.normpath(os.path.join(base, os.path.basename(model)))
        if os.path.isfile(cand2):
            return cand2
    return tried[0] if tried else model


def load_joints(yaml_path, data=None, config_dir=None):
    """Extract the ordered angular joints from a machine-parts yaml.

    ``data`` may be passed in if the caller already loaded the yaml (the dialog
    keeps a copy so it can write it back unchanged). Joints are returned sorted
    by their declared ``joint:`` index.
    """
    if data is None:
        with open(yaml_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    yaml_dir = os.path.dirname(os.path.abspath(yaml_path or "."))
    search_dirs = [config_dir, yaml_dir, os.path.dirname(yaml_dir)]

    joints = []
    for _path, node in iter_parts(data or {}):
        if str(node.get("type", "")).lower() != "angular":
            continue
        jnum = node.get("joint")
        if jnum is None:
            continue
        origin = np.array(list(node.get("origin") or [0, 0, 0])[:3], dtype=float)
        axis_name = node.get("axis")
        axis = axis_to_vector(axis_name)
        model = node.get("model")
        model_abs = resolve_model_path(model, search_dirs)
        joints.append(Joint(int(jnum), node.get("id") or _path[-1], origin,
                            axis_name, axis, node.get("mount"), model,
                            model_abs, node))
    joints.sort(key=lambda j: j.index)
    return joints


# ----------------------------------------------------------------------
# STL inspection
# ----------------------------------------------------------------------
class MeshInfo(object):
    __slots__ = ("path", "exists", "bounds", "center", "size", "triangles",
                 "error")

    def __init__(self, path, exists=False, bounds=None, center=None,
                 size=None, triangles=0, error=None):
        self.path = path
        self.exists = exists
        self.bounds = bounds          # (xmin,xmax,ymin,ymax,zmin,zmax)
        self.center = center          # np(3,)
        self.size = size              # np(3,)
        self.triangles = triangles
        self.error = error


def inspect_mesh(path):
    """Read an STL and report its bounds / centre / triangle count."""
    if not path:
        return MeshInfo(path, error="no model path")
    if not os.path.isfile(path):
        return MeshInfo(path, error="file not found")
    try:
        from vtkmodules.vtkIOGeometry import vtkSTLReader
        reader = vtkSTLReader()
        reader.SetFileName(path)
        reader.Update()
        poly = reader.GetOutput()
        b = poly.GetBounds()
        center = np.array([(b[0] + b[1]) * 0.5,
                           (b[2] + b[3]) * 0.5,
                           (b[4] + b[5]) * 0.5])
        size = np.array([b[1] - b[0], b[3] - b[2], b[5] - b[4]])
        return MeshInfo(path, True, b, center, size,
                        int(poly.GetNumberOfCells()))
    except Exception as exc:  # noqa: BLE001 - mesh reading is best-effort
        return MeshInfo(path, error=str(exc))


def point_in_bounds(point, bounds, margin=1.0):
    """True if ``point`` lies inside ``bounds`` grown by ``margin`` mm."""
    if bounds is None:
        return False
    x, y, z = point
    return (bounds[0] - margin <= x <= bounds[1] + margin and
            bounds[2] - margin <= y <= bounds[3] + margin and
            bounds[4] - margin <= z <= bounds[5] + margin)


# ----------------------------------------------------------------------
# axis-pair geometry (this is what DH parameters actually encode)
# ----------------------------------------------------------------------
class AxisPair(object):
    """Geometric relationship between two consecutive joint axes."""

    __slots__ = ("i", "distance", "angle_deg", "relation", "common_normal",
                 "foot_a", "foot_b")

    def __init__(self, i, distance, angle_deg, relation, common_normal,
                 foot_a, foot_b):
        self.i = i
        self.distance = distance
        self.angle_deg = angle_deg
        self.relation = relation      # parallel | perpendicular | skew | ...
        self.common_normal = common_normal
        self.foot_a = foot_a
        self.foot_b = foot_b


def axis_pair(i, p1, d1, p2, d2):
    """Distance, twist and common normal between two axis lines."""
    d1, d2 = unit(d1), unit(d2)
    cross = np.cross(d1, d2)
    n = np.linalg.norm(cross)
    cosang = float(np.clip(abs(np.dot(d1, d2)), -1.0, 1.0))
    angle = math.degrees(math.acos(cosang))

    if n < 1e-9:                                    # parallel (or anti-)
        w = p2 - p1
        perp = w - np.dot(w, d1) * d1
        dist = float(np.linalg.norm(perp))
        normal = unit(perp) if dist > 1e-9 else None
        relation = "coincident" if dist <= DIST_TOL_MM else "parallel"
        return AxisPair(i, dist, angle, relation, normal, p1, p1 + perp)

    normal = cross / n
    # closest points on each line
    A = np.array([[1.0, -float(np.dot(d1, d2))],
                  [float(np.dot(d1, d2)), -1.0]])
    b = np.array([float(np.dot(p2 - p1, d1)), float(np.dot(p2 - p1, d2))])
    try:
        t, s = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        t = s = 0.0
    f1, f2 = p1 + t * d1, p2 + s * d2
    dist = float(np.linalg.norm(f2 - f1))
    if dist <= DIST_TOL_MM:
        relation = "intersecting"
    elif abs(angle - 90.0) < 1e-6:
        relation = "perpendicular"
    else:
        relation = "skew"
    return AxisPair(i, dist, angle, relation, normal, f1, f2)


# ----------------------------------------------------------------------
# kinematics in the exact genserkins convention
# ----------------------------------------------------------------------
def dh_link(alpha, a, d, theta):
    """One modified-DH (Craig) link: ``Rx(alpha).Tx(a).Rz(theta).Tz(d)``.

    This matches ``go_dh_pose_convert`` in linuxcnc's ``gomath.c`` and has been
    verified numerically against a running instance.
    """
    ca, sa = math.cos(alpha), math.sin(alpha)
    ct, st = math.cos(theta), math.sin(theta)
    return np.array([
        [ct,      -st,       0.0,  a],
        [st * ca,  ct * ca, -sa,  -sa * d],
        [st * sa,  ct * sa,  ca,   ca * d],
        [0.0,      0.0,      0.0,  1.0],
    ])


def forward_frames(table, thetas=None):
    """Cumulative 4x4 frames after each link. ``table`` is ``[(alpha,a,d)]``
    with alpha in **radians**; ``thetas`` in radians (defaults to all zero)."""
    if thetas is None:
        thetas = [0.0] * len(table)
    acc = np.eye(4)
    out = []
    for (alpha, a, d), th in zip(table, thetas):
        acc = acc @ dh_link(alpha, a, d, th)
        out.append(acc.copy())
    return out


def joint_axes_from_table(table, thetas=None):
    """Joint axis (point, direction) for every joint of a DH table.

    Joint ``i`` rotates about the z of ``F_{i-1}.Rx(alpha_i).Tx(a_i)`` - i.e.
    *before* its own ``Rz(theta_i)`` - so this is where the physical axis sits.
    """
    if thetas is None:
        thetas = [0.0] * len(table)
    frame = np.eye(4)
    pts, dirs = [], []
    for (alpha, a, d), th in zip(table, thetas):
        ca, sa = math.cos(alpha), math.sin(alpha)
        pre = np.array([[1.0, 0.0, 0.0, a],
                        [0.0, ca, -sa, 0.0],
                        [0.0, sa,  ca, 0.0],
                        [0.0, 0.0, 0.0, 1.0]])
        g = frame @ pre
        pts.append(g[:3, 3].copy())
        dirs.append(g[:3, 2].copy())
        ct, st = math.cos(th), math.sin(th)
        post = np.array([[ct, -st, 0.0, 0.0],
                         [st,  ct, 0.0, 0.0],
                         [0.0, 0.0, 1.0, d],
                         [0.0, 0.0, 0.0, 1.0]])
        frame = g @ post
    return np.array(pts), np.array(dirs)


def jacobian(table, thetas):
    """6xN geometric Jacobian at ``thetas`` (radians).

    Joint ``i`` turns about the z of frame ``i``, not frame ``i-1``: in this
    modified-DH (Craig) convention the link transform is
    ``Rx(alpha_i).Tx(a_i).Rz(theta_i).Tz(d_i)``, and ``Rz(theta_i)`` leaves z
    untouched, so frame ``i``'s z *is* joint ``i``'s axis and frame ``i``'s
    origin lies on it. Walking one frame behind (the standard-DH layout)
    duplicates joint 0's column and silently drops the last joint's, which
    reports a phantom rank deficiency on perfectly good tables.
    """
    frames = forward_frames(table, thetas)
    pe = frames[-1][:3, 3]
    n = len(table)
    jac = np.zeros((6, n))
    for i in range(n):
        z = frames[i][:3, 2]
        p = frames[i][:3, 3]
        jac[:3, i] = np.cross(z, pe - p)
        jac[3:, i] = z
    return jac


def rank_report(table, thetas):
    """``(rank, singular_values)`` of the Jacobian at one pose."""
    jac = jacobian(table, thetas)
    sv = np.linalg.svd(jac, compute_uv=False)
    return int(np.linalg.matrix_rank(jac, tol=RANK_TOL)), sv


# Poses used to decide whether a table is *structurally* capable of 6 DOF.
# A mechanism that is rank deficient at all of these is degenerate by design,
# not merely parked in a singular pose.
PROBE_POSES_DEG = [
    [0, 0, 0, 0, 0, 0],
    [10, 20, -30, 15, 40, 25],
    [20, -40, 50, 25, 35, -15],
    [-35, 25, 15, -20, 55, 40],
    [45, -15, -25, 60, -30, 10],
]


def structural_rank(table):
    """Best Jacobian rank over several generic poses, plus the best pose.

    Returns ``(best_rank, best_sigma_min, best_pose_deg)``.
    """
    n = len(table)
    best = (0, 0.0, None)
    for pose in PROBE_POSES_DEG:
        thetas = np.radians(pose[:n])
        rank, sv = rank_report(table, thetas)
        smin = float(sv[-1])
        if rank > best[0] or (rank == best[0] and smin > best[1]):
            best = (rank, smin, list(pose[:n]))
    return best


def singularity_sweep(table, joint, angles_deg, base_pose_deg=None):
    """Sweep one joint and report rank / conditioning along the way.

    Used by the UI to answer "how far from zero does the wrist have to be
    before the solver is comfortable?".
    """
    n = len(table)
    base = list(base_pose_deg or [0.0] * n)[:n]
    while len(base) < n:
        base.append(0.0)
    out = []
    for ang in angles_deg:
        pose = list(base)
        pose[joint] = float(ang)
        rank, sv = rank_report(table, np.radians(pose))
        out.append((float(ang), rank, float(sv[-1])))
    return out


# ----------------------------------------------------------------------
# derive a DH table from the model's joint axes
# ----------------------------------------------------------------------
def derive_table(joints):
    """Best-effort modified-DH table reproducing the model's joint axes.

    ``a_i`` / ``alpha_i`` describe the step from axis ``i-1`` to axis ``i``,
    which is why the model's *pairwise* geometry maps onto the table shifted by
    one index. ``d_i`` positions the frame along its own joint axis.

    Returns ``(table_rad, notes)``. The table is a starting point: a zero pose
    that genserkins can represent must have every frame's x-axis parallel (it
    has no theta offsets), which is not always true of how the STLs are drawn -
    see :func:`zero_pose_representable`.
    """
    n = len(joints)
    notes = []
    if n == 0:
        return [], ["no angular joints found"]

    pairs = [axis_pair(i, joints[i].origin, joints[i].axis,
                       joints[i + 1].origin, joints[i + 1].axis)
             for i in range(n - 1)]

    table = []
    # First link: bring the base frame onto joint 0's axis.
    j0 = joints[0]
    z0 = unit(j0.axis)
    alpha0 = math.atan2(float(np.linalg.norm(np.cross([0, 0, 1], z0))),
                        float(np.dot([0, 0, 1], z0)))
    table.append((alpha0, 0.0, float(np.dot(j0.origin, z0))))

    for i, pair in enumerate(pairs):
        alpha = math.radians(pair.angle_deg)
        # sign follows the common normal's handedness relative to the axes
        d1 = unit(joints[i].axis)
        d2 = unit(joints[i + 1].axis)
        if pair.common_normal is not None:
            sign = np.dot(np.cross(d1, d2), pair.common_normal)
            if sign < 0:
                alpha = -alpha
        table.append((alpha, float(pair.distance), 0.0))
        if pair.relation == "coincident":
            notes.append(
                "J%d and J%d share the same axis line - one of them cannot "
                "contribute a degree of freedom." % (i, i + 1))

    while len(table) < n:
        table.append((0.0, 0.0, 0.0))
    return table[:n], notes


def zero_pose_representable(joints):
    """Can genserkins express the model's drawn pose as its all-zero pose?

    genserkins has no theta offsets, so at ``theta = 0`` every DH frame's
    x-axis is parallel. That shared x must be perpendicular to *every* joint
    axis, and each consecutive pair's common normal has to line up with it.
    When the model is drawn folded (e.g. the upper arm stacked vertically while
    the base yaw is also vertical) that cannot hold, and the visual zero pose
    has to differ from the kinematic zero pose.

    Returns ``(ok, shared_axis_or_None, reason)``.
    """
    if len(joints) < 2:
        return True, None, ""
    dirs = [unit(j.axis) for j in joints]
    candidates = []
    for name, vec in AXIS_VECTORS.items():
        v = np.array(vec, dtype=float)
        if all(abs(float(np.dot(v, d))) < 1e-6 for d in dirs):
            candidates.append(name)
    if not candidates:
        return (False, None,
                "no single direction is perpendicular to every joint axis, so "
                "the drawn pose cannot be the kinematic zero pose")

    shared = axis_to_vector(candidates[0])
    for i in range(len(joints) - 1):
        pair = axis_pair(i, joints[i].origin, joints[i].axis,
                         joints[i + 1].origin, joints[i + 1].axis)
        if pair.common_normal is None:
            continue
        if abs(abs(float(np.dot(pair.common_normal, shared))) - 1.0) > 1e-6:
            return (False, candidates[0],
                    "the J%d->J%d offset is not along %s, so the drawn pose "
                    "cannot be the kinematic zero pose" %
                    (i, i + 1, candidates[0]))
    return True, candidates[0], ""


# ----------------------------------------------------------------------
# report
# ----------------------------------------------------------------------
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2, "ok": 3}


class Finding(object):
    __slots__ = ("severity", "title", "detail")

    def __init__(self, severity, title, detail=""):
        self.severity = severity
        self.title = title
        self.detail = detail

    def __repr__(self):
        return "<%s %s>" % (self.severity.upper(), self.title)


class Report(object):
    """Everything the Analyze tab renders."""

    def __init__(self):
        self.yaml_path = None
        self.joints = []
        self.meshes = {}          # joint index -> MeshInfo
        self.pairs = []
        self.derived = []         # [(alpha_rad, a, d)]
        self.derive_notes = []
        self.current = []         # [(alpha_rad, a, d)] from the .hal
        self.findings = []
        self.current_rank = None
        self.current_sigma = None
        self.derived_rank = None
        self.derived_sigma = None
        self.zero_pose_ok = None
        self.zero_pose_axis = None
        self.zero_pose_reason = ""
        self.sweep = []

    @property
    def worst(self):
        if not self.findings:
            return "ok"
        return sorted(self.findings,
                      key=lambda f: SEVERITY_ORDER.get(f.severity, 9))[0].severity

    def add(self, severity, title, detail=""):
        self.findings.append(Finding(severity, title, detail))


def analyse(yaml_path, data=None, config_dir=None, current_table=None,
            read_meshes=True):
    """Run the full model / DH analysis and return a :class:`Report`."""
    report = Report()
    report.yaml_path = yaml_path
    joints = load_joints(yaml_path, data=data, config_dir=config_dir)
    report.joints = joints

    if not joints:
        report.add("error", "No angular joints found",
                   "The machine-parts yaml has no parts with "
                   "type: angular and a joint: index.")
        return report

    # --- per joint sanity -------------------------------------------------
    seen = {}
    for j in joints:
        if j.axis is None:
            report.add("error", "J%d (%s) has no usable axis" % (j.index, j.part_id),
                       "axis: %r is not one of x/-x/y/-y/z/-z." % (j.axis_name,))
        if j.index in seen:
            report.add("error", "Duplicate joint index %d" % j.index,
                       "Parts %s and %s both claim joint %d."
                       % (seen[j.index], j.part_id, j.index))
        seen[j.index] = j.part_id

        if read_meshes:
            info = inspect_mesh(j.model_abs)
            report.meshes[j.index] = info
            if info.error:
                report.add("warning", "J%d mesh unavailable" % j.index,
                           "%s (%s)" % (info.error, j.model))
            elif not point_in_bounds(j.origin, info.bounds, margin=5.0):
                report.add(
                    "warning", "J%d pivot lies outside its mesh" % j.index,
                    "origin %s is not within the bounds of %s - the STL may be "
                    "exported in a different frame than the pivot."
                    % (np.round(j.origin, 1).tolist(), os.path.basename(j.model or "?")))

    expected = list(range(len(joints)))
    if [j.index for j in joints] != expected:
        report.add("warning", "Joint indices are not 0..%d" % (len(joints) - 1),
                   "Found %s." % [j.index for j in joints])

    # --- pairwise geometry -------------------------------------------------
    for i in range(len(joints) - 1):
        if joints[i].axis is None or joints[i + 1].axis is None:
            continue
        pair = axis_pair(i, joints[i].origin, joints[i].axis,
                         joints[i + 1].origin, joints[i + 1].axis)
        report.pairs.append(pair)
        if pair.relation == "coincident":
            report.add(
                "error", "J%d and J%d are the same axis" % (i, i + 1),
                "They are parallel and %.3f mm apart, so together they supply "
                "one degree of freedom instead of two. Inverse kinematics will "
                "fail at every pose." % pair.distance)

    # --- degrees of freedom in the model itself ---------------------------
    dirs = [unit(j.axis) for j in joints if j.axis is not None]
    if dirs:
        rot_rank = int(np.linalg.matrix_rank(np.array(dirs).T, tol=1e-6))
        # At the genserkins zero pose every joint axis is perpendicular to the
        # shared x-axis (no theta offsets), so a *valid* zero pose always spans
        # at most two directions. Only flag when the axis set has NO common
        # perpendicular - i.e. the drawn pose is not even a zero pose, and the
        # arm genuinely lacks a rotation direction. The real orientation
        # capability is measured by the active DH table's structural rank.
        has_common = any(
            all(abs(float(np.dot(v, d))) < 1e-6 for d in dirs)
            for v in AXIS_VECTORS.values())
        if rot_rank < 3 and len(dirs) >= 6 and not has_common:
            missing = _missing_rotation_axis(dirs)
            report.add(
                "error", "The arm cannot rotate about every axis",
                "All %d joint axes span only %d independent directions at the "
                "drawn pose%s. A 6-DOF arm needs 3. Check that the wrist has a "
                "roll axis along the forearm."
                % (len(dirs), rot_rank,
                   " (nothing rotates about %s)" % missing if missing else ""))

    # --- zero pose representability ---------------------------------------
    ok, shared, reason = zero_pose_representable(
        [j for j in joints if j.axis is not None])
    report.zero_pose_ok = ok
    report.zero_pose_axis = shared
    report.zero_pose_reason = reason
    if not ok:
        report.add(
            "warning", "Drawn pose is not a valid kinematic zero",
            (reason + ". genserkins has no theta offsets, so the DH zero pose "
             "must differ from how the STLs are drawn - carry the difference "
             "in the model's mount rotations, not in the DH table."))

    # --- derived table -----------------------------------------------------
    derived, notes = derive_table([j for j in joints if j.axis is not None])
    report.derived = derived
    report.derive_notes = notes
    if derived:
        rank, sigma, _pose = structural_rank(derived)
        report.derived_rank = rank
        report.derived_sigma = sigma
        if rank < 6 and len(derived) >= 6:
            report.add(
                "warning", "Derived table only reaches rank %d of 6" % rank,
                "The pairwise axis geometry does not capture the along-axis "
                "wrist offsets (d), so a naive derivation collapses the wrist "
                "links. Use the active HAL table / 'Compute DH from parts' "
                "(which reads the mounts) instead of 'Load derived'.")

    # --- current table -----------------------------------------------------
    if current_table:
        report.current = current_table
        rank, sigma, pose = structural_rank(current_table)
        report.current_rank = rank
        report.current_sigma = sigma
        if rank < len(current_table):
            report.add(
                "error", "Active DH table is rank %d of %d" % (rank, len(current_table)),
                "The Jacobian never reaches full rank at any pose, so "
                "genserkins cannot solve inverse kinematics - every attempt "
                "fails immediately with 'kinematicsInverse failed'.")
        elif sigma < 0.05:
            report.add(
                "warning", "Active DH table is poorly conditioned",
                "Smallest singular value %.4f at the best probe pose; the "
                "solver may struggle near singularities." % sigma)
        else:
            report.add("ok", "Active DH table reaches full rank %d" % rank,
                       "Smallest singular value %.4f." % sigma)

        # wrist sweep: which joint is the pitch between two rolls?
        wrist = min(4, len(current_table) - 2)
        if wrist >= 0:
            report.sweep = singularity_sweep(
                current_table, wrist,
                [0, 0.1, 1, 5, 10, 20, 30, 45, 60, 90])
            poor = [ang for ang, rank, s in report.sweep
                    if rank >= len(current_table) and s < 0.05]
            if poor:
                report.add(
                    "info", "Keep J%d away from zero" % wrist,
                    "Conditioning stays below 0.05 up to %g deg; park the home "
                    "position well clear of the wrist singularity."
                    % max(poor))

    if not report.findings:
        report.add("ok", "No problems found", "")
    return report


def _missing_rotation_axis(dirs):
    """Name a world axis that no joint can rotate about, if there is one."""
    for name in ("x", "y", "z"):
        ref = axis_to_vector(name)
        if all(abs(float(np.dot(unit(d), ref))) < 1e-6 for d in dirs):
            return name.upper()
    return None


# ----------------------------------------------------------------------
# text rendering (used for the "copy report" button and for CLI use)
# ----------------------------------------------------------------------
def format_report(report):
    """Render a :class:`Report` as plain text."""
    L = []
    L.append("Robot arm geometry analysis")
    L.append("=" * 60)
    L.append("model: %s" % (report.yaml_path or "?"))
    L.append("")

    L.append("Joints (pivot + rotation axis, home-pose world coords)")
    L.append("-" * 60)
    for j in report.joints:
        mesh = report.meshes.get(j.index)
        extra = ""
        if mesh is not None and mesh.exists:
            extra = "  mesh %d tris, size %s" % (
                mesh.triangles, np.round(mesh.size, 1).tolist())
        L.append("  J%d %-10s origin=%-22s axis=%-3s%s"
                 % (j.index, j.part_id, np.round(j.origin, 1).tolist(),
                    j.axis_name, extra))
    L.append("")

    if report.pairs:
        L.append("Consecutive axis geometry")
        L.append("-" * 60)
        for p in report.pairs:
            L.append("  J%d-J%d  %9.3f mm  %7.2f deg  %s"
                     % (p.i, p.i + 1, p.distance, p.angle_deg, p.relation))
        L.append("")

    def _table(name, tbl, rank, sigma):
        if not tbl:
            return
        L.append("%s  (rank %s, sigma_min %s)"
                 % (name,
                    "?" if rank is None else rank,
                    "?" if sigma is None else "%.4f" % sigma))
        L.append("-" * 60)
        L.append("   i   alpha(deg)        a(mm)        d(mm)")
        for i, (al, a, d) in enumerate(tbl):
            L.append("  %2d %12.4f %12.3f %12.3f"
                     % (i, math.degrees(al), a, d))
        L.append("")

    _table("Active DH table", report.current, report.current_rank,
           report.current_sigma)
    _table("Derived from model", report.derived, report.derived_rank,
           report.derived_sigma)

    if report.sweep:
        L.append("Wrist conditioning sweep")
        L.append("-" * 60)
        for ang, rank, sig in report.sweep:
            L.append("  %6.1f deg  rank %d  sigma_min %.4f" % (ang, rank, sig))
        L.append("")

    L.append("Findings")
    L.append("-" * 60)
    for f in sorted(report.findings,
                    key=lambda f: SEVERITY_ORDER.get(f.severity, 9)):
        L.append("  [%s] %s" % (f.severity.upper(), f.title))
        if f.detail:
            for line in _wrap(f.detail, 68):
                L.append("        " + line)
    return "\n".join(L)


def _wrap(text, width):
    words = str(text).split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


# ----------------------------------------------------------------------
# .hal table access (shared with the dialog)
# ----------------------------------------------------------------------
def read_hal_table(hal_path, njoints=6):
    """Read ``[(alpha_rad, a, d)]`` out of a ``robot_arm-kinematics.hal``."""
    values = {}
    try:
        with open(hal_path, "r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s.startswith("setp") or "genserkins." not in s:
                    continue
                body = s.split("genserkins.", 1)[1]
                name, _, val = body.partition(" ")
                try:
                    values[name.strip()] = float(val.split()[0])
                except (ValueError, IndexError):
                    continue
    except OSError:
        return []
    table = []
    for j in range(njoints):
        table.append((values.get("ALPHA-%d" % j, 0.0),
                      values.get("A-%d" % j, 0.0),
                      values.get("D-%d" % j, 0.0)))
    return table


if __name__ == "__main__":  # pragma: no cover - manual/CLI use
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "models/robot_arm.yml"
    hal = sys.argv[2] if len(sys.argv) > 2 else None
    cur = read_hal_table(hal) if hal else None
    print(format_report(analyse(path, current_table=cur)))
