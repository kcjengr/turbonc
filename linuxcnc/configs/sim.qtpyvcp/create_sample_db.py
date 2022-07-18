# coding=utf-8
#   2022 TurBoss

from tool_db.base import Session, engine, Base
from tool_db.tool_database import Spindles, Magazines, Pockets, GeomGroups, Geometries, Offsets, Tools


Base.metadata.create_all(engine)

session = Session()


# create Spindle 1

spindle_1 = Spindles(
    description = "Big spindle with tool changer"
)

# some tools

tool_1 = Tools(
    id = 1,
    description = "First example tool",
    number = 1,
)


tool_2 = Tools(
    id = 2,
    description = "Seccond example Tool",
    number = 2,
)

# offsets for tools

offset_1 = Offsets(
    id = 1,
    description = "Z Offset 80mm",
    z_offset = 80,
)

offset_2 = Offsets(
    id = 2,
    description = "Z Offset 160mm",
    z_offset = 160,
)

# populate spindle

spindle_1.tools = [tool_1]


# populate offsets

tool_1.offsets = [offset_1]
tool_2.offsets = [offset_2]

# persists data

session.add(spindle_1)

session.add(tool_1)
session.add(tool_2)

session.add(offset_1)
session.add(offset_2)

# commit and close session

try:
    session.commit()
    
except Exception as e:
    print(e)
    
finally:
    session.close()

