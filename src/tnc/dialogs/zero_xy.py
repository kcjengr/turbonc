import os

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPen,
                           QRadialGradient)
from qtpyvcp.widgets.dialogs.base_dialog import BaseDialog

import tnc.main as tnc_main


def _accent():
    """(r, g, b) of the currently applied theme accent."""
    return getattr(tnc_main, "CURRENT_ACCENT", (0, 229, 255))


class ZeroXY(BaseDialog):
    def __init__(self, ui_file):
        super(ZeroXY, self).__init__(stay_on_top=True, frameless=True,
                                     ui_file=ui_file)
        # Translucent window: the frosted fill blends with the desktop behind
        # the dialog (needs a compositor). Frameless removes the native frame;
        # the dialog is moved by dragging it.
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_offset = None

        self.ui.zero_xy_button.clicked.connect(self.set_method)
        self.ui.close_button.clicked.connect(self.close_method)

    def paintEvent(self, event):
        """Semi-translucent glass with chamfered (cut) corners, a top sheen
        and a neon border matching the theme accent.

        Painted in code because Qt does not reliably draw the stylesheet
        background of a translucent top-level window.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        ar, ag, ab = _accent()
        top, bottom = tnc_main.glass_fill()
        path = tnc_main.glass_path(rect)

        # frosted fill (diagonal gradient)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        # glass sheen across the top
        sheen = QLinearGradient(0, rect.y(), 0, rect.y() + rect.height() * 0.45)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 30))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(sheen)
        painter.drawPath(path)

        # soft neon glow from the top-left corner
        glow = QRadialGradient(
            QPointF(rect.x() + rect.width() * 0.2,
                    rect.y() + rect.height() * 0.2),
            rect.width() * 0.9)
        glow.setColorAt(0.0, QColor(ar, ag, ab, 40))
        glow.setColorAt(1.0, QColor(ar, ag, ab, 0))
        painter.setBrush(glow)
        painter.drawPath(path)

        # neon border
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(ar, ag, ab, 150), 1))
        painter.drawPath(path)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super(ZeroXY, self).mouseReleaseEvent(event)

    def open(self):
        super(ZeroXY, self).open()

    def close_method(self):
        self.reject()
        self.close()

    def set_method(self):
        self.accept()
        self.close()

    