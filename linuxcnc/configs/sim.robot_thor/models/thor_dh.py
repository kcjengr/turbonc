"""Single source of truth for the sim.robot_thor arm (AngelLM "Thor", mm / deg).

Thor is a 6-DOF yaw-roll-roll-yaw-roll-yaw arm.  The numbers below are the
official kinematics from the upstream ``AngelLM/Thor-ROS`` URDF
(``thor_urdf/urdf/thor.urdf.xacro``), re-expressed as the modified (Craig) DH
table that LinuxCNC's genserkins and the VTK machine-parts renderer use:

    T_j = Rx(alpha) . Tx(a) . Rz(theta) . Tz(d)

Two conventions matter here:

* The URDF joint origin offsets run along the parent frame's +Z, and the joint
  axes are  z, x, x, z, x, z  (all joint origins lie on the base Z axis).
* genserkins has no base transform, so the machine frame is the URDF frame
  rotated by -90 deg about Z (``BASE_ROT``).  Without that rotation the first
  two axes (Z then X) cannot be written as a modified-DH chain.

The DH extraction (``_extract`` in gen_thor_config.py) also yields a constant
per-joint angular offset: the genserkins joint coordinate is
``theta = q_physical + JOINT_OFFSET``.  The LinuxCNC joint limits/HOME are
therefore the URDF limits shifted by that offset.

Pure python/numpy, no bpy, so the mesh exporter and the config generator share
the same numbers.
"""
import math

import numpy as np

# --- URDF joint chain (mm) -------------------------------------------------
# joint i origin is TRANSLATED along the parent +Z by ORIGIN[i], then rotated
# about AXIS[i] by q[i].
ORIGIN = [99.0, 103.0, 160.0, 89.5, 104.5, 13.5]
AXIS = ["z", "x", "x", "z", "x", "z"]
# URDF joint limits (deg).
Q_LIMITS = [(-170.0, 170.0), (-90.0, 90.0), (-90.0, 90.0),
            (-170.0, 170.0), (-90.0, 90.0), (-170.0, 170.0)]

# --- extracted modified DH (mm / deg) --------------------------------------
# D[5] carries the spindle-nose offset NOSE along the tool axis, so the
# genserkins frame-5 origin (the machine's controlled point) is the spindle
# NOSE rather than the flange.  The world/gcode path then references the nose.
NOSE = 92.0                                     # mm, flange -> spindle nose
ALPHA = [0.0, 90.0, 0.0, 90.0, 90.0, 90.0]     # deg
A = [0.0, 0.0, 160.0, 0.0, 0.0, 0.0]           # mm
D = [202.0, 0.0, 0.0, 194.0, 0.0, 13.5 + NOSE]  # mm
# machine joint angle = physical URDF joint angle + JOINT_OFFSET
JOINT_OFFSET = [0.0, 90.0, 90.0, 180.0, 180.0, 180.0]  # deg

# Rotation from the URDF base frame to the genserkins machine frame.
BASE_ROT = -90.0  # deg about Z

# Gripper base is a fixed child of the URDF link 6, +43 mm along link 6 z.
GRIPPER_BASE_Z = 43.0  # mm
# Legacy flange -> finger-tip approximation, used only as the default for tcp().
# NOTE: it is NOT the tool-table Z length -- the LinuxCNC tool table is now
# referenced to the spindle NOSE (D[5] already carries NOSE), not the flange.
TOOL_LEN = 43.0 + 75.0 + 60.0  # mm (base + mid-point + finger reach, approx)

# Physical (URDF) joint-space home.  Tool hangs VERTICALLY (spindle nose straight
# down) with the TCP at ~(208, 0, 256) world; the wrist is clear of its singular
# configuration (J4 physical = -90).  Machine coords = [0,85,5,180,90,180].
HOME_Q = [0.0, -5.0, -85.0, 0.0, -90.0, 0.0]    # deg (physical)



def _rt(axis, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def urdf_frames(q=None):
    """World 4x4 frame of every URDF link (index 0..5 = link_1..link_6).

    Frames are expressed in the (BASE_ROT-rotated) genserkins machine frame.
    """
    q = [0.0] * 6 if q is None else q
    T = np.eye(4)
    out = []
    for j in range(6):
        step = np.eye(4)
        step[2, 3] = ORIGIN[j]
        step[:3, :3] = _rt(AXIS[j], q[j])
        T = T @ step
        out.append(G4 @ T)
    return out


def dh_link(alpha_deg, a, d, theta_deg):
    al, th = math.radians(alpha_deg), math.radians(theta_deg)
    c, s = math.cos(th), math.sin(th)
    ca, sa = math.cos(al), math.sin(al)
    return np.array([[c, -s, 0, a],
                     [s * ca, c * ca, -sa, -sa * d],
                     [s * sa, c * sa, ca, ca * d],
                     [0, 0, 0, 1]])


def dh_frames(theta):
    """World modified-DH frames for genserkins joint coordinates theta (deg)."""
    T = np.eye(4)
    out = []
    for j in range(6):
        T = T @ dh_link(ALPHA[j], A[j], D[j], theta[j])
        out.append(T.copy())
    return out


def _rz(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


G4 = np.eye(4)
G4[:3, :3] = _rz(BASE_ROT)


def tcp(q, tool=TOOL_LEN):
    """Physical URDF joint angles -> TCP frame (machine coords)."""
    T = urdf_frames(q)[5].copy()
    T[:3, 3] += T[:3, 2] * GRIPPER_BASE_Z
    T[:3, 3] += T[:3, 2] * tool
    return T


def rpy_deg(R):
    """LinuxCNC/genserkins A,B,C: R = Rz(C) Ry(B) Rx(A)."""
    pitch = math.atan2(-R[2, 0], math.hypot(R[0, 0], R[1, 0]))
    if abs(math.cos(pitch)) < 1e-9:
        return 0.0, math.degrees(pitch), math.degrees(math.atan2(-R[0, 1], R[1, 1]))
    yaw = math.atan2(R[1, 0], R[0, 0])
    roll = math.atan2(R[2, 1], R[2, 2])
    return tuple(math.degrees(v) for v in (roll, pitch, yaw))


def machine_home():
    """Physical HOME_Q shifted into genserkins joint coordinates (deg)."""
    return [HOME_Q[j] + JOINT_OFFSET[j] for j in range(6)]


def mesh_inputs():
    """(part_name, link_index_or_None, mesh_scale, visual_rpy_z_deg).

    ``link_index`` 0..5 = Art1..Art6 (link_1..link_6); None = base/tool static.
    ``mesh_scale`` converts the raw mesh units to mm (some upstream meshes are
    authored in metres).  ``visual_rpy_z_deg`` is the URDF <visual><origin> rpy
    about Z (all arm/base meshes use -90; the gripper base uses 0).
    """
    return [
        ("base", None, 1.0, -90.0),
        ("link1", 0, 1.0, -90.0),    # Art1
        ("link2", 1, 1.0, -90.0),    # Art2
        ("link3", 2, 1.0, -90.0),    # Art3
        ("link4", 3, 1.0, -90.0),    # Art4
        ("link5", 4, 1.0, -90.0),    # Art5
        ("link6", 5, 1.0, -90.0),    # Art6 (dae node matrix scales m -> mm)
    ]


def mesh_world(link_index, scale, visual_rpy_z_deg, qref=None):
    """4x4 mapping raw mesh vertices -> the renderer home (feedback=0) pose.

    The renderer authors every STL in the joint-space pose where feedback = 0,
    i.e. physical q = -JOINT_OFFSET, then drives parts by DH deltas.
    """
    qref = [-JOINT_OFFSET[j] for j in range(6)] if qref is None else qref
    if link_index is None:
        # static base is the identity link; tool hangs off link 6.
        return None  # resolved by the caller (needs the part name)
    return urdf_frames(qref)[link_index] @ _visual(visual_rpy_z_deg) @ _scale(scale)


def _visual(rz_deg):
    T = np.eye(4)
    T[:3, :3] = _rz(rz_deg)
    return T


def _scale(s):
    return np.diag([s, s, s, 1.0])


def _tr_z(z):
    T = np.eye(4)
    T[2, 3] = z
    return T


if __name__ == "__main__":
    for j, f in enumerate(urdf_frames()):
        print(j, f[:3, 3].round(2), "z", f[:3, 2].round(2))
    T = tcp(HOME_Q)
    print("HOME", T[:3, 3].round(3), [round(v, 3) for v in rpy_deg(T[:3, :3])])
