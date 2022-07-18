# coding=utf-8
#   2022 TurBoss


from tool_db.base import Session
from tool_db.tool_database import Spindles, Magazines, Pockets, GeomGroups, Geometries, Offsets, Tools

session = Session()

# Spindles

spindles = session.query(Spindles).all()

print("Spindles")
print("\tID\tDescription")

for spindle in spindles:
    print(f"\t{spindle.id}\t{spindle.description}")
    
    tools = spindle.tools
    for tool in tools:
        print(f"Tool {tool.number} in spindle")

# Tool Table

tools = session.query(Tools).all()

print("Tools in tool table")

print("\tID\tNumbertDescription")

for tool in tools:
    offsets = tool.offsets
    
    print(f"\t{tool.id}\t{tool.number}\t{tool.description}")
    
    print(f"\t\t\tOffsets:")
  
    for offset in offsets:
        print(f"\t\t\t\tDescription: {offset.description}")

        print("\t\t\t\tAxis\tValue")
        
        print(f"\t\t\t\tX\t{offset.x_offset}")
        print(f"\t\t\t\tY\t{offset.y_offset}")
        print(f"\t\t\t\tZ\t{offset.z_offset}")
        print(f"\t\t\t\tA\t{offset.a_offset}")
        print(f"\t\t\t\tB\t{offset.b_offset}")
        print(f"\t\t\t\tC\t{offset.c_offset}")
        print(f"\t\t\t\tU\t{offset.u_offset}")
        print(f"\t\t\t\tV\t{offset.v_offset}")
        print(f"\t\t\t\tW\t{offset.w_offset}")



session.close()
