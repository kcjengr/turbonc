#   Copyright (c) 2024 TurBoss
#      <j.l.toledano.l@gmail.com>
#
#   This file is part of TurboNC.
#
#   TurboNC is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 2 of the License, or
#   (at your option) any later version.
#
#   TurboNC is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with TurboNC.  If not, see <http://www.gnu.org/licenses/>.

from qtpy import uic
from qtpy.QtWidgets import QVBoxLayout, QDialog, QDialogButtonBox, QLabel

from qtpyvcp.widgets.dialogs.base_dialog import BaseDialog

# Version detection - use installed package version if available, otherwise fall back to git
try:
    from importlib.metadata import version, PackageNotFoundError
    try:
        __version__ = version("turbonc")
        # Check for placeholder version from editable installs
        if __version__ in ("0.0", "0.0.0"):
            raise PackageNotFoundError
    except PackageNotFoundError:
        # Development mode - use dunamai to extract version from git
        from dunamai import Version
        from pathlib import Path
        # Use module's parent directory (repo root) for git operations
        module_dir = Path(__file__).parent.parent.parent.parent
        v = Version.from_git(path=module_dir, pattern=r"^(?P<base>\d+\.\d+\.\d+)")
        base = v.base
        distance = v.distance
        commit = v.commit[:8] if v.commit else "unknown"
        __version__ = f"{base}+{distance}.g{commit}"
except ImportError:
    __version__ = "0+unknown"


class AboutTurboNCDialog(BaseDialog):
    def __init__(self, *args, **kwargs):
        super(AboutTurboNCDialog, self).__init__(stay_on_top=True)

        self.ui_file = kwargs.get('ui_file')

        if self.ui_file:
            uic.loadUi(self.ui_file, self)
        else:

            self.setFixedSize(600, 200)

            self.setWindowTitle("About TurboNC")

            self.layout = QVBoxLayout()
            self.setLayout(self.layout)

            self.button_box = QDialogButtonBox(QDialogButtonBox.Ok)
            self.button_box.accepted.connect(self.close)

            self.about_text = QLabel()
            self.about_text.setOpenExternalLinks(True)
            self.about_text.setText(
                f"""
                <center>
                TurboNC is a QtPyVCP based interface for LinuxCNC.<br />
                Version: {__version__}<br />
                Copyright (c) 2024 TurBoss<br />
                <a href="https://github.com/TurBoss/turbonc">https://github.com/TurBoss/turbonc</a>
                </center>
                """
            )

            self.layout.addWidget(self.about_text)
            self.layout.addWidget(self.button_box)
