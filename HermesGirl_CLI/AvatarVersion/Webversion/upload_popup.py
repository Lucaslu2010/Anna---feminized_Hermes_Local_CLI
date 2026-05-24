from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _event_global_pos(event) -> QPoint:
    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    return event.globalPos()


def _clamped_child_pos(widget: QWidget, pos: QPoint, margin: int = 8) -> QPoint:
    parent = widget.parentWidget()
    if parent is None:
        return pos

    max_x = max(margin, parent.width() - widget.width() - margin)
    max_y = max(margin, parent.height() - widget.height() - margin)
    return QPoint(
        min(max(pos.x(), margin), max_x),
        min(max(pos.y(), margin), max_y),
    )


class DraggableUploadButton(QPushButton):
    dragged = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._dragging = False
        self._drag_start_global = QPoint()
        self._drag_offset = QPoint()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            global_pos = _event_global_pos(event)
            self._dragging = False
            self._drag_start_global = global_pos
            self._drag_offset = global_pos - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not event.buttons() & Qt.LeftButton:
            return super().mouseMoveEvent(event)

        global_pos = _event_global_pos(event)
        if not self._dragging:
            distance = (global_pos - self._drag_start_global).manhattanLength()
            if distance < QApplication.startDragDistance():
                return super().mouseMoveEvent(event)
            self._dragging = True
            self.setDown(False)
            self.setCursor(Qt.ClosedHandCursor)

        self.move(_clamped_child_pos(self, global_pos - self._drag_offset))
        self.raise_()
        self.dragged.emit()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class UploadPopup(QFrame):
    minimize_requested = Signal()
    dragged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.rows = {}
        self._drag_handles = []
        self._dragging = False
        self._drag_start_global = QPoint()
        self._drag_offset = QPoint()
        self.setObjectName("uploadPopup")
        self.setFixedWidth(360)
        self.setStyleSheet("""
            QFrame#uploadPopup {
                background-color: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 12px;
            }

            QLabel#uploadTitle {
                color: #24292f;
                font-size: 14px;
                font-weight: 700;
            }

            QLabel#uploadFilename {
                color: #24292f;
                font-size: 12px;
                font-weight: 600;
            }

            QLabel#uploadStatus {
                color: #57606a;
                font-size: 11px;
            }

            QPushButton#uploadClose {
                border: none;
                border-radius: 10px;
                background-color: transparent;
                color: #57606a;
                font-weight: 700;
                min-width: 24px;
                min-height: 24px;
            }

            QPushButton#uploadClose:hover {
                background-color: #eef2f6;
            }

            QWidget#uploadHeader {
                background-color: transparent;
            }

            QProgressBar {
                height: 8px;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                background-color: #f6f8fa;
                text-align: center;
            }

            QProgressBar::chunk {
                border-radius: 4px;
                background-color: #0969da;
            }
        """)

        title = QLabel("Uploads")
        title.setObjectName("uploadTitle")

        close_button = QPushButton("x")
        close_button.setObjectName("uploadClose")
        close_button.clicked.connect(self.minimize_requested.emit)

        header_widget = QWidget()
        header_widget.setObjectName("uploadHeader")
        header_widget.setCursor(Qt.SizeAllCursor)

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 8, 2)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close_button)
        header_widget.setLayout(header)

        self._drag_handles = [header_widget, title]
        for handle in self._drag_handles:
            handle.installEventFilter(self)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(12, 6, 12, 12)
        self.list_layout.setSpacing(10)
        self.list_widget.setLayout(self.list_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.list_widget)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header_widget)
        layout.addWidget(scroll)
        self.setLayout(layout)

    def eventFilter(self, obj, event):
        if obj in self._drag_handles:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                global_pos = _event_global_pos(event)
                self._dragging = False
                self._drag_start_global = global_pos
                self._drag_offset = global_pos - self.frameGeometry().topLeft()
                self.setCursor(Qt.ClosedHandCursor)
                return True

            if event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.LeftButton:
                global_pos = _event_global_pos(event)
                if not self._dragging:
                    distance = (global_pos - self._drag_start_global).manhattanLength()
                    if distance < QApplication.startDragDistance():
                        return True
                    self._dragging = True

                self.move(_clamped_child_pos(self, global_pos - self._drag_offset))
                self.raise_()
                self.dragged.emit()
                return True

            if event.type() == QEvent.Type.MouseButtonRelease:
                self._dragging = False
                self.unsetCursor()
                return True

        return super().eventFilter(obj, event)

    def update_upload(self, upload_id: str, filename: str, percent: int, status: str):
        row = self.rows.get(upload_id)
        if row is None:
            row = self._create_row(filename)
            self.rows[upload_id] = row
            self.list_layout.addWidget(row["container"])

        row["filename"].setText(filename or "file")
        row["progress"].setValue(max(0, min(int(percent), 100)))
        row["status"].setText(status or "")

    def _create_row(self, filename: str):
        container = QWidget()

        filename_label = QLabel(filename or "file")
        filename_label.setObjectName("uploadFilename")
        filename_label.setWordWrap(True)

        status_label = QLabel("")
        status_label.setObjectName("uploadStatus")
        status_label.setWordWrap(True)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setTextVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(filename_label)
        layout.addWidget(progress)
        layout.addWidget(status_label)
        container.setLayout(layout)

        return {
            "container": container,
            "filename": filename_label,
            "status": status_label,
            "progress": progress,
        }
