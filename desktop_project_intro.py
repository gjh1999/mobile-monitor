"""Project details and the resizable desktop workspace."""

from pathlib import Path

from PySide6.QtCore import QPointF, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter, QSplitterHandle,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from desktop_theme import configure_text, ui_font
from handoff_sync import atomic_write


class ProjectIntroPreview(QLabel):
    edit_requested = Signal()

    def mouseDoubleClickEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.edit_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2:
            self.edit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ProjectIntroEditor(QTextEdit):
    save_requested = Signal()
    cancel_requested = Signal()

    def __init__(self):
        super().__init__()
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.composing = False

    def keyPressEvent(self, event):
        if not self.composing:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.insertPlainText('\n')
                elif not event.isAutoRepeat():
                    self.save_requested.emit()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class ProjectIntroField(QStackedWidget):
    def __init__(self, title, placeholder):
        super().__init__()
        self.preview = ProjectIntroPreview()
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.preview.setAccessibleName(title)
        self.preview.setAccessibleDescription('双击文字可编辑，键盘可按 F2。')
        self.preview.setToolTip('双击编辑')
        self.addWidget(self.preview)
        self.editor = ProjectIntroEditor()
        self.editor.setObjectName('projectIntroEditor')
        self.editor.setAccessibleName(title)
        self.editor.setAccessibleDescription('Enter 保存，Shift+Enter 换行，Esc 取消。')
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText(placeholder)
        self.editor.setMinimumHeight(64)
        configure_text(self.editor, 10, 5)
        self.addWidget(self.editor)

    def show_value(self, value, draft=None, *, editable=True):
        self.preview.setText(value if value.strip() else '双击添加')
        self.preview.setEnabled(editable)
        with QSignalBlocker(self.editor):
            self.editor.setPlainText(value if draft is None else draft)
        self.setCurrentWidget(self.preview if draft is None else self.editor)


class ProjectIntroPage(QWidget):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.key = None
        self.drafts = {}
        self.values = ('', '')
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
        self.repository = self.metadata_label(layout)
        self.branch = self.metadata_label(layout)
        self.open_button = QPushButton('打开文件夹')
        self.open_button.clicked.connect(self.open_folder)
        layout.addWidget(self.open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.purpose = self.add_field(layout, 0, '项目用途', '写下这个项目是做什么的', 1)
        self.progress = self.add_field(layout, 1, '当前进展', '写下已完成的工作、下一步或遇到的问题', 2)
        self.fields = (self.purpose, self.progress)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    @staticmethod
    def metadata_label(layout):
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        label.hide()
        layout.addWidget(label)
        return label

    def add_field(self, layout, index, title, placeholder, stretch):
        label = QLabel(title)
        label.setFont(ui_font(10, True))
        layout.addWidget(label)
        field = ProjectIntroField(title, placeholder)
        field.preview.edit_requested.connect(lambda: self.edit_field(index))
        field.editor.save_requested.connect(lambda: self.save_field(index))
        field.editor.cancel_requested.connect(lambda: self.cancel_field(index))
        field.editor.textChanged.connect(lambda: self.changed(index))
        label.setBuddy(field.preview)
        layout.addWidget(field, stretch)
        return field

    def show_project(self, key, title, path, *, repository='', branch=''):
        self.title.setText(title)
        self.path.setText(path)
        self.open_button.setEnabled(bool(path))
        for label, prefix, value in ((self.repository, 'Git 仓库', repository), (self.branch, '当前分支', branch)):
            label.setText(f'{prefix} · {value}' if value else '')
            label.setVisible(bool(value))
        if key == self.key:
            return
        self.key = key
        error = ''
        try:
            self.values = self.store.load(key)
        except (OSError, ValueError) as failure:
            self.values, error = ('', ''), str(failure)
        drafts = self.drafts.get(key, {})
        for index, field in enumerate(self.fields):
            field.show_value(self.values[index], drafts.get(index) if not error else None, editable=not error)
        self.status.setText(error or ('尚未保存' if drafts else ''))

    def open_folder(self):
        path = Path(self.path.text())
        if (not path.is_absolute() or not path.is_dir()
                or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))):
            self.status.setText('暂时无法打开本机项目文件夹。')

    def edit_field(self, index):
        field = self.fields[index]
        if self.key is None or not field.preview.isEnabled():
            return
        field.setCurrentWidget(field.editor)
        field.editor.setFocus()

    def changed(self, index):
        value = self.fields[index].editor.toPlainText()
        if value == self.values[index]:
            self.remove_draft(index)
        else:
            self.drafts.setdefault(self.key, {})[index] = value
        self.status.setText('尚未保存' if self.key in self.drafts else '')

    def remove_draft(self, index):
        if self.key in self.drafts:
            drafts = self.drafts[self.key]
            drafts.pop(index, None)
            if not drafts:
                del self.drafts[self.key]

    def cancel_field(self, index):
        self.remove_draft(index)
        field = self.fields[index]
        field.show_value(self.values[index])
        field.preview.setFocus()
        self.status.setText('尚未保存' if self.key in self.drafts else '')

    def save_field(self, index):
        draft = self.drafts.get(self.key, {}).get(index)
        if draft is not None:
            try:
                self.values = self.write_changes(self.key, {index: draft})
            except (OSError, ValueError) as error:
                self.status.setText(str(error))
                return
        self.cancel_field(index)
        if self.key not in self.drafts and draft is not None:
            self.status.setText('已保存')

    def write_changes(self, key, changes):
        values = list(self.store.load(key))
        for index, text in changes.items():
            values[index] = text
        self.store.save(key, *values)
        return tuple(values)

    def confirm_exit(self):
        if not self.drafts:
            return True
        box = QMessageBox(self.window())
        box.setWindowTitle('项目介绍')
        box.setText('还有未保存的项目介绍。')
        save = box.addButton('保存并退出', QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton('丢弃并退出', QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is discard:
            return True
        if box.clickedButton() is not save:
            return False
        for key, drafts in tuple(self.drafts.items()):
            try:
                values = self.write_changes(key, drafts)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self.window(), '项目介绍保存失败', str(error))
                return False
            del self.drafts[key]
            if key == self.key:
                self.values = values
                for index, field in enumerate(self.fields):
                    field.show_value(values[index])
                self.status.setText('已保存')
        return True


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
