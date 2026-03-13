import os

from qtpyvcp.widgets.dialogs.base_dialog import BaseDialog


class HomeAll(BaseDialog):
    def __init__(self, ui_file):
        super(HomeAll, self).__init__(stay_on_top=True, ui_file=ui_file)

        self.ui.homeall_abutton.clicked.connect(self.set_method)
        self.ui.close_button.clicked.connect(self.close_method)

    def open(self):
        super(HomeAll, self).open()

    def close_method(self):
        self.reject()
        self.close()

    def set_method(self):
        self.accept()
        self.close()
