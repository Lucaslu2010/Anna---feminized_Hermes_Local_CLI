import math

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class CloudLoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.angle = 0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.advance)

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.hide()

    def start(self):
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.angle = 0
        self.show()
        self.raise_()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.hide()

    def advance(self):
        self.angle = (self.angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        del event

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor(255, 255, 255, 154))

        size = min(self.width(), self.height(), 88)
        if size <= 0:
            return

        cx = self.width() / 2
        cy = self.height() / 2
        ring_size = size
        ring_rect = QRectF(
            cx - ring_size / 2,
            cy - ring_size / 2,
            ring_size,
            ring_size,
        )

        track_pen = QPen(QColor("#d0d7de"), 4)
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(ring_rect, 0, 360 * 16)

        active_pen = QPen(QColor("#0969da"), 4)
        active_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(active_pen)
        painter.drawArc(ring_rect, int(-self.angle * 16), int(-110 * 16))

        self.draw_cloud(painter, cx, cy, ring_size * 0.42)

    def draw_cloud(self, painter: QPainter, cx: float, cy: float, width: float):
        height = width * 0.48
        left = cx - width / 2
        top = cy - height / 2 + 2

        path = QPainterPath()
        path.moveTo(left + width * 0.20, top + height * 0.78)
        path.cubicTo(
            left + width * 0.06,
            top + height * 0.78,
            left,
            top + height * 0.58,
            left + width * 0.13,
            top + height * 0.46,
        )
        path.cubicTo(
            left + width * 0.16,
            top + height * 0.18,
            left + width * 0.43,
            top + height * 0.10,
            left + width * 0.58,
            top + height * 0.28,
        )
        path.cubicTo(
            left + width * 0.76,
            top + height * 0.20,
            left + width,
            top + height * 0.34,
            left + width * 0.96,
            top + height * 0.58,
        )
        path.cubicTo(
            left + width * 0.94,
            top + height * 0.72,
            left + width * 0.82,
            top + height * 0.78,
            left + width * 0.70,
            top + height * 0.78,
        )
        path.closeSubpath()

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0969da"))
        painter.drawPath(path)

        painter.setBrush(QColor(255, 255, 255, 235))
        dot_radius = max(1.5, width * 0.035)
        for offset in [-0.13, 0.0, 0.13]:
            x = cx + width * offset
            y = cy + height * 0.08 + math.sin((self.angle + offset * 200) / 180 * math.pi)
            painter.drawEllipse(QRectF(x - dot_radius, y - dot_radius, dot_radius * 2, dot_radius * 2))
