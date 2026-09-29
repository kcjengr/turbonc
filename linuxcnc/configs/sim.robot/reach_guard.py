#!/usr/bin/env python3
"""reach_guard - abort Cartesian (teleop) jogs before they leave the arm's
reachable workspace.

genserkins solves the inverse kinematics iteratively. When motion asks it to
invert a pose beyond the arm's reach it fails ("kinematicsInverse failed"),
which raises an error and *disables the machine*. A jog that walks the TCP past
the envelope therefore does not just stop - it faults the whole machine.

This userspace HAL component watches the commanded Cartesian position and, when
a coordinate (teleop) jog is moving *outward* past ``r-max``, asserts
``motion.jog-stop``. That aborts the jog gracefully (decelerating) instead of
letting genserkins fault. ``motion.jog-stop`` only affects jogs, so G-code
programs - which are validated by their own limits - are untouched.

Geometry
--------
The TCP sits at most ``a2 + d3 + d5 = 224 + 230 + 164.15 = 618.15 mm`` from the
shoulder pitch axis, which is at ``(0, 0, 186)`` in machine coordinates (see
robot_arm-kinematics.hal). So the reachable set is bounded by that sphere and a
jog is stopped once the TCP passes ``r-max`` (default 600 mm, an ~18 mm margin)
while moving away from the shoulder. Moving back inward is always allowed, so
the guard cannot trap the arm outside the envelope.

Directions use the change in distance between scans rather than the velocity
pins, so it works for any kind of teleop jog (NML, MPG wheel, keyboard).
"""

import math
import time

import hal

# Shoulder pitch axis height in machine coordinates (mm).
SHOULDER_Z = 186.0
# Hard reach limit (mm) and the safe limit the guard enforces by default.
HARD_LIMIT = 618.15
R_MAX_DEFAULT = 600.0
# Distance must change by at least this much (mm) between scans to count as
# motion, so numeric noise in the commanded position is ignored.
MOTION_EPS = 1e-4
# After a trip, hold jog-stop asserted for this many scans (20 ms each). Motion
# aborts the jog on the servo cycle, but holding the request for ~100 ms makes
# the abort robust to scan timing.
HOLD_SCANS = 5

comp = hal.component("reach_guard")
comp.newpin("x", hal.Type.REAL, hal.Dir.IN)
comp.newpin("y", hal.Type.REAL, hal.Dir.IN)
comp.newpin("z", hal.Type.REAL, hal.Dir.IN)
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

last_ds = None
hold = 0

try:
    while True:
        x = comp["x"]
        y = comp["y"]
        z = comp["z"] - SHOULDER_Z
        ds = math.sqrt(x * x + y * y + z * z)
        comp["distance"] = ds

        guarding = bool(comp["jog-active"]) and bool(comp["teleop-mode"])
        if not guarding:
            comp["jog-stop"] = False
            comp["tripped"] = False
            hold = 0
            last_ds = ds
        else:
            r_max = comp["r-max"]
            outward = last_ds is not None and (ds - last_ds) > MOTION_EPS
            if (ds > r_max and outward) or hold > 0:
                # Stop the jog before genserkins is asked to invert it.
                comp["jog-stop"] = True
                comp["tripped"] = True
                hold = HOLD_SCANS if (ds > r_max and outward) else hold - 1
            else:
                comp["jog-stop"] = False
            last_ds = ds

        time.sleep(0.02)
except KeyboardInterrupt:
    pass
finally:
    comp["jog-stop"] = False
