"""Project details and the resizable desktop workspace."""

from PySide6.QtCore import QPointF, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QPushButton, QSplitter, QSplitterHandle, QTextEdit, QHBoxLayout, QVBoxLayout, QWidget

from desktop_theme import configure_text, ui_font
from handoff_sync import atomic_write


class ProjectIntroPage(QWidget):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.key = None
        self.drafts = {}
        self.timer = QTimer(self, interval=500, singleShot=True)
        self.timer.timeout.connect(self.flush)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(8)
        caption = QLabel('项目介绍')
        caption.setFont(ui_font(9))
        layout.addWidget(caption)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setFont(ui_font(13, True))
        layout.addWidget(self.title)
        self.path = QLabel()
        self.path.setTextFormat(Qt.TextFormat.PlainText)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setWordWrap(True)
        layout.addWidget(self.path)
        self.purpose = self.add_field(layout, '项目用途', '写下这个项目是做什么的', 1)
        self.progress = self.add_field(layout, '当前进展', '写下已完成的工作、下一步或遇到的问题', 2)
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self.save_button = QPushButton('保存介绍')
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.flush)
        row.addWidget(self.save_button)
        layout.addLayout(row)

    def add_field(self, layout, title, placeholder, stretch):
        label = QLabel(title)
        label.setFont(ui_font(10, True))
        layout.addWidget(label)
        editor = QTextEdit()
        editor.setObjectName('projectIntroEditor')
        editor.setAccessibleName(title)
        editor.setAcceptRichText(False)
        editor.setPlaceholderText(placeholder)
        editor.setMinimumHeight(64)
        configure_text(editor, 10, 5)
        editor.textChanged.connect(self.changed)
        label.setBuddy(editor)
        layout.addWidget(editor, stretch)
        return editor

    def show_project(self, key, title, path):
        self.title.setText(title)
        self.path.setText(path)
        if key == self.key:
            return
        self.flush()
        self.key = key
        error = ''
        try:
            values = self.drafts[key] if key in self.drafts else self.store.load(key)
        except (OSError, ValueError) as failure:
            values, error = ('', ''), str(failure)
        for editor, value in zip((self.purpose, self.progress), values):
            with QSignalBlocker(editor):
                editor.setPlainText(value)
                editor.setReadOnly(bool(error))
        self.status.setText(error or ('尚未保存' if key in self.drafts else ''))
        self.save_button.setEnabled(key in self.drafts)

    def changed(self):
        if self.key is None:
            return
        self.drafts[self.key] = self.purpose.toPlainText(), self.progress.toPlainText()
        self.status.setText('正在保存…')
        self.save_button.setEnabled(True)
        self.timer.start()

    def flush(self):
        self.timer.stop()
        for key, (purpose, progress) in tuple(self.drafts.items()):
            try:
                self.store.save(key, purpose, progress)
            except (OSError, ValueError) as error:
                if key == self.key:
                    self.status.setText(str(error))
                continue
            del self.drafts[key]
            if key == self.key:
                self.status.setText('已保存')
                self.save_button.setEnabled(False)
        return not self.drafts


class WorkspaceSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setAccessibleName('调整左右栏宽度')
        self.setToolTip('拖动调整左右栏宽度')
        self.dragging = False

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.update()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.dragging = False
        self.update()

    def paintEvent(self, _event):
        if not (self.underMouse() or self.dragging):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#d9f3ff'))
        for offset in (-6, 0, 6):
            painter.drawEllipse(QPointF(self.width() / 2, self.height() / 2 + offset), 1.75, 1.75)


class WorkspaceSplitter(QSplitter):
    def __init__(self, root):
        super().__init__(Qt.Orientation.Horizontal)
        self.path = root / '.local' / 'desktop-sidebar-width'
        self.setChildrenCollapsible(False)
        self.setHandleWidth(7)
        self.dirty = False
        self.timer = QTimer(self, interval=300, singleShot=True)
        self.timer.timeout.connect(self.flush)
        self.splitterMoved.connect(self.remember)

    def createHandle(self):
        return WorkspaceSplitterHandle(self.orientation(), self)

    def restore_width(self, content_width):
        try:
            width = max(180, min(640, int(self.path.read_text(encoding='ascii'))))
        except (OSError, ValueError):
            width = 266
        self.setSizes([width, max(380, content_width - width - self.handleWidth())])

    def remember(self, _position, _index):
        self.dirty = True
        self.timer.start()

    def flush(self):
        if not self.dirty:
            return
        self.timer.stop()
        width = self.sizes()[0]
        if width:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self.path, str(width).encode('ascii'))
                self.dirty = False
            except OSError:
                self.handle(1).setToolTip('拖动调整左右栏宽度。本次宽度未能保存。')
