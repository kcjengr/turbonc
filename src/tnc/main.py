#!/usr/bin/env python

import os

from qtpy.QtCore import Slot
from qtpy.QtWidgets import QAbstractButton

from qtpyvcp.widgets.form_widgets.main_window import VCPMainWindow

# Setup logging
from qtpyvcp.utilities import logger

LOG = logger.getLogger("QtPyVCP." + __name__)

from qtpyvcp import actions

import resources_rc

VCP_DIR = os.path.dirname(os.path.abspath(__file__))


class MainWindow(VCPMainWindow):
    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)

        self.initUi()

