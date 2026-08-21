import bpy, sys, math

path = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else None
bpy.ops.wm.open_mainfile(filepath=path)
print("===== FILE:", path, "=====")

# only first instance set (drop .001 duplicates)
seen = set()
for o in bpy.data.objects:
    base = o.name.split(".")[0]
    if o.type != 'MESH' or base in seen:
        continue
    seen.add(base)
    mw = o.matrix_world
    wo = (mw[0][3], mw[1][3], mw[2][3])
    lo = tuple(round(v, 3) for v in o.location)
    re = tuple(round(math.degrees(v), 2) for v in o.rotation_euler)
    parent = o.parent.name if o.parent else None
    # world origin = pivot point in world
    print("%-14s parent=%-14s LOCAL loc=(%8.3f,%8.3f,%8.3f) LOCAL rot=(%7.2f,%7.2f,%7.2f) WORLD-origin=(%8.3f,%8.3f,%8.3f)"
          % (base, parent, lo[0], lo[1], lo[2], re[0], re[1], re[2],
             wo[0], wo[1], wo[2]))
