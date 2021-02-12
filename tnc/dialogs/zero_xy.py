import os

from qtpyvcp.widgets.dialogs.base_dialog import BaseDialog

UI_FILE = os.path.join(os.path.dirname(__file__), 'zero_xy.ui')

class ZeroXY(BaseDialog):
    def __init__(self):
        super(ZeroXY, self).__init__(stay_on_top=True, ui_file=UI_FILE)
        
    def open(self):
        super(ZeroXY, self).open()
        
    def on_close_button_clicked(self):
        super(ZeroXY, self).close()
        
    def on_zero_xy_button_clicked(self):
        super(ZeroXY, self).close()