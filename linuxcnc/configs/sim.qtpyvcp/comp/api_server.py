#!/usr/bin/env python3

import sys
import linuxcnc
import hal

from opcua import ua, Server

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMainWindow

from qtpyvcp.utilities.logger import getLogger

LOG = getLogger("opcua")


class OpcUA(QMainWindow):
    def __init__(self, parent=None):
        super(OpcUA, self).__init__(parent)

        self.log = LOG
        self.poll_timmer = None

        self.comp = hal.component("api_server")

        self.stat = linuxcnc.stat()

        # data variables

        self.feed_x = 0.0
        self.feed_y = 0.0
        self.feed_z = 0.0

        self.position_x = 0.0
        self.position_y = 0.0
        self.position_z = 0.0

        self.command = ""
        self.file = ""
        self.estop = ""

        self.joint_0_enable = False
        self.joint_1_enable = False
        self.joint_2_enable = False

        self.joint_0_homed = False
        self.joint_1_homed = False
        self.joint_2_homed = False

        self.task_mode = ""

        # previous data to compare

        self.prev_feed_x = None
        self.prev_feed_y = None
        self.prev_feed_z = None

        self.prev_position_x = None
        self.prev_position_y = None
        self.prev_position_z = None

        self.prev_command = None
        self.prev_file = None

        self.prev_estop = None

        self.prev_joint_0_enable = None
        self.prev_joint_1_enable = None
        self.prev_joint_2_enable = None

        self.prev_joint_0_homed = None
        self.prev_joint_1_homed = None
        self.prev_joint_2_homed = None

        self.prev_task_mode = None

        # OPC UA Server

        self.server = Server()

        self.url = "opc.tcp://0.0.0.0:4840"

        self.server.set_endpoint(self.url)
        self.server.set_server_name("Tnx Server")

        uri = "http://examples.freeopcua.github.io"
        idx = self.server.register_namespace(uri)

        # populating our address space

        self.pendant = self.server.nodes.objects.add_object(idx, "Pendant")

        # Data event types

        self.postione_etype = self.server.create_custom_event_type(
            idx,
            "Position",
            ua.ObjectIds.BaseEventType,
            [
                ("x", ua.VariantType.Float),
                ("y", ua.VariantType.Float),
                ("z", ua.VariantType.Float),
            ],
        )

        self.velocity_etype = self.server.create_custom_event_type(
            idx,
            "Velocity",
            ua.ObjectIds.BaseEventType,
            [
                ("x", ua.VariantType.Float),
                ("y", ua.VariantType.Float),
                ("z", ua.VariantType.Float),
            ],
        )

        self.command_etype = self.server.create_custom_event_type(
            idx, "Command", ua.ObjectIds.BaseEventType, [("cmd", ua.VariantType.String)]
        )

        self.file_etype = self.server.create_custom_event_type(
            idx, "File", ua.ObjectIds.BaseEventType, [("file", ua.VariantType.String)]
        )

        self.estop_etype = self.server.create_custom_event_type(
            idx,
            "ESTOP",
            ua.ObjectIds.BaseEventType,
            [("enabled", ua.VariantType.Boolean)],
        )

        self.joint_enabled_etype = self.server.create_custom_event_type(
            idx,
            "Joints Enabled",
            ua.ObjectIds.BaseEventType,
            [
                ("x", ua.VariantType.Boolean),
                ("y", ua.VariantType.Boolean),
                ("z", ua.VariantType.Boolean),
            ],
        )

        self.joint_homed_etype = self.server.create_custom_event_type(
            idx,
            "Joints Homed",
            ua.ObjectIds.BaseEventType,
            [
                ("x", ua.VariantType.Boolean),
                ("y", ua.VariantType.Boolean),
                ("z", ua.VariantType.Boolean),
            ],
        )

        self.task_mode_etype = self.server.create_custom_event_type(
            idx,
            "Task Mode",
            ua.ObjectIds.BaseEventType,
            [("mode", ua.VariantType.Int16)],
        )

        # Data event

        self.position_event = self.server.get_event_generator(
            self.postione_etype, self.pendant
        )

        self.velocity_event = self.server.get_event_generator(
            self.velocity_etype, self.pendant
        )

        self.command_event = self.server.get_event_generator(
            self.command_etype, self.pendant
        )

        self.file_event = self.server.get_event_generator(self.file_etype, self.pendant)

        self.estop_event = self.server.get_event_generator(
            self.estop_etype, self.pendant
        )

        self.joint_enabled_event = self.server.get_event_generator(
            self.joint_enabled_etype, self.pendant
        )

        self.joint_homed_event = self.server.get_event_generator(
            self.joint_homed_etype, self.pendant
        )

        self.task_mode_event = self.server.get_event_generator(
            self.task_mode_etype, self.pendant
        )

        self.mpg = self.pendant.add_variable(idx, "mpg", 0)
        self.mpg.set_writable()  # Set MyVariable to be writable by clients

        # Init the server

        self.server.start()

        # linuxcnc is waiting for us notify we are ready

        self.comp.ready()

    def start(self):
        self.poll_timmer = QTimer()
        self.poll_timmer.timeout.connect(self.poll)
        self.poll_timmer.start(30)

    def poll(self):
        try:
            self.stat.poll()

            # FEED / Velocity per axis

            self.feed_x = self.stat.joint[0].get("velocity")
            self.feed_y = self.stat.joint[1].get("velocity")
            self.feed_z = self.stat.joint[2].get("velocity")

            if (
                self.feed_x != self.prev_feed_x
                or self.feed_y != self.prev_feed_y
                or self.feed_z != self.prev_feed_z
            ):

                self.velocity_event.event.Message = ua.LocalizedText("Axis Velocity")
                self.velocity_event.event.Severity = 300
                self.velocity_event.event.x = self.feed_x
                self.velocity_event.event.y = self.feed_y
                self.velocity_event.event.z = self.feed_z
                self.velocity_event.trigger()

                self.prev_feed_x = self.feed_x
                self.prev_feed_y = self.feed_y
                self.prev_feed_z = self.feed_z

            # Postionion

            self.position_x = self.stat.position[0] - self.stat.g5x_offset[0]
            self.position_y = self.stat.position[1] - self.stat.g5x_offset[1]
            self.position_z = self.stat.position[2] - self.stat.g5x_offset[2]

            if (
                self.position_x != self.prev_position_x
                or self.position_y != self.prev_position_y
                or self.position_z != self.prev_position_z
            ):

                self.position_event.event.Message = ua.LocalizedText("Axis Position")
                self.position_event.event.Severity = 300
                self.position_event.event.x = self.position_x
                self.position_event.event.y = self.position_y
                self.position_event.event.z = self.position_z
                self.position_event.trigger()

                self.prev_position_x = self.position_x
                self.prev_position_y = self.position_y
                self.prev_position_z = self.position_z

            # Running Command

            self.command = self.stat.command

            if self.command != self.prev_command:

                self.command_event.event.Message = ua.LocalizedText("Running Command")
                self.command_event.event.Severity = 300
                self.command_event.event.text = self.command
                self.command_event.trigger()

                self.prev_commad = self.command

            # File Opened

            self.file = self.stat.file

            if self.file != self.prev_file:

                self.file_event.event.Message = ua.LocalizedText("File Opened")
                self.file_event.event.Severity = 300
                self.file_event.event.text = self.file
                self.file_event.trigger()

                self.prev_file = self.file

            # E-STOP

            self.estop = self.stat.estop

            if self.estop != self.prev_estop:

                self.estop_event.event.Message = ua.LocalizedText("ESTOP")
                self.estop_event.event.Severity = 300
                self.estop_event.event.enabled = self.estop
                self.estop_event.trigger()

                self.prev_estop = self.estop

            # Joints Enabled

            self.joint_0_enable = self.stat.joint[0].get("enabled")
            self.joint_1_enable = self.stat.joint[1].get("enabled")
            self.joint_2_enable = self.stat.joint[2].get("enabled")

            if (
                self.joint_0_enable != self.prev_joint_0_enable
                or self.joint_1_enable != self.prev_joint_1_enable
                or self.joint_2_enable != self.prev_joint_2_enable
            ):

                self.joint_enabled_event.event.Message = ua.LocalizedText(
                    "Joints Enabled"
                )
                self.joint_enabled_event.event.Severity = 300
                self.joint_enabled_event.event.x = self.joint_0_enable
                self.joint_enabled_event.event.y = self.joint_1_enable
                self.joint_enabled_event.event.z = self.joint_2_enable
                self.joint_enabled_event.trigger()

                self.prev_joint_0_enable = self.joint_0_enable
                self.prev_joint_1_enable = self.joint_1_enable
                self.prev_joint_2_enable = self.joint_2_enable

            # Joints Home

            self.joint_0_homed = self.stat.joint[0].get("homed")
            self.joint_1_homed = self.stat.joint[1].get("homed")
            self.joint_2_homed = self.stat.joint[2].get("homed")

            if (
                self.joint_0_homed != self.prev_joint_0_homed
                or self.joint_1_homed != self.prev_joint_1_homed
                or self.joint_2_homed != self.prev_joint_2_homed
            ):

                self.joint_homed_event.event.Message = ua.LocalizedText("Joints Homed")
                self.joint_homed_event.event.Severity = 300
                self.joint_homed_event.event.x = self.joint_0_homed
                self.joint_homed_event.event.y = self.joint_1_homed
                self.joint_homed_event.event.z = self.joint_2_homed
                self.joint_homed_event.trigger()

                self.prev_joint_0_homed = self.joint_0_homed
                self.prev_joint_1_homed = self.joint_1_homed
                self.prev_joint_2_homed = self.joint_2_homed

            # Task Mode

            self.task_mode = self.stat.task_mode

            if self.task_mode != self.prev_task_mode:

                self.task_mode_event.event.Message = ua.LocalizedText("Task Mode")
                self.task_mode_event.event.Severity = 300
                self.task_mode_event.event.mode = self.task_mode
                self.task_mode_event.trigger()

                self.prev_task_mode = self.task_mode

            # task_mode = self.stat.task_mode
            #
            # self.task_mode.set_value(task_mode)

        except Exception as ki:
            print(ki)
            self.log.info("Stop OpcUA Server.")


def main():

    # create app
    App = QApplication(sys.argv)

    # ursor flashtime
    App.setCursorFlashTime(100)

    # obejct name
    App.setObjectName("TNCREMOTE")

    # display name
    App.setApplicationDisplayName("TNC Remote")

    # beep
    App.beep()

    # create the instance of our Window
    opcua_server = OpcUA()
    opcua_server.start()

    # start
    sys.exit(App.exec())


if __name__ == "__main__":
    main()
