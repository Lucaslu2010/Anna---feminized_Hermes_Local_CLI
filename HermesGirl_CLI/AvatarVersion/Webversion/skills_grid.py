import re

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


CARD_NORMAL_W = 152
CARD_NORMAL_H = 116
CARD_HOVER_W = 190
CARD_HOVER_H = 146
CARD_SHRINK_W = 132
CARD_SHRINK_H = 100
CARD_GAP = 8
GRID_MARGIN = 22


def parse_hermes_skills_text(text: str):
    skills = []
    for line in (text or "").splitlines():
        cells = split_table_row(line)
        if len(cells) < 2:
            continue

        if is_table_header(cells) or is_table_separator(cells):
            continue

        name = cells[0].strip()
        if not name:
            continue

        skills.append(
            {
                "name": name,
                "category": cells[1].strip() if len(cells) > 1 else "",
                "source": cells[2].strip() if len(cells) > 2 else "",
                "trust": cells[3].strip() if len(cells) > 3 else "",
                "status": cells[4].strip() if len(cells) > 4 else "",
                "raw": line,
            }
        )

    if skills:
        return skills

    return parse_category_list_fallback(text)


def split_table_row(line: str):
    stripped = (line or "").strip()
    if not stripped:
        return []

    if is_box_border(stripped):
        return []

    separators = "|│┃"
    if stripped[0] not in separators or not any(ch in stripped[1:] for ch in separators):
        return []

    cells = [cell.strip() for cell in re.split(r"[|│┃]", stripped.strip(" |│┃"))]
    cells = [strip_table_cell_noise(cell) for cell in cells]
    cells = [cell for cell in cells if cell or len(cells) >= 5]
    return cells


def strip_table_cell_noise(cell: str) -> str:
    return (cell or "").strip().strip("┆┊╎╏")


def is_box_border(line: str) -> bool:
    border_chars = set("┏┓┗┛┡┩┬┴┳┻╇╈╋┌┐└┘├┤┼─━═╞╡╪╤╧╟╢╫ ")
    return bool(line) and set(line) <= border_chars


def parse_category_list_fallback(text: str):
    skills = []
    for line in (text or "").splitlines():
        if ":" not in line:
            continue

        category, names = line.split(":", 1)
        category = category.strip()
        if not category or len(category.split()) > 4:
            continue

        for name in re.split(r",\s*", names.strip()):
            name = name.strip().strip(".")
            if name:
                skills.append(
                    {
                        "name": name,
                        "category": category,
                        "source": "",
                        "trust": "",
                        "status": "",
                        "raw": line,
                    }
                )
    return skills


def is_table_header(cells):
    lowered = [cell.lower() for cell in cells]
    return "name" in lowered and "status" in lowered


def is_table_separator(cells):
    normalized = "".join(cells).replace("─", "-").replace("━", "-").replace("—", "-").replace(" ", "")
    return bool(normalized) and set(normalized) <= {"-", ":"}


class SkillsGridWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.skills = []
        self.cards = []
        self.columns = 1
        self.hovered_card = None

        self.container = QWidget()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.container)

        self.empty_label = QLabel("No skills found.", self.container)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setObjectName("skillsEmptyLabel")
        self.empty_label.hide()

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)
        self.setLayout(layout)

    def set_skills_from_text(self, text: str):
        self.skills = parse_hermes_skills_text(text)
        self.rebuild_grid()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_cards(animated=False)

    def rebuild_grid(self):
        self.clear_grid()

        if not self.skills:
            self.empty_label.show()
            self.position_cards(animated=False)
            return

        for index, skill in enumerate(self.skills):
            card = SkillCard(skill, self.container)
            card.index = index
            card.clicked.connect(self.show_skill_detail)
            card.hover_changed.connect(self.handle_card_hover)
            self.cards.append(card)
            card.show()

        self.empty_label.hide()
        self.position_cards(animated=False)

    def clear_grid(self):
        self.hovered_card = None
        for card in self.cards:
            card.deleteLater()
        self.cards = []

    def position_cards(self, animated: bool):
        width = max(self.scroll.viewport().width(), self.width(), 1)
        self.columns = max(1, (width - GRID_MARGIN * 2 + CARD_GAP) // (CARD_NORMAL_W + CARD_GAP))

        if not self.cards:
            self.container.setMinimumHeight(160)
            self.empty_label.setGeometry(0, 0, max(width, 320), 150)
            return

        rows = (len(self.cards) + self.columns - 1) // self.columns
        self.container.setMinimumHeight(
            GRID_MARGIN * 2 + rows * CARD_NORMAL_H + max(0, rows - 1) * CARD_GAP + 26
        )

        hovered_index = self.hovered_card.index if self.hovered_card else -1
        hovered_row = hovered_index // self.columns if hovered_index >= 0 else -1
        hovered_col = hovered_index % self.columns if hovered_index >= 0 else -1

        for index, card in enumerate(self.cards):
            row = index // self.columns
            col = index % self.columns
            base = self.base_rect(row, col)

            if card is self.hovered_card:
                target = centered_rect(base, CARD_HOVER_W, CARD_HOVER_H)
                card.raise_()
            elif hovered_index >= 0 and max(abs(row - hovered_row), abs(col - hovered_col)) <= 1:
                target = centered_rect(base, CARD_SHRINK_W, CARD_SHRINK_H)
            else:
                target = base

            card.move_to(target, animated)

    def base_rect(self, row: int, col: int) -> QRect:
        x = GRID_MARGIN + col * (CARD_NORMAL_W + CARD_GAP)
        y = GRID_MARGIN + row * (CARD_NORMAL_H + CARD_GAP)
        return QRect(x, y, CARD_NORMAL_W, CARD_NORMAL_H)

    def handle_card_hover(self, card, hovered: bool):
        if hovered:
            self.hovered_card = card
        elif self.hovered_card is card:
            self.hovered_card = None

        self.position_cards(animated=True)

    def show_skill_detail(self, skill: dict):
        dialog = SkillDetailDialog(skill, self)
        dialog.exec()


class SkillCard(QFrame):
    clicked = Signal(object)
    hover_changed = Signal(object, bool)

    def __init__(self, skill: dict, parent=None):
        super().__init__(parent)
        self.skill = skill
        self.index = -1
        self.animation = None

        self.setObjectName("skillCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.icon_label = QLabel(skill_initial(skill.get("name", "")))
        self.icon_label.setObjectName("skillIcon")
        self.icon_label.setAlignment(Qt.AlignCenter)

        self.name_label = QLabel(skill.get("name", "Unnamed"))
        self.name_label.setObjectName("skillName")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)

        self.category_label = QLabel(skill.get("category") or "uncategorized")
        self.category_label.setObjectName("skillCategory")
        self.category_label.setAlignment(Qt.AlignCenter)
        self.category_label.setWordWrap(True)

        self.status_label = QLabel(skill.get("status") or "unknown")
        self.status_label.setObjectName(status_object_name(skill.get("status", "")))
        self.status_label.setAlignment(Qt.AlignCenter)

        self.content_layout = QVBoxLayout()
        self.content_layout.addWidget(self.icon_label, 0, Qt.AlignCenter)
        self.content_layout.addWidget(self.name_label)
        self.content_layout.addWidget(self.category_label)
        self.content_layout.addWidget(self.status_label, 0, Qt.AlignCenter)
        self.setLayout(self.content_layout)
        self.update_inner_scale(1.0)

    def enterEvent(self, event):
        self.raise_()
        self.hover_changed.emit(self, True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_changed.emit(self, False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.skill)
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        scale = max(0.78, min(1.24, self.width() / CARD_NORMAL_W))
        self.update_inner_scale(scale)

    def update_inner_scale(self, scale: float):
        margin = max(7, round(10 * scale))
        spacing = max(3, round(5 * scale))
        icon_size = max(30, round(38 * scale))
        icon_radius = icon_size // 2
        name_size = max(10, round(13 * scale))
        category_size = max(9, round(11 * scale))
        status_size = max(8, round(10 * scale))
        status_pad_y = max(1, round(2 * scale))
        status_pad_x = max(6, round(8 * scale))
        status_radius = max(6, round(8 * scale))

        self.content_layout.setContentsMargins(margin, margin, margin, margin)
        self.content_layout.setSpacing(spacing)
        self.icon_label.setFixedSize(icon_size, icon_size)
        self.icon_label.setStyleSheet(
            f"""
            min-width: {icon_size}px;
            min-height: {icon_size}px;
            max-width: {icon_size}px;
            max-height: {icon_size}px;
            border-radius: {icon_radius}px;
            font-size: {max(10, round(13 * scale))}px;
            """
        )
        self.name_label.setStyleSheet(f"font-size: {name_size}px;")
        self.category_label.setStyleSheet(f"font-size: {category_size}px;")
        self.status_label.setStyleSheet(
            f"""
            font-size: {status_size}px;
            padding: {status_pad_y}px {status_pad_x}px;
            border-radius: {status_radius}px;
            """
        )

    def move_to(self, rect: QRect, animated: bool):
        if self.animation:
            self.animation.stop()
            self.animation = None

        if not animated:
            self.setGeometry(rect)
            return

        animation = QPropertyAnimation(self, b"geometry")
        animation.setDuration(170)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.setEndValue(rect)
        self.animation = animation
        animation.finished.connect(lambda: setattr(self, "animation", None))
        animation.start(QAbstractAnimation.DeleteWhenStopped)


def centered_rect(base: QRect, width: int, height: int) -> QRect:
    return QRect(
        base.center().x() - width // 2,
        base.center().y() - height // 2,
        width,
        height,
    )


class SkillDetailDialog(QDialog):
    def __init__(self, skill: dict, parent=None):
        super().__init__(parent)

        self.setWindowTitle(skill.get("name", "Skill Details"))
        self.setModal(True)
        self.resize(520, 360)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #ffffff;
            }

            QTextEdit {
                border: 1px solid #d0d7de;
                border-radius: 8px;
                padding: 8px;
                background-color: #ffffff;
                color: #24292f;
            }
            """
            + SKILLS_GRID_STYLE
        )

        title = QLabel(skill.get("name", "Unnamed skill"))
        title.setObjectName("skillDetailTitle")

        fields = QTextEdit()
        fields.setReadOnly(True)
        fields.setPlainText(
            "\n".join(
                [
                    f"Name: {skill.get('name') or '-'}",
                    f"Category: {skill.get('category') or '-'}",
                    f"Source: {skill.get('source') or '-'}",
                    f"Trust: {skill.get('trust') or '-'}",
                    f"Status: {skill.get('status') or '-'}",
                    "",
                    "Raw details:",
                    skill.get("raw") or "-",
                ]
            )
        )

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(fields)
        self.setLayout(layout)


def skill_initial(name: str) -> str:
    parts = re.split(r"[-_\s]+", name.strip())
    letters = "".join(part[:1].upper() for part in parts if part)
    return (letters or "?")[:2]


def status_object_name(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "enabled":
        return "skillStatusEnabled"
    if normalized == "disabled":
        return "skillStatusDisabled"
    return "skillStatus"


SKILLS_GRID_STYLE = """
    QScrollArea {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        background-color: #ffffff;
    }

    QFrame#skillCard {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        background-color: #ffffff;
    }

    QFrame#skillCard:hover {
        border-color: #8c959f;
        background-color: #f6f8fa;
    }

    QLabel#skillIcon {
        min-width: 38px;
        min-height: 38px;
        max-width: 38px;
        max-height: 38px;
        border-radius: 19px;
        background-color: #ddf4ff;
        color: #0969da;
        font-weight: 800;
    }

    QLabel#skillName {
        color: #24292f;
        font-size: 13px;
        font-weight: 800;
    }

    QLabel#skillCategory {
        color: #57606a;
        font-size: 11px;
    }

    QLabel#skillStatus,
    QLabel#skillStatusEnabled,
    QLabel#skillStatusDisabled {
        border-radius: 8px;
        padding: 2px 8px;
        font-size: 10px;
        font-weight: 700;
    }

    QLabel#skillStatusEnabled {
        background-color: #dafbe1;
        color: #116329;
    }

    QLabel#skillStatusDisabled {
        background-color: #ffebe9;
        color: #82071e;
    }

    QLabel#skillStatus {
        background-color: #f6f8fa;
        color: #57606a;
    }

    QLabel#skillsEmptyLabel {
        color: #57606a;
        padding: 24px;
    }

    QLabel#skillDetailTitle {
        color: #24292f;
        font-size: 20px;
        font-weight: 800;
    }
"""
