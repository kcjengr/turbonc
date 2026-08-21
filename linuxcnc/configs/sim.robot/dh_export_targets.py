#!/usr/bin/env python3
"""Blender re-export targets for the robot arm parts, using the STANDARD
Meca500 DH now in robot_arm-kinematics.hal.

The dialog (RobotWorkspaceDialog._apply_dh_frames) places each part by
centring the STL's geometry bbox-centre on the joint's world frame and
applying that frame's full rotation. So to make parts plot in place you should
re-author each part in Blender so its geometry bbox-centre sits at the printed
frame origin, and the part is oriented along the printed frame axes.

For each joint we print:
  - world frame origin (what bbox-centre must land on)
  - the frame's z axis (and x,y) as world unit vectors (how to align)
  - suggested Blender location/rotation (as an Empty target to snap to)
"""
import math

ALPHA = [0.0,          -math.pi/2.0, 0.0,
         -math.pi/2.0,  math.pi/2.0, -math.pi/2.0]
A = [0.0, 0.0, 135.0, 38.0, 0.0, 0.0]
D = [135.0, 0.0, 0.0, 120.0, 0.0, 70.0]
NAMES = ["joint_0", "joint_1", "joint_2", "joint_3", "joint_4", "joint_5"]


def dh_link(alpha, a, d, theta=0.0):
    sth, cth = math.sin(theta), math.cos(theta)
    sal, cal = math.sin(alpha), math.cos(alpha)
    return [[cth, -sth, 0.0, a],
            [sth*cal, cth*cal, -sal, -sal*d],
            [sth*sal, cth*sal, cal, cal*d],
            [0.0, 0.0, 0.0, 1.0]]


def mat_mul(A, B):
    return [[sum(A[i][k]*B[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def vmul(M, v):
    return (M[0][0]*v[0] + M[0][1]*v[1] + M[0][2]*v[2],
            M[1][0]*v[0] + M[1][1]*v[1] + M[1][2]*v[2],
            M[2][0]*v[0] + M[2][1]*v[1] + M[2][2]*v[2])


def frames():
    T = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    out = []
    for j in range(6):
        T = mat_mul(T, dh_link(ALPHA[j], A[j], D[j]))
        out.append(T)
    return out


if __name__ == "__main__":
    fr = frames()
    print("Standard Meca500 DH frames at home (all joints = 0)")
    print("Coordinate system: X right, Y front, Z up (mm).")
    print("-" * 80)
    imported = ""  # accumulate a Blender python snippet to create Empties
    lines = ["import bpy, math",
             "\ndef make_target(name, loc, rot_euler_deg):",
             "    bpy.ops.object.empty_add(type='ARROWS', location=loc)",
             "    o = bpy.context.object",
             "    o.name = name",
             "    o.rotation_euler = tuple(math.radians(a) for a in rot_euler_deg)",
             "    o.scale = (30,30,30)",
             "    bpy.ops.object.select_all(action='DESELECT')"]
    for j in range(6):
        T = fr[j]
        ox, oy, oz = T[0][3], T[1][3], T[2][3]
        zx, zy, zz = vmul(T, (0, 0, 1))
        xx, xy, xz = vmul(T, (1, 0, 0))
        yx, yy, yz = vmul(T, (0, 1, 0))
        # Blender euler by matching the 3x3 rotation to XYZ euler
        # R = Rz*Ry*Rx; extract standard angles
        R = [[xx, yx, zx], [xy, yy, zy], [xz, yz, zz]]
        sy = math.hypot(R[0][0], R[1][0])
        if sy > 1e-9:
            rz = math.degrees(math.atan2(R[1][0], R[0][0]))
            ry = math.degrees(math.atan2(-R[2][0], sy))
            rx = math.degrees(math.atan2(R[2][1], R[2][2]))
        else:
            rz = 0.0; ry = math.degrees(math.atan2(-R[2][0], sy)); rx = 0.0
        print("joint_%d" % j)
        print("  origin            : (%.1f, %.1f, %.1f)" % (ox, oy, oz))
        print("  x-axis (world)    : (%.2f, %.2f, %.2f)" % (xx, xy, xz))
        print("  y-axis (world)    : (%.2f, %.2f, %.2f)" % (yx, yy, yz))
        print("  z-axis (world)    : (%.2f, %.2f, %.2f)" % (zx, zy, zz))
        print("  Blender euler(XYZ): (%.2f, %.2f, %.2f) deg" % (rx, ry, rz))
        imported += ("make_target('j%d', (%f, %f, %f), (%f, %f, %f))\n"
                     % (j, ox, oy, oz, rx, ry, rz))
        print()
    print("--- Blender script snippet ---")
    print("\n".join(lines))
    print(imported)
