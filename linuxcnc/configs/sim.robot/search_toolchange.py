#!/usr/bin/env python3
"""Grid search for a robust TOOL_CHANGE_POSITION: tool-down (A=180,B=0,C=0),
reachable with comfortable joint margins and away from the J4 wrist singularity."""
import math
import numpy as np
from scipy.optimize import least_squares
import check_tcp_ik as m

LB = np.deg2rad([-720, -200, -170, -720, -115, -720])
UB = np.deg2rad([ 720,  200,  170,  720,  115,  720])

def rot_from_abc(a, b, c):
    a, b, c = map(math.radians, (a, b, c))
    ca, sa = math.cos(a), math.sin(a)
    cb, sb = math.cos(b), math.sin(b)
    cc, sc = math.cos(c), math.sin(c)
    Rx = np.array([[1,0,0],[0,ca,-sa],[0,sa,ca]])
    Ry = np.array([[cb,0,sb],[0,1,0],[-sb,0,cb]])
    Rz = np.array([[cc,-sc,0],[sc,cc,0],[0,0,1]])
    return Rz @ Ry @ Rx

def solve(xyz, abc):
    target = (np.array(xyz, dtype=float), rot_from_abc(*abc))
    best = None
    for seed in ([0,-math.pi/2,0,0,0.01,0],
                 [0,-1.2,0.6,0.5,-0.3,0.2],
                 [0,-0.5,-0.5,1.0,0.5,0.5],
                 [0.3,-1.0,0.8,-0.7,0.2,-1.2],
                 [0,-1.4,1.0,0.3,1.2,0.5]):
        res = least_squares(m.residual, seed, args=(target,), bounds=(LB, UB), max_nfev=4000)
        if best is None or res.cost < best.cost:
            best = res
    T = m.dh(best.x)
    pos_err = np.linalg.norm(T[:3,3] - target[0])
    if best.cost > 1e-6 or pos_err > 0.05:
        return None
    q = np.rad2deg(best.x)
    # margin to nearest joint limit (deg)
    margins = np.minimum(q - np.rad2deg(LB), np.rad2deg(UB) - q)
    return q, margins

print(f"{'X':>6} {'Y':>6} {'Z':>6} | {'J0':>7} {'J1':>7} {'J2':>7} {'J3':>7} {'J4':>7} {'J5':>7} | min-margin | J4-abs")
best_pts = []
for x in range(150, 460, 10):
    for z in range(40, 420, 10):
        r = solve((x, 0.0, z), (180.0, 0.0, 0.0))
        if r is None:
            continue
        q, margins = r
        mm = margins.min()
        if mm >= 10.0 and abs(q[4]) >= 20.0:
            best_pts.append((mm, abs(q[4]), (x, 0, z), q))
            print(f"{x:6d} {0:6d} {z:6d} | {q[0]:7.1f} {q[1]:7.1f} {q[2]:7.1f} {q[3]:7.1f} {q[4]:7.1f} {q[5]:7.1f} | {mm:9.1f} | {abs(q[4]):5.1f}")

best_pts.sort(reverse=True)
print("\nTOP 5 by min joint margin:")
for mm, j4, (x, y, z), q in best_pts[:5]:
    print(f"  X={x} Y={y} Z={z}  A=180 B=0 C=0   min-margin={mm:.1f}deg  J4={q[4]:.1f}")
