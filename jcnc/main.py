#!/usr/bin/env python

import os

from qtpy.QtCore import Slot
from qtpyvcp.widgets.form_widgets.main_window import VCPMainWindow

# Setup logging
from qtpyvcp.utilities import logger

LOG = logger.getLogger('QtPyVCP.' + __name__)

from qtpyvcp import actions

import resources

VCP_DIR = os.path.dirname(os.path.abspath(__file__))


class MainWindow(VCPMainWindow):
    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)


        self.initUi()
        self.setWindowTitle("Jauria CNC")

        # ==========================================================================
        #  Add/Override methods and slots below to customize the main window
        # ==========================================================================

        # actions.bindWidget(self.flood, action='coolant.flood.toggle')
        # actions.bindWidget(self.floodCheckBox, action="coolant.flood.toggle")

        if actions.program.run.ok():
            actions.program.run()
        else:
            print "RUN NOT OK: ", actions.program.run.ok.msg

    # This slot will be automatically connected to a menu item named 'Test'
    # created in QtDesigner.
    @Slot()
    def on_actionTest_triggered(self):
        print 'Test action triggered'
        # implement the action here
