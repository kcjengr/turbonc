#!/usr/bin/env python3
"""reach_guard - stop Cartesian (teleop) jogs before genserkins' IK fails.

LinuxCNC's genserkins solver is an *undamped* Gauss-Newton iteration seeded
with the current joint values.  When a Cartesian jog walks the flange toward a
kinematic singularity (or outside the reachable workspace) the iteration
diverges and ``kinematicsInverse`` returns an error, which faults and disables
the machine.  An axis-aligned soft-limit box cannot describe this: the Thor's
wrist is not spherical, so the *position* workspace also depends on the tool
orientation held during the jog.

This userspace HAL component watches the commanded Cartesian pose and the
current joint values and does two things:

* the cheap reach check - stop a jog that walks the flange past ``r-max`` from
  the shoulder, and
* a *look-ahead IK check* - run the same modified-DH Newton solve genserkins
  uses on a point ``LOOKAHEAD`` mm further along the jog direction, seeded from
  the current joints.  If that pose is not solvable the jog is stopped while
  the arm is still in a well-conditioned region.

``motion.jog-stop`` only aborts jogs (decelerating), so G-code programs are
untouched.

Geometry / kinematics come from ``models/thor_dh.py`` (single source of truth).
"""
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))
import thor_dh as dh  # noqa: E402

import hal  # noqa: E402

# Shoulder pitch axis height in machine coordinates (mm) and reach sphere.
# The fully-stretched arm measures A[2] + D[3] + D[5] from the shoulder, so the
# guard must use the same D[5] as genserkins (a shorter value would let jogs
# walk past the real envelope and fault the controller's IK).
SHOULDER_Z = 202.0
HARD_LIMIT = dh.A[2] + dh.D[3] + dh.D[5]
R_MAX_DEFAULT = HARD_LIMIT - 20.0

# Look-ahead distance (mm) and Newton iteration budget for the IK check.
LOOKAHEAD = 40.0
IK_ITERS = 25
IK_TOL = 1e-7
# Minimum commanded move (mm) between scans to define a jog direction.
MOVE_EPS = 1e-4
# After a trip, hold jog-stop asserted for this many scans (20 ms each).
HOLD_SCANS = 5
# A solved look-ahead pose may not move a joint further than this in one scan,
# and must stay inside the physical joint travel.  genserkins has no joint
# limits of its own, so without this a jog can walk the wrist into another
# 360 deg branch (the arm visibly spins several turns) and then trip the
# controller's soft limits on the next move.
MAX_JOINT_STEP = 45.0       # deg
JOINT_MARGIN = 5.0          # deg allowed outside the URDF travel

_ALPHA = np.radians(dh.ALPHA)
_A = dh.A
_D = dh.D
# Physical joint travel in machine coordinates (theta = physical + offset).
J_MIN = [dh.Q_LIMITS[j][0] + dh.JOINT_OFFSET[j] - JOINT_MARGIN for j in range(6)]
J_MAX = [dh.Q_LIMITS[j][1] + dh.JOINT_OFFSET[j] + JOINT_MARGIN for j in range(6)]


def _dh_mat(al, a, d, th_deg):
    c, s = math.cos(th_deg), math.sin(th_deg)  # th already in radians
    ca, sa = math.cos(al), math.sin(al)
    return np.array([[c, -s, 0, a],
                     [s * ca, c * ca, -sa, -sa * d],
                     [s * sa, c * sa, ca, ca * d],
                     [0, 0, 0, 1]])


def _frames(theta_deg):
    F = np.eye(4)
    out = []
    for j in range(6):
        F = F @ _dh_mat(_ALPHA[j], _A[j], _D[j], math.radians(theta_deg[j]))
        out.append(F.copy())
    return out


def _err(T, pos):
    E = np.linalg.inv(T) @ pos
    v = E[:3, 3]
    w = np.array([E[2, 1] - E[1, 2], E[0, 2] - E[2, 0], E[1, 0] - E[0, 1]]) / 2.0
    R = T[:3, :3]
    return np.concatenate([R @ v, R @ w])


def _jac(theta_deg, h=1e-6):
    """Finite-difference Jacobian that mirrors genserkins' iteration dynamics."""
    T0 = _frames(theta_deg)[5]
    J = np.zeros((6, 6))
    for j in range(6):
        d = list(theta_deg)
        d[j] += math.degrees(h)
        T = _frames(d)[5]
        J[:3, j] = (T[:3, 3] - T0[:3, 3]) / h
        W = ((T[:3, :3] - T0[:3, :3]) / h) @ T0[:3, :3].T
        J[3:, j] = [W[2, 1], W[0, 2], W[1, 0]]
    return J


def _pose(x, y, z, a, b, c):
    ra, rb, rc = math.radians(a), math.radians(b), math.radians(c)
    ca, sa = math.cos(ra), math.sin(ra)
    cb, sb = math.cos(rb), math.sin(rb)
    cc, sc = math.cos(rc), math.sin(rc)
    T = np.eye(4)
    T[:3, 3] = (x, y, z)
    T[:3, :3] = (np.array([[cc, -sc, 0], [sc, cc, 0], [0, 0, 1]])
                 @ np.array([[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]])
                 @ np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]]))
    return T


def _solve(target, seed):
    """Newton solve like genserkins; returns the joint solution or ``None``."""
    th = list(seed)
    for _ in range(IK_ITERS):
        e = _err(_frames(th)[5], target)
        if np.linalg.norm(e) < IK_TOL:
            return th
        try:
            dq = np.linalg.solve(_jac(th), e)
        except np.linalg.LinAlgError:
            return None
        for j in range(6):
            th[j] += math.degrees(dq[j])
    return None


def _overshoot(joint, value):
    """How far ``value`` is outside the joint travel (0 when inside)."""
    return max(J_MIN[joint] - value, value - J_MAX[joint], 0.0)


def _bad_solution(solution, seed):
    """Why a look-ahead solution must not be followed, or ``''`` if it is fine."""
    for j in range(6):
        # Leaving the physical travel is only a problem when the jog is walking
        # further out: a joint that is already outside must be able to come
        # back, otherwise the operator is locked out of recovery.
        if _overshoot(j, solution[j]) > _overshoot(j, seed[j]) + 0.5:
            return "joint %d leaves its travel (%.0f..%.0f -> %.1f)" % (
                j, J_MIN[j], J_MAX[j], solution[j])
        if abs(solution[j] - seed[j]) > MAX_JOINT_STEP:
            return "joint %d jumps %.0f deg in one scan" % (
                j, solution[j] - seed[j])
    return ""


def _solvable(target, seed):
    """True when a look-ahead pose is both solvable and inside the travel."""
    solution = _solve(target, seed)
    return solution is not None and not _bad_solution(solution, seed)


comp = None


def main():
    comp = hal.component("reach_guard")
    for p in ("x", "y", "z", "a", "b", "c"):
        comp.newpin(p, hal.Type.REAL, hal.Dir.IN)
    for j in range(6):
        comp.newpin("j%d" % j, hal.Type.REAL, hal.Dir.IN)
    comp.newpin("jog-active", hal.Type.BOOL, hal.Dir.IN)
    comp.newpin("teleop-mode", hal.Type.BOOL, hal.Dir.IN)
    comp.newpin("jog-stop", hal.Type.BOOL, hal.Dir.OUT)
    comp.newpin("distance", hal.Type.REAL, hal.Dir.OUT)
    comp.newpin("tripped", hal.Type.BOOL, hal.Dir.OUT)
    comp.newparam("r-max", hal.Type.REAL, hal.Dir.RW)
    comp.newparam("hard-limit", hal.Type.REAL, hal.Dir.RO)

    comp["r-max"] = R_MAX_DEFAULT
    comp["hard-limit"] = HARD_LIMIT
    comp["jog-stop"] = False
    comp["tripped"] = False
    comp.ready()

    last = None
    last_ds = None
    last_reason = ""
    hold = 0

    try:
        while True:
            x = comp["x"]
            y = comp["y"]
            z = comp["z"]
            zc = z - SHOULDER_Z
            ds = math.sqrt(x * x + y * y + zc * zc)
            comp["distance"] = ds

            guarding = bool(comp["jog-active"]) and bool(comp["teleop-mode"])
            if not guarding:
                comp["jog-stop"] = False
                comp["tripped"] = False
                last_reason = ""
                hold = 0
                last = (x, y, z)
                last_ds = ds
            else:
                r_max = comp["r-max"]
                outward = last_ds is not None and (ds - last_ds) > MOVE_EPS
                reach_stop = ds > r_max and outward

                # look-ahead IK check along the jog direction
                ik_stop = False
                if last is not None:
                    dx, dy, dz = x - last[0], y - last[1], z - last[2]
                    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if dist > MOVE_EPS:
                        u = LOOKAHEAD / dist
                        target = _pose(x + dx * u, y + dy * u, z + dz * u,
                                       comp["a"], comp["b"], comp["c"])
                        seed = [comp["j%d" % j] for j in range(6)]
                        solution = _solve(target, seed)
                        if solution is None:
                            ik_stop = True
                        else:
                            reason = _bad_solution(solution, seed)
                            if reason:
                                ik_stop = True
                                if reason != last_reason:
                                    # rtapi_print is not available in userspace
                                    print("reach_guard: %s" % reason,
                                          file=sys.stderr, flush=True)
                                    last_reason = reason

                if reach_stop or ik_stop or hold > 0:
                    comp["jog-stop"] = True
                    comp["tripped"] = True
                    if reach_stop or ik_stop:
                        hold = HOLD_SCANS
                    else:
                        hold -= 1
                else:
                    comp["jog-stop"] = False
                last = (x, y, z)
                last_ds = ds

            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        comp["jog-stop"] = False


if __name__ == "__main__":
    main()
