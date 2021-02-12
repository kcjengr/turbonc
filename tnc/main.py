#!/usr/bin/env python

import os

from qtpy.QtCore import Slot
from qtpyvcp.widgets.form_widgets.main_window import VCPMainWindow

# Setup logging
from qtpyvcp.utilities import logger

LOG = logger.getLogger('QtPyVCP.' + __name__)

from qtpyvcp import actions

from tnc.dialogs.zero_xy import ZeroXY

import resources

VCP_DIR = os.path.dirname(os.path.abspath(__file__))


class MainWindow(VCPMainWindow):
    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)

        self.initUi()
        
        self.zero__xy_dialog = ZeroXY()

    def on_zero_xy_button_clicked(self):
        self.zero__xy_dialog.open()