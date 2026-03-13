import os

from qtpyvcp.widgets.dialogs.base_dialog import BaseDialog


class ZeroXY(BaseDialog):
    def __init__(self, ui_file):
        super(ZeroXY, self).__init__(stay_on_top=True, ui_file=ui_file)

        self.ui.zero_xy_button.clicked.connect(self.set_method)
        self.ui.close_button.clicked.connect(self.close_method)

    def open(self):
        super(ZeroXY, self).open()

    def close_method(self):
        self.reject()
        self.close()

    def set_method(self):
        self.accept()
        self.close()

    