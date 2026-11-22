"""Native task workspace: light cards, soft-blue actions and explicit failures."""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path

from PySide6.QtCore import QEvent, QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSharedMemory, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QDrag, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCompleter, QDialog, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QStyle,
    QStyleOptionViewItem, QSystemTrayIcon,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

import monitor
from cloud_pairing import bundled_workspace
from code_sync import trust_git_directory
from codex_approval_alerts import (
    configure as configure_approval_alerts, hook_home, hook_status, take_alerts,
)
from desktop_approvals import ApprovalQueueDialog, INACTIVE, approval_status
from desktop_glass import ClientSwitch, FrostedPanel, OceanBackdrop, motion_enabled
from desktop_diagnostics import DiagnosticsDialog
from desktop_model_api import ModelApiDialog
from desktop_notebook import NotebookStore, NotebookWorkspace
from desktop_pairing import CONNECT_CLOUD, PairingDialog, WorkspaceConnectDialog
from desktop_poetry import COVER_VERSES
from desktop_wallpapers import WallpaperDialog
from desktop_projects import SessionTitles, folder_name, project_group
from desktop_project_sync import POLICIES, ProjectSyncDialog
from desktop_project_intro import ProjectIntroPage, WorkspaceSplitter
from desktop_retirement import RetirementDialog
from desktop_theme import (
    SLASH_MENU_STYLE, STYLESHEET, configure_text, format_reading_document, insert_markdown,
    message_html, process_html, ui_font, warning_html, web_link,
)
from desktop_update import DesktopUpdates
from model_groups import Organizer
from portable_update import VERSION
from plain_projects import folder_id, receiver_placeholder
from project_sync_policy import ProjectSyncSettings
from project_intro import ProjectIntroStore
from project_library import ENTRY_PREFIX, configured_root, set_project_root
from rpc_errors import RPCError
from task_tree_preferences import TaskTreePreferences

MUTED = '#24495f'
WINDOW_TITLE = '澜汐'
TASK_TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
TASK_DIRECTORY_ROLE = Qt.ItemDataRole.UserRole + 2
PROJECT_CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 3
PROJECT_WORKSPACE_ROLE = Qt.ItemDataRole.UserRole + 4
UUID_PATTERN = re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')


def display_text(value):
    """Identifiers remain in data, not in task captions or operation notices."""
    return UUID_PATTERN.sub('此任务', str(value or ''))


def load_conversation(key, previous, font, option, width, gui_thread, stop):
    if stop.is_set():
        return None
    client, thread = key
    data = monitor.conversation_for(thread, client)
    if data == previous or stop.is_set():
        return None
    bubbles = []
    roles = []
    replies = []
    for message in data.get('messages') or []:
        if message.get('role') == 'process':
            bubbles.append(process_html(message))
            roles.append('process')
            replies.append('')
            continue
        assistant = message.get('role') == 'assistant'
        role = ('Claude Code' if client == 'claude' else 'Codex') if assistant else '你'
        text = message.get('text', '')
        bubbles.append(message_html(role, '' if assistant else text, assistant))
        roles.append(assistant)
        replies.append(text)
    errors = {json.dumps(event, sort_keys=True, ensure_ascii=False) for event in data.get('events') or []
              if isinstance(event.get('status'), dict) and event['status'].get('error')}
    for encoded in sorted(errors):
        bubbles.append(warning_html(display_text(json.loads(encoded)['status']['error'])))
    # Text-only documents are reentrant. Build and lay out an unparented
    # document here, then transfer ownership before handing it to the widget.
    document = QTextDocument()
    document.setLayoutEnabled(False)
    document.setDefaultFont(font)
    document.setDefaultTextOption(option)
    document.setDocumentMargin(3)
    document.setIndentWidth(24)
    document.setDefaultStyleSheet('p { margin-top:3px; margin-bottom:8px; }')
    document.setUndoRedoEnabled(False)
    document.setHtml(''.join(bubbles) or '<p style="color:#61738c;">灯下尚无来信。写下第一句吧。</p>')
    # Only the leading tables are messages. Warnings follow them and code
    # tables are children of the corresponding message.
    document.message_frames = tuple(zip(document.rootFrame().childFrames(), roles))
    for (frame, assistant), text in zip(document.message_frames, replies):
        if stop.is_set():
            return None
        if assistant is True:
            insert_markdown(frame.cellAt(0, 0).lastCursorPosition(), text)
    format_reading_document(document)
    document.setTextWidth(width)
    document.setLayoutEnabled(True)
    document.size()
    if stop.is_set():
        return None
    if not document.moveToThread(gui_thread):
        raise RuntimeError('无法显示对话')
    return data, document, errors


def label(text, size=13, bold=False, color=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setFont(ui_font(size, bold))
    widget.setWordWrap(True)
    if color:
        widget.setStyleSheet(f'color:{color};')
    return widget


def vscode_executable():
    found = shutil.which('Code.exe')
    if found:
        return Path(found)
    launcher = shutil.which('code.cmd')
    candidates = [Path(launcher).parent.parent / 'Code.exe'] if launcher else []
    for root in ('LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)'):
        if os.environ.get(root):
            directory = Path(os.environ[root])
            if root == 'LOCALAPPDATA':
                directory /= 'Programs'
            candidates.append(directory / 'Microsoft VS Code' / 'Code.exe')
    return next((candidate for candidate in candidates if candidate.is_file()), None)


class TaskTitle(QLabel):
    edit_requested = Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.edit_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class VerseTitle(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        baseline = (self.height() + self.fontMetrics().ascent() - self.fontMetrics().descent()) / 2
        glyphs = QPainterPath()
        glyphs.addText(1, baseline, self.font(), self.text())
        ink = QColor('#ffffff')
        painter.fillPath(glyphs, ink)
        # SimSun on Windows supplies only a regular face. Thicken its fine strokes
        # slightly after the native bold weight has been resolved.
        pen = QPen(ink, .85)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(glyphs, pen)


class ProjectTree(QTreeWidget):
    moved = Signal(object, object, str)

    def __init__(self):
        super().__init__()
        self.drag_item = None
        self.drop_target = None
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDropIndicatorShown(False)

    def startDrag(self, _actions):
        self.drag_item = self.currentItem()
        if self.drag_item is None:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([self.drag_item]))
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.drag_item = None
            self.drop_target = None
            self.viewport().update()

    def dragEnterEvent(self, event):
        if event.source() is self and self.drag_item is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def target_at(self, point):
        item = self.itemAt(point)
        source = self.drag_item
        if source is None or item is None or item is source:
            return None
        tid = source.data(0, Qt.ItemDataRole.UserRole)
        target_tid = item.data(0, Qt.ItemDataRole.UserRole)
        valid = (item.parent() is not None if target_tid else bool(item.data(0, PROJECT_WORKSPACE_ROLE))) if tid else (
            not target_tid and bool(item.data(0, PROJECT_WORKSPACE_ROLE)) and source.parent() is item.parent())
        if not valid:
            return None
        rect = self.visualItemRect(item)
        relative = (point.y() - rect.top()) / max(1, rect.height())
        if tid and target_tid and not target_tid.startswith(ENTRY_PREFIX) and .25 <= relative <= .75:
            zone = 'on'
        elif tid and not target_tid:
            zone = 'on'
        else:
            zone = 'before' if relative < .5 else 'after'
        return item, zone

    def dragMoveEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        super().dragMoveEvent(event)
        self.drop_target = self.target_at(event.position().toPoint())
        if self.drop_target:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self.drop_target = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        target = self.target_at(event.position().toPoint()) if event.source() is self else None
        if target is None:
            event.ignore()
            return
        item, zone = target
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.moved.emit(self.drag_item, item, zone)

    def drawRow(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        item_rect = self.visualRect(index)
        painter.save()
        painter.setClipRect(item_rect, Qt.ClipOperation.IntersectClip)
        super().drawRow(painter, option, index)
        painter.restore()

        if self.drop_target is not None and self.itemFromIndex(index) is self.drop_target[0]:
            zone = self.drop_target[1]
            painter.save()
            painter.setPen(QPen(QColor('#327fb2'), 2))
            if zone == 'on':
                painter.drawRect(item_rect.adjusted(1, 1, -2, -2))
            else:
                edge_y = item_rect.top() if zone == 'before' else item_rect.bottom()
                painter.drawLine(item_rect.left(), edge_y, item_rect.right(), edge_y)
            painter.restore()

        if index.parent().isValid() or not self.model().hasChildren(index):
            return
        # Paint the indicator outside the item's selection and hover surface.
        direction = -1 if self.isRightToLeft() else 1
        edge = item_rect.left() if direction == 1 else item_rect.right() + 1
        x = edge - direction * self.indentation() / 2
        y = item_rect.center().y() + .5
        arrow = QPainterPath()
        if self.isExpanded(index):
            arrow.moveTo(x - 4, y - 2)
            arrow.lineTo(x + 4, y - 2)
            arrow.lineTo(x, y + 2)
        else:
            arrow.moveTo(x - direction * 2, y - 4)
            arrow.lineTo(x - direction * 2, y + 4)
            arrow.lineTo(x + direction * 2, y)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(arrow, QColor('#466a7e'))
        painter.restore()


class ConversationBrowser(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.anchorClicked.connect(self.open_link)

    def open_link(self, url):
        if web_link(url):
            QDesktopServices.openUrl(url)

    def paintEvent(self, event):
        document = self.document()
        messages = getattr(document, 'message_frames', ())
        if messages:
            x, y = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
            exposed = QRectF(event.rect()).translated(x, y)
            layout = document.documentLayout()
            with QPainter(self.viewport()) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.translate(-x, -y)
                painter.setPen(Qt.PenStyle.NoPen)
                for frame, assistant in messages:
                    cell = frame.cellAt(0, 0 if assistant else 1)
                    padding = cell.format().toTableCellFormat()
                    text = layout.blockBoundingRect(cell.firstCursorPosition().block())
                    box = QRectF(text.left() - padding.leftPadding(), text.top() - padding.topPadding(),
                                 text.width() + padding.leftPadding() + padding.rightPadding(),
                                 layout.frameBoundingRect(frame).height())
                    if box.top() >= exposed.bottom():
                        break
                    if not box.intersects(exposed):
                        continue
                    if assistant == 'process':
                        painter.setBrush(QColor(207, 226, 234, 231))
                        painter.drawRoundedRect(box, 8, 8)
                        marker = QRectF(box.left() + 3, box.top() + 10, 2, 12)
                        painter.setBrush(QColor('#4f8194'))
                        painter.drawRoundedRect(marker, 1, 1)
                    else:
                        painter.setBrush(QColor(235, 249, 255, 132) if assistant else QColor(218, 240, 253, 172))
                        painter.drawRoundedRect(box, 11, 11)
        super().paintEvent(event)


class ActionHeader(QFrame):
    """Reveal actions without moving the conversation or losing keyboard access."""
    def __init__(self):
        super().__init__()
        self.actions = QFrame()
        self.opacity = QGraphicsOpacityEffect(self.actions)
        self.opacity.setOpacity(0.0)
        self.actions.setGraphicsEffect(self.opacity)
        self.animation = QPropertyAnimation(self.opacity, b'opacity', self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.target = 0.0
        self.hovered = False
        QApplication.instance().focusChanged.connect(self.sync_actions)

    def sync_actions(self, *_):
        focus = QApplication.focusWidget()
        value = float(self.hovered or (focus is not None and self.actions.isAncestorOf(focus)))
        if value == self.target:
            return
        self.target = value
        self.animation.stop()
        if motion_enabled():
            self.animation.setStartValue(self.opacity.opacity())
            self.animation.setEndValue(value)
            self.animation.start()
        else:
            self.opacity.setOpacity(value)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hovered = True
        self.sync_actions()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.hovered = False
        self.sync_actions()


class EdgeMenu(QWidget):
    HEIGHT = 22
    HOT_ZONE = 4

    def __init__(self, backdrop, menus, *, actions=()):
        super().__init__(backdrop)
        self.revealed = False
        self.active_menu = None
        self.menus = {}
        self.panel = FrostedPanel(backdrop, radius=0, tint=QColor(13, 51, 69, 203), edge=QColor(205, 239, 250, 60))
        self.panel.setParent(self)
        self.panel.hide()
        self.opacity = QGraphicsOpacityEffect(self.panel)
        self.opacity.setOpacity(0.0)
        self.panel.setGraphicsEffect(self.opacity)
        self.fade = QPropertyAnimation(self.opacity, b'opacity', self)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self.finish_fade)
        self.leave_delay = QTimer(self)
        self.leave_delay.setSingleShot(True)
        self.leave_delay.setInterval(100)
        self.leave_delay.timeout.connect(lambda: self.reveal(False))

        row = QHBoxLayout(self.panel)
        row.setContentsMargins(24, 0, 24, 0)
        row.setSpacing(5)
        for title, entries in menus.items():
            button = self.button(title)
            menu = QMenu(button)
            self.menus[title] = menu
            for caption, callback in entries:
                menu.addAction(caption, callback)
            button.setMenu(menu)
            menu.aboutToShow.connect(lambda menu=menu: self.menu_opened(menu))
            menu.aboutToHide.connect(self.menu_closed)
            row.addWidget(button)
        for caption, callback, tooltip in actions:
            button = self.button(caption)
            button.setToolTip(tooltip)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()

        self.resize(backdrop.width(), self.HOT_ZONE)
        backdrop.installEventFilter(self)

    @staticmethod
    def button(text):
        button = QPushButton(text)
        button.setObjectName('edgeMenuItem')
        button.setFont(ui_font(9, text == WINDOW_TITLE))
        button.setFixedHeight(20)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def menu_opened(self, menu):
        self.leave_delay.stop()
        self.active_menu = menu

    def menu_closed(self):
        self.active_menu = None
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.schedule_hide()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.resize(event.size().width(), self.height())
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        self.panel.resize(event.size().width(), self.HEIGHT)
        super().resizeEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.leave_delay.stop()
        self.reveal(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.active_menu is None:
            self.schedule_hide()

    def schedule_hide(self):
        if self.revealed:
            if motion_enabled():
                self.leave_delay.start()
            else:
                self.reveal(False)

    def reveal(self, value):
        if not self.isVisible() or self.revealed == value:
            return
        self.revealed = value
        self.fade.stop()
        if value:
            self.raise_()
            self.resize(self.width(), self.HEIGHT)
            self.panel.show()
        end = float(value)
        if motion_enabled():
            self.fade.setDuration(190 if value else 210)
            self.fade.setStartValue(self.opacity.opacity())
            self.fade.setEndValue(end)
            self.fade.start()
        else:
            self.opacity.setOpacity(end)
            self.finish_fade()

    def finish_fade(self):
        if not self.revealed:
            self.panel.hide()
            self.resize(self.width(), self.HOT_ZONE)

    def hideEvent(self, event):
        self.leave_delay.stop()
        self.fade.stop()
        self.revealed = False
        self.opacity.setOpacity(0.0)
        self.finish_fade()
        super().hideEvent(event)


class ActionButton(QPushButton):
    """160ms hover/press elevation, without moving or resizing the hit target."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName('taskAction')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.effect = QGraphicsDropShadowEffect(self)
        self.effect.setColor(QColor(43, 99, 198, 26))
        self.effect.setOffset(0, 1)
        self.effect.setBlurRadius(0)
        self.setGraphicsEffect(self.effect)
        self.animation = QPropertyAnimation(self.effect, b'blurRadius', self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def animate_to(self, radius):
        self.animation.stop()
        if not motion_enabled():
            self.effect.setBlurRadius(radius if self.isEnabled() else 0)
            return
        self.animation.setStartValue(self.effect.blurRadius())
        self.animation.setEndValue(radius if self.isEnabled() else 0)
        self.animation.start()

    def enterEvent(self, event):
        self.animate_to(12)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animate_to(0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.animate_to(2)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.animate_to(12 if self.underMouse() else 0)
        super().mouseReleaseEvent(event)


class Composer(QTextEdit):
    send_requested = Signal()
    slash_requested = Signal(str)
    SLASH_OPTIONS = {'/model': '选择模型', '/permissions': '调整权限'}

    def __init__(self):
        super().__init__()
        self.codex_slash = True
        self._composing = False
        self.slash = QCompleter([f'{command}   {title}' for command, title in self.SLASH_OPTIONS.items()], self)
        self.slash.setWidget(self)
        self.slash.popup().setAccessibleName('Codex 斜杠选项')
        self.slash.popup().setFont(ui_font(10))
        self.slash.popup().setStyleSheet(SLASH_MENU_STYLE)
        self.slash.popup().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.slash.activated[str].connect(lambda choice: self.slash_requested.emit(choice.split()[0]))
        self.textChanged.connect(self.show_slash)

    def show_slash(self):
        prefix = self.toPlainText()
        popup = self.slash.popup()
        if not (self.codex_slash and self.hasFocus() and prefix.startswith('/')
                and any(command.startswith(prefix) for command in self.SLASH_OPTIONS)):
            popup.hide()
            return
        self.slash.setCompletionPrefix(prefix)
        anchor = self.cursorRect()
        anchor.moveLeft(0)
        anchor.setWidth(min(280, self.viewport().width()))
        self.slash.complete(anchor)
        popup.move(self.mapToGlobal(QPoint(0, -popup.height() - 5)))
        popup.setCurrentIndex(self.slash.completionModel().index(0, 0))

    def inputMethodEvent(self, event):
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._composing = False

    def keyPressEvent(self, event):
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and self._composing:
            event.accept()
            return
        if enter and event.isAutoRepeat():
            event.accept()
            return
        popup = self.slash.popup()
        if popup.isVisible():
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                model = self.slash.completionModel()
                step = -1 if event.key() == Qt.Key.Key_Up else 1
                row = (popup.currentIndex().row() + step) % model.rowCount()
                popup.setCurrentIndex(model.index(row, 0))
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                choice = popup.currentIndex().data() or self.slash.currentCompletion()
                if choice:
                    self.slash_requested.emit(choice.split()[0])
                    event.accept()
                    return
            if event.key() == Qt.Key.Key_Escape:
                popup.hide()
                event.accept()
                return
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if enter and modifiers == Qt.KeyboardModifier.ShiftModifier:
            self.insertPlainText('\n')
            event.accept()
            return
        if enter and modifiers in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ManualSyncDialog(QDialog):
    trust_finished = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('合流 · 手动同步')
        self.resize(540, 330)
        self.body = QTextBrowser(self)
        self.untrusted = []
        self.trust_pending = False
        self.trust_button = QPushButton('信任 Git 目录并重试', self)
        self.trust_button.hide()
        self.trust_button.clicked.connect(self.trust_and_retry)
        self.trust_finished.connect(self.on_trust_finished, Qt.ConnectionType.QueuedConnection)
        layout = QVBoxLayout(self)
        layout.addWidget(self.body)
        layout.addWidget(self.trust_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh_result)

    def observe(self, future, progress):
        self.future = future
        self.progress = progress
        self.refresh_result()
        if not future.done():
            self.timer.start()
        self.show()
        self.raise_()
        self.activateWindow()

    def trust_and_retry(self):
        if self.trust_pending:
            return
        path, accepted = QInputDialog.getItem(
            self, '信任 Git 目录', '选择你确认可信的本机仓库。澜汐会将此目录加入当前 Windows 用户的 Git 信任配置。',
            self.untrusted, 0, False,
        )
        if not accepted or path not in self.untrusted:
            return
        self.trust_pending = True
        self.trust_button.setEnabled(False)
        self.trust_button.setText('正在写入 Git 信任配置…')

        def trust():
            detail = ''
            try:
                trust_git_directory(path)
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                detail = str(error) if isinstance(error, ValueError) else '请检查目录与 Git 配置写入权限。'
            self.trust_finished.emit(detail)

        threading.Thread(target=trust, name='lanxi-git-trust', daemon=True).start()

    def on_trust_finished(self, detail):
        self.trust_pending = False
        self.trust_button.setEnabled(True)
        self.trust_button.setText('信任 Git 目录并重试')
        if detail:
            QMessageBox.warning(self, '暂时无法信任该目录', detail)
            return
        self.parent().open_manual_sync(fresh=True)

    def refresh_result(self):
        self.trust_button.hide()
        future = self.future
        if not future.done():
            progress = self.progress()
            lines = []
            if not future.running():
                lines.append('已安排同步，等待后台开始。' if not progress else '下一轮已安排，当前轮次仍在运行。')
            if progress:
                phase, seconds, finished, total, projects = progress
                lines.append(f'{phase} · 已用 {seconds} 秒')
                if total:
                    lines.append(f'仓库已处理 {finished}/{total}')
                    lines.extend(f'{project} · {stage}' for project, stage in sorted(projects.items()))
            elif future.running():
                lines.append('正在检查 GitHub、服务器代码备份、记忆和文件清单…')
            self.body.setPlainText('\n'.join(lines))
            return
        self.timer.stop()
        result = None if future.cancelled() else future.result()
        if result is None:
            self.body.setPlainText('这轮同步未完成。请检查连接和项目登记后重试。')
            return
        status = result['status']
        paths = result['projectPaths']
        synced = sum(status.get(project) == '已同步' for project in paths)
        pending = result['projects'] - synced
        lines = [f"Git 项目 {result['projects']} 个，已同步 {synced} 个，未同步 {pending} 个。"]
        self.untrusted = result.get('untrustedGit') or []
        unlisted = [path for path in self.untrusted if path not in paths.values()]
        lines.extend(f'{path} · Git 拒绝访问该目录' for path in unlisted)
        self.trust_button.setVisible(bool(self.untrusted))
        backups = result.get('codeBackups') or {}
        if backups:
            saved = sum(value == '已备份' for value in backups.values())
            lines.append(f'服务器代码备份已确认 {saved} 个，待处理 {len(backups) - saved} 个。')
        folders = result.get('folderProjects') or {}
        if not paths and not folders:
            lines.append('未发现可同步的 GitHub 项目，请核对项目母目录、仓库 origin 和项目登记。')
        if result['connection']:
            lines.append(result['connection'])
        if result['memoryChecked'] and '记忆' not in status:
            lines.append('项目记忆已检查。')
        elif not result['memoryChecked']:
            lines.append('本轮未检查项目记忆。')
        inventory = result['largeInventory']
        if inventory is None:
            lines.append('本轮未取得大文件清单。')
        elif inventory:
            lines.append(f'大文件清单有 {sum(map(len, inventory.values()))} 个文件需核对。')
            for project, entries in sorted(inventory.items()):
                for name, entry in sorted(entries.items()):
                    state = '本机缺少' if entry['localSize'] is None else '大小不同'
                    lines.append(f'{project}/{name} · {state}')
        elif '大文件' not in status:
            lines.append('大文件清单未发现缺少或大小不同的文件。')
        if folders:
            lines.append(f'普通项目会话映射 {len(folders)} 个。')
            for key, path in sorted(folders.items(), key=lambda entry: str(entry[1]).casefold()):
                lines.append(f"{path} · {status.get(key, '已登记')}")
                git_result = (result.get('folderGit') or {}).get(key)
                if git_result:
                    lines.append(f"    Git 接入 · {git_result}")
        lines.extend(f'{key} · {value}' for key, value in sorted(status.items()) if key not in paths and key not in folders)
        for project, path in sorted(paths.items(), key=lambda row: (status.get(row[0]) == '已同步', row[0])):
            lines.append(f"{project} · {status.get(project, '未检查')}")
            if project in backups:
                lines.append('    服务器备份 · ' + backups[project])
            if path:
                lines.append('    ' + path)
        self.body.setPlainText('\n'.join(lines))


class MainWindow(QMainWindow):
    history_ready = Signal(object, object)
    submission_ready = Signal(object, object, object)
    title_submission_ready = Signal(object, object, object)
    approval_ready = Signal(object)
    codex_options_ready = Signal(object, object)
    grouping_changed = Signal(str)
    code_backup_result = Signal(object, object, object)

    def __init__(self, shared, workspace=None):
        super().__init__()
        QApplication.instance().setStyle('Fusion')
        self.setFont(ui_font(10))
        self.shared = shared
        self.code_backup_result.connect(self.show_code_backup_result)
        self.workspace_grant = workspace
        self.stop = threading.Event()
        self.worker = None
        self._quitting = False
        self._shutdown_complete = False
        self._tray_hidden = False
        self._tray_release_action = QAction(self)
        self._tray_release_action.setEnabled(False)
        self._tray_release_action.setVisible(False)
        self.client = 'codex'
        self.selected_thread = ''
        self.client_selections = {'codex': '', 'claude': ''}
        self.task_drafts = {}
        self._pending_operations = {}
        self._local_messages = {}
        self._optimistic_titles = {}
        self._matched_messages = {}
        self._submissions = set()
        self._submission_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lanxi-submit')
        self.submission_ready.connect(self.submission_finished, Qt.ConnectionType.QueuedConnection)
        self.title_submission_ready.connect(self.title_submission_finished, Qt.ConnectionType.QueuedConnection)
        self._started_at = time.time()
        self._seen_operation_states = {'codex': {}, 'claude': {}}
        self._error_events = {}
        self._dialogs = []
        self._task_signature = None
        self.session_titles = SessionTitles()
        self.project_items = {}
        self.category_items = {}
        self._project_sync_view = {}
        self._project_intro_group = None
        self.local_project_settings = ProjectSyncSettings(monitor.ROOT)
        self.tree_preferences = TaskTreePreferences(monitor.ROOT)
        self.task_items = {}
        self.collapsed_projects = {'codex': set(), 'claude': set()}
        self._pending_approvals = {}
        self._shown_approvals = {}
        self._approval_feedback = {}
        self._approval_sending = {}
        self._approval_scope = 'local'
        self._approval_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lanxi-approval')
        self.approval_dialog = None
        self._rename_target = None
        self._project_directory = None
        self._pending_open = None
        self._sync_project = None
        self.notebook = None
        self.diagnostics = None
        self.manual_sync_dialog = None
        self._notebook_revision = 0
        self._last_snapshot = {}
        self._history_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lanxi-history')
        self._history_busy = False
        self._history_repeat = False
        self._history_cache = OrderedDict()
        self._history_scroll = {}
        self._activity_seen = None
        self._visible_history = None
        self._codex_request = None
        self._codex_menu = None
        self.organizer = Organizer(monitor.ROOT, self.grouping_changed.emit)
        self.history_ready.connect(self.history_loaded, Qt.ConnectionType.QueuedConnection)
        self.approval_ready.connect(self.approval_finished, Qt.ConnectionType.QueuedConnection)
        self.codex_options_ready.connect(self.show_codex_options, Qt.ConnectionType.QueuedConnection)
        self.grouping_changed.connect(self.refresh_grouping, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle(WINDOW_TITLE)
        icon_path = Path(getattr(sys, '_MEIPASS', monitor.ROOT)) / 'app.ico'
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(1020, 640)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        self.resize(min(1260, max(1020, available.width() - 40)) if available else 1260,
                    min(840, max(640, available.height() - 50)) if available else 840)
        self.build_ui()
        self._history_placeholder = self.conversation.document()
        self._history_placeholder.setParent(self)
        self.build_tray()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.approval_timer = QTimer(self)
        self.approval_timer.timeout.connect(self.refresh_approvals)
        self.approval_timer.timeout.connect(self.refresh_external_approval_alerts)
        self.approval_timer.start(500)
        self.activity_timer = QTimer(self)
        self.activity_timer.timeout.connect(self.refresh_activity)
        self.activity_timer.start(200)
        _stop, self.worker = monitor.start_background_service(self.stop)
        self.refresh_approvals()
        self.refresh()

    def build_ui(self):
        self.setStyleSheet(STYLESHEET)
        try:
            minutes = monitor.config().get('desktopWallpaperMinutes', 30)
        except (OSError, ValueError, AttributeError):
            minutes = 30
        root = OceanBackdrop(Path(getattr(sys, '_MEIPASS', monitor.ROOT)), minutes, monitor.ROOT)
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(24, EdgeMenu.HEIGHT, 24, 24)
        page.setSpacing(16)

        self.client_switch = ClientSwitch(root, self.select_workspace)
        self.client_buttons = self.client_switch.buttons
        page.addWidget(self.client_switch, 0, Qt.AlignmentFlag.AlignHCenter)

        hero = QFrame()
        hero.setObjectName('desktopHero')
        hero.setFixedHeight(154)
        heading = QHBoxLayout(hero)
        heading.setContentsMargins(35, 6, 9, 9)
        heading.setSpacing(16)
        title = QVBoxLayout()
        title.setSpacing(0)
        verse = self.hero_verse = VerseTitle()
        self.verse_hour = None
        verse.setObjectName('heroVerse')
        verse.setTextFormat(Qt.TextFormat.PlainText)
        verse_font = QFont('SimSun', 42, QFont.Weight.Bold)
        verse_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -1.3)
        verse.setFont(verse_font)
        verse.setWordWrap(False)
        title.addWidget(verse)
        title.addSpacing(2)
        source = self.hero_source = label('', 10, color='#d1e8f5')
        source.setObjectName('heroSource')
        source.setWordWrap(False)
        title.addWidget(source)
        title.addSpacing(10)
        intro = label(
            '<p style="margin:0;line-height:130%"><strong>一处开始，处处继续。</strong>'
            ' 白日写到的那一行，暮归时仍等着你。<br>'
            '终端续上未尽的话，掌中一瞥，便知今日行至何处。</p>',
            12, color='#e3f3fd',
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setObjectName('heroIntro')
        title.addWidget(intro)
        title.addStretch()
        heading.addLayout(title, 1)

        self.summary = FrostedPanel(root, radius=13, tint=QColor(3, 32, 55, 191), edge=QColor(153, 223, 253, 67))
        self.summary.setFixedHeight(60)
        self.summary.setObjectName('oceanStats')
        counts = QHBoxLayout(self.summary)
        counts.setContentsMargins(5, 8, 5, 8)
        counts.setSpacing(0)
        self.stat_total, self.stat_running, self.stat_pending = (label('0', 15, color='#f3fbff') for _ in range(3))
        for index, (value, caption, meaning) in enumerate((
            (self.stat_total, '案头', '任务总数'), (self.stat_running, '行舟', '运行中的任务'),
            (self.stat_pending, '待你定夺', '待审批的请求'),
        )):
            if index:
                divider = QFrame()
                divider.setObjectName('statDivider')
                divider.setFixedSize(1, 35)
                counts.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
            column = QFrame()
            column.setObjectName('statColumn')
            column.setFixedWidth(60)
            column_contents = QVBoxLayout(column)
            column_contents.setContentsMargins(10, 0, 0, 0)
            column_contents.setSpacing(2)
            caption_label = label(caption, 8.5, color='#e4f2fa')
            caption_label.setWordWrap(False)
            caption_label.setToolTip(meaning)
            caption_label.setAccessibleName(meaning)
            column_contents.addWidget(caption_label)
            value.setWordWrap(False)
            value.setAccessibleName(meaning)
            column_contents.addWidget(value)
            counts.addWidget(column)
        heading.addWidget(self.summary, 0, Qt.AlignmentFlag.AlignBottom)
        page.addWidget(hero)

        self.workspace_pages = QStackedWidget()
        self.task_workspace = QWidget()
        self.workspace_pages.addWidget(self.task_workspace)
        page.addWidget(self.workspace_pages, 1)
        middle = QHBoxLayout(self.task_workspace)
        middle.setContentsMargins(0, 0, 0, 0)
        self.workspace_splitter = WorkspaceSplitter(monitor.ROOT)
        middle.addWidget(self.workspace_splitter)
        self.task_panel = FrostedPanel(root)
        self.task_panel.setMinimumWidth(180)
        self.task_panel.setMaximumWidth(640)
        side = QVBoxLayout(self.task_panel)
        side.setContentsMargins(13, 12, 13, 10)
        side.setSpacing(8)
        task_header_area = QVBoxLayout()
        task_header_area.setSpacing(0)
        side.addLayout(task_header_area)
        task_header = QHBoxLayout()
        pane_title_font = ui_font(13, True)
        task_heading = label('案头诸事')
        task_heading.setFont(pane_title_font)
        task_heading.setObjectName('taskPanelTitle')
        task_heading.setWordWrap(False)
        task_header.addWidget(task_heading, 1)
        create = ActionButton('＋')
        create.setToolTip('创建测试任务')
        create.setAccessibleName('创建测试任务')
        create.clicked.connect(lambda: self.operate('create_test'))
        task_header.addWidget(create, 0, Qt.AlignmentFlag.AlignVCenter)
        self.task_search = QLineEdit()
        self.task_search.setAccessibleName('搜索任务')
        self.task_search.setPlaceholderText('寻一件案头事')
        self.task_search.textChanged.connect(self.filter_tasks)
        side.addWidget(self.task_search)
        self.task_choice = ProjectTree()
        self.task_choice.setObjectName('taskList')
        self.task_choice.setAccessibleName('项目与对话')
        self.task_choice.setHeaderHidden(True)
        self.task_choice.setIndentation(14)
        self.task_choice.setWordWrap(True)
        self.task_choice.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.task_choice.currentItemChanged.connect(self.select_task)
        self.task_choice.itemClicked.connect(self.clicked_task)
        self.task_choice.itemClicked.connect(self.focus_task)
        self.task_choice.itemActivated.connect(self.focus_task)
        self.task_choice.moved.connect(self.move_tree_item)
        self.task_choice.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_choice.customContextMenuRequested.connect(self.show_task_menu)
        self.task_choice.itemExpanded.connect(self.expand_project)
        self.task_choice.itemCollapsed.connect(self.collapse_project)
        self.task_choice.viewport().setAutoFillBackground(False)
        side.addWidget(self.task_choice, 1)
        self.task_empty = label('', 9, color=MUTED)
        self.task_empty.hide()
        side.addWidget(self.task_empty)
        self.live = label('○ 正在连接', 9, color=MUTED)
        self.live.setObjectName('status')
        side.addWidget(self.live)
        self.workspace_splitter.addWidget(self.task_panel)

        self.conversation_card = FrostedPanel(root)
        conversation_layout = QVBoxLayout(self.conversation_card)
        conversation_layout.setContentsMargins(15, 12, 15, 12)
        conversation_layout.setSpacing(8)
        self.conversation_header = ActionHeader()
        conversation_header = QHBoxLayout(self.conversation_header)
        conversation_header.setContentsMargins(0, 0, 0, 0)
        conversation_title = QVBoxLayout()
        conversation_title.setSpacing(3)
        conversation_caption = label('灯下对谈', 9, color=MUTED)
        conversation_title.addWidget(conversation_caption)
        self.title_stack = QStackedWidget()
        self.title_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.conversation_heading = TaskTitle('未尽的话，可以接着说。')
        self.conversation_heading.setFont(pane_title_font)
        self.conversation_heading.setTextFormat(Qt.TextFormat.PlainText)
        self.conversation_heading.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.conversation_heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.conversation_heading.setWordWrap(False)
        self.conversation_heading.edit_requested.connect(self.begin_rename)
        QShortcut(QKeySequence('F2'), self.conversation_heading, self.begin_rename,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self.title_editor = QLineEdit()
        self.title_editor.setMaxLength(80)
        self.title_editor.setAccessibleName('任务名称')
        self.title_editor.returnPressed.connect(self.save_rename)
        QShortcut(QKeySequence('Escape'), self.title_editor, self.cancel_rename,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self.title_stack.addWidget(self.conversation_heading)
        self.title_stack.addWidget(self.title_editor)
        conversation_title.addWidget(self.title_stack)
        conversation_header.addLayout(conversation_title, 1)
        self.build_task_actions()
        self.title_sync_button = QPushButton('待同步')
        self.title_sync_button.clicked.connect(self.resolve_title_conflict)
        self.title_sync_button.hide()
        conversation_header.addWidget(self.title_sync_button)
        conversation_header.addWidget(self.conversation_header.actions)
        conversation_layout.addWidget(self.conversation_header)
        divider = QFrame()
        divider.setObjectName('divider')
        divider.setFixedHeight(1)
        conversation_layout.addWidget(divider)

        self.conversation = ConversationBrowser()
        self.conversation.setObjectName('conversation')
        configure_text(self.conversation, 10, 3)
        self.conversation.setMinimumHeight(130)
        self.conversation.setOpenExternalLinks(False)
        self.conversation.anchorClicked.connect(self.restore_local_message)
        self.conversation.viewport().setAutoFillBackground(False)
        conversation_layout.addWidget(self.conversation, 1)

        self.review_button = ActionButton('查看待审批请求')
        self.review_button.clicked.connect(lambda: self.offer_approval(force=True))
        self.review_button.hide()
        conversation_layout.addWidget(self.review_button, 0, Qt.AlignmentFlag.AlignLeft)
        composer_box = QFrame()
        composer_box.setObjectName('composerBox')
        composer_layout = QVBoxLayout(composer_box)
        composer_layout.setContentsMargins(10, 6, 10, 7)
        composer_layout.setSpacing(3)
        self.message_edit = Composer()
        self.message_edit.setObjectName('composer')
        configure_text(self.message_edit, 10, 3)
        self.message_edit.viewport().setAutoFillBackground(False)
        self.message_edit.setFixedHeight(57)
        self.message_edit.setPlaceholderText('把下一步，写在这里…')
        self.message_edit.setAccessibleName('任务指令')
        self.message_edit.setAcceptRichText(False)
        self.message_edit.send_requested.connect(self.submit_composer)
        self.message_edit.slash_requested.connect(self.open_codex_options)
        composer_layout.addWidget(self.message_edit)
        send_row = QHBoxLayout()
        self.message_hint = label('', 8.5, color=MUTED)
        send_row.addWidget(self.message_hint, 1)
        self.send_button = ActionButton('↑')
        self.send_button.setObjectName('send')
        self.send_button.setFixedSize(34, 34)
        self.send_button.setToolTip('Enter 发送 · Shift+Enter 换行')
        self.send_button.setAccessibleName('发送指令')
        self.send_button.clicked.connect(self.submit_composer)
        send_row.addWidget(self.send_button)
        composer_layout.addLayout(send_row)
        conversation_layout.addWidget(composer_box)
        self.control_message = label('', 8.5, color=MUTED)
        conversation_layout.addWidget(self.control_message)
        self.task_detail_pages = QStackedWidget()
        self.task_detail_pages.addWidget(self.conversation_card)
        self.project_intro_card = FrostedPanel(root)
        intro_layout = QVBoxLayout(self.project_intro_card)
        intro_layout.setContentsMargins(0, 0, 0, 0)
        self.project_intro = ProjectIntroPage(ProjectIntroStore(monitor.ROOT))
        intro_layout.addWidget(self.project_intro)
        self.task_detail_pages.addWidget(self.project_intro_card)
        self.workspace_splitter.addWidget(self.task_detail_pages)
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.restore_width(self.width() - page.contentsMargins().left() - page.contentsMargins().right())
        task_header_area.addSpacing(conversation_caption.sizeHint().height() + conversation_title.spacing())
        task_heading.setFixedHeight(self.title_stack.sizeHint().height())
        task_header_area.addLayout(task_header)

        self.details_dialog = QDialog(self)
        self.details_dialog.setObjectName('lanxiDialog')
        self.details_dialog.setWindowTitle('运行与额度')
        self.details_dialog.resize(520, 380)
        details = QVBoxLayout(self.details_dialog)
        for title, attr in (('连接与自动推进', 'guard_text'), ('账户额度', 'quota_text')):
            details.addWidget(label(title, 11, True))
            content = QTextBrowser()
            configure_text(content, 10, 4)
            setattr(self, attr, content)
            details.addWidget(content, 1)
        self.edge_menu = EdgeMenu(root, {
            WINDOW_TITLE: [],
            '任务': [('执舟', self.action_buttons['attach'].click), ('暂泊', self.action_buttons['pause'].click),
                   ('续航', self.action_buttons['resume'].click), ('待你定夺', lambda: self.offer_approval(force=True)),
                   ('运行与额度', self.show_details)],
            '显示': [('Codex', lambda: self.select_workspace('codex')), ('Claude Code', lambda: self.select_workspace('claude')),
                   ('吉光片羽', self.open_notebook)],
            '设置': [('大模型 API…', self.open_model_api), ('壁纸管理…', self.open_wallpapers)],
            '窗口': [('最大化／还原', self.toggle_maximized), ('收起到托盘', self.close)],
        }, actions=[
            (CONNECT_CLOUD, lambda: self.open_device_pairing(self), '连接云服务器或查看连接状态'),
            ('测试', self.open_diagnostics, '运行本机自检、服务器测试和项目会话诊断'),
            ('合流', self.open_manual_sync, '手动同步代码、记忆和文件清单'),
        ])
        self.populate_app_menu(self.edge_menu.menus[WINDOW_TITLE], include_about=True)
        task_menu = self.edge_menu.menus['任务']
        task_menu.addAction('会话摘要与删除进度…', self.show_retired_sessions)
        task_menu.addAction('项目分组与同步…', self.open_project_settings)
        task_menu.aboutToShow.connect(self.refresh_task_menu)

    def refresh_task_menu(self):
        attach, pause, resume, review, _details, retirements, _groups = self.edge_menu.menus['任务'].actions()
        for action, key in zip((attach, pause, resume), ('attach', 'pause', 'resume')):
            action.setEnabled(self.workspace_pages.currentWidget() is self.task_workspace
                              and self.task_detail_pages.currentWidget() is self.conversation_card
                              and self.action_buttons[key].isEnabled())
        review.setEnabled(bool(self._pending_approvals or self._approval_feedback or self._approval_sending))
        retirements.setEnabled(monitor.personal_sync is not None)

    def about_lanxi(self):
        self._popup('关于澜汐', f'澜汐 R{VERSION} · 开发工作台\n一处开始，处处继续。')

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def open_model_api(self):
        dialog = ModelApiDialog(self, self.organizer)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            if self.notebook is not None:
                self.notebook.load_list()

    def open_wallpapers(self):
        backdrop = self.centralWidget()
        backdrop.reload_wallpapers()
        dialog = WallpaperDialog(self, backdrop)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def refresh_grouping(self, scope):
        if self.stop.is_set():
            return
        if scope == 'task:' + self.client:
            self.refresh()
        elif self.notebook is not None and scope == self.notebook.kind:
            self.notebook.load_list()

    def build_task_actions(self):
        self.action_buttons = {}
        actions = QHBoxLayout(self.conversation_header.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(5)
        for action, text, meaning in (
            ('attach', '执舟', '接入任务'), ('pause', '暂泊', '暂停任务'),
            ('resume', '续航', '继续任务'), ('manage', '扬帆', '开启自动推进'),
            ('unmanage', '收帆', '关闭自动推进'), ('detach', '移离案头', '移出任务'),
            ('handoff', '交还任务', '同步当前会话并将操作权交还给下一台电脑'),
            ('open_project', '启卷', '在 VS Code 打开任务项目'),
        ):
            button = QPushButton(text)
            button.setObjectName('hoverAction')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setFont(ui_font(9))
            button.setToolTip(meaning)
            button.setAccessibleName(meaning)
            if action == 'open_project':
                button.clicked.connect(self.open_project)
            elif action == 'attach':
                button.clicked.connect(lambda: self.focus_task(self.task_items.get(self.selected_thread)))
            else:
                button.clicked.connect(lambda _=False, action=action: self.operate(action))
            self.action_buttons[action] = button
            actions.addWidget(button)

    def build_tray(self):
        self.updater = DesktopUpdates(self)
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.windowIcon() if not self.windowIcon().isNull() else self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.tray.setToolTip(WINDOW_TITLE)
        menu = QMenu(self)
        self.populate_app_menu(menu)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason in (
            QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()

    def populate_app_menu(self, menu, *, include_about=False):
        menu.addAction('打开澜汐', self.show_window)
        menu.addAction(self._tray_release_action)
        menu.aboutToShow.connect(self.refresh_tray_release)
        menu.addAction('吉光片羽', self.open_notebook)
        menu.addAction('运行与额度', self.show_details)
        menu.addAction('测试…', self.open_diagnostics)
        sync_menu = menu.addMenu('跨机接续')
        sync_menu.aboutToShow.connect(lambda: self.fill_sync_menu(sync_menu))
        menu.addAction('查看待审批请求', lambda: self.offer_approval(force=True))
        menu.addAction('VS Code 审批提醒…', self.configure_external_approval_alerts)
        remote_completion = menu.addAction('接收其他电脑的完成提醒')
        remote_completion.setCheckable(True)
        menu.aboutToShow.connect(lambda: remote_completion.setChecked(monitor.remote_completion_since() is not None))
        remote_completion.triggered.connect(self.set_remote_completion_notifications)
        menu.addAction('打开用户数据目录', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(monitor.ROOT))))
        menu.addAction('设置项目母目录…', lambda: self.choose_project_root())
        if getattr(sys, 'frozen', False):
            menu.addAction('打开程序所在目录', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(sys.executable).parent))))
            menu.addAction('检查更新…', lambda: self.updater.check(manual=True))
        menu.addSeparator()
        if include_about:
            menu.addAction('关于澜汐', self.about_lanxi)
        menu.addAction('退出程序', self.quit_app)

    def choose_project_root(self, first=False):
        selected = choose_project_folder(self)
        if not selected and not first:
            return
        try:
            set_project_root(monitor.ROOT, selected)
            if selected:
                self.control_message.setText('已保存项目母目录，正在扫描项目')
        except OSError as error:
            self._popup('无法保存项目母目录', str(error))

    def fill_sync_menu(self, menu):
        menu.clear()
        flow = monitor.personal_sync
        coordination = monitor.config().get('coordination') or {}
        if coordination.get('url', '').startswith('https://'):
            menu.addAction('邀请另一台电脑…', lambda: PairingDialog(self, monitor.ROOT, coordination).exec())
        menu.addAction('使用邀请码连接…', self.open_cloud_pairing)
        if flow:
            view = flow.snapshot()
            if flow.coordination and not flow.settings.get('codeOnly') and not flow.settings.get('exchange'):
                menu.addAction('下载服务器代码备份…', self.choose_code_backup)
            if view.get('connection'):
                menu.addAction(view['connection']).setEnabled(False)
            for project in sorted(view['projects']):
                label = project + ' · ' + view['status'].get(project, '准备中')
                backup = view.get('codeBackups', {}).get(project)
                if backup:
                    label += ' · 服务器' + backup
                count = len(view.get('largeInventory', {}).get(project, {}))
                if count:
                    label += f' · {count} 个大文件需核对'
                menu.addAction(label, lambda checked=False, p=project: self.request_project(p))
            if view['projects']:
                menu.addAction('查看大文件清单…', self.show_large_inventory)
                menu.addAction('登记大文件…', self.register_large_files)
                if flow.file_exchange:
                    menu.addAction('借用另一台电脑的大文件…', self.borrow_large_file)
                    menu.addAction('查看大文件借用与回传…', self.large_file_transfers)
            for key in (('记忆', '吉光片羽', '大文件') if view.get('connection') else ('配对', '记忆', '吉光片羽', '大文件')):
                if key in view['status']:
                    menu.addAction(key+' · '+view['status'][key]).setEnabled(False)
            if not view['projects']:
                menu.addAction('等待项目登记').setEnabled(False)
        if (not flow or flow.settings.get('codeOnly') or
                not flow.settings.get('exchange') and not flow.coordination):
            if coordination:
                menu.addAction('通过已配置的澜汐协调服务配对', lambda: self.enable_flow({'codeOnly':False}))
        if not flow:
            menu.addAction('仅启用已登记项目的 GitHub 同步', lambda: self.enable_flow({'codeOnly':True}))

    def choose_code_backup(self):
        flow = monitor.personal_sync
        candidates = sorted(flow.snapshot()['projects'])
        project, accepted = QInputDialog.getItem(self, '服务器代码备份', 'GitHub 仓库（所有者/项目）', candidates, 0, True)
        if accepted and project.strip():
            project = project.strip().lower()
            self.run_code_backup(project, lambda client: client.list(project))

    def run_code_backup(self, project, operation):
        flow = monitor.personal_sync

        def work():
            try:
                client = flow.code_backups() if flow else None
                if client is None:
                    raise ValueError('请先连接云服务器')
                result = operation(client)
                self.code_backup_result.emit(project, result, None)
            except (ValueError, OSError, KeyError) as error:
                message = str(error) if isinstance(error, ValueError) else '无法读取服务器备份或保存目标文件'
                self.code_backup_result.emit(project, None, message)

        threading.Thread(target=work, daemon=True).start()

    def show_code_backup_result(self, project, result, error):
        if error:
            self._popup('服务器代码备份', error)
        elif isinstance(result, Path):
            self._popup('服务器代码备份', f'已保存到 {result}')
        elif not result:
            self._popup('服务器代码备份', '服务器没有这个项目的代码副本。')
        else:
            labels = {f"{row['device']} · {time.strftime('%Y-%m-%d %H:%M', time.localtime(row['updated']))}": row['device']
                      for row in result}
            selected, accepted = QInputDialog.getItem(self, '服务器代码备份', '选择来源设备和备份时间', list(labels), 0, False)
            if not accepted:
                return
            filename, _ = QFileDialog.getSaveFileName(self, '保存代码备份', project.split('/')[-1] + '.zip', 'ZIP (*.zip)')
            if filename:
                destination = Path(filename)

                def download(client):
                    client.download(project, labels[selected], destination)
                    return destination

                self.run_code_backup(project, download)

    def show_large_inventory(self):
        state = monitor.personal_sync.snapshot()
        rows = state.get('largeInventory', {})
        text = []
        for project, entries in sorted(rows.items()):
            text.append(project)
            for name, entry in sorted(entries.items()):
                size = entry['localSize']
                local = '本机缺少' if size is None else f'本机 {size / 1024**2:.2f} MiB'
                text.append(f'  {name}  ·  {local}')
                for expected, computers in sorted(entry['copies'].items()):
                    text.append(f"    {expected / 1024**2:.2f} MiB  ·  上次清点见于 {', '.join(computers)}")
            text.append('')
        if not text:
            text.append('上次检查没有发现需核对的大文件。' if state.get('connection')
                        else '尚未取得其他设备的大文件清单。')
        if '大文件' in state['status']:
            text.append(state['status']['大文件'])
        dialog = QDialog(self)
        dialog.setWindowTitle('大文件清单')
        dialog.resize(680, 370)
        body = QTextBrowser(dialog)
        body.setPlainText('\n'.join(text))
        QVBoxLayout(dialog).addWidget(body)
        dialog.exec()

    def register_large_files(self):
        from large_inventory import register

        flow = monitor.personal_sync
        projects = flow.snapshot()['projects']
        project, accepted = QInputDialog.getItem(self, '登记大文件', '项目', sorted(projects), editable=False)
        if not accepted:
            return
        directory = Path(projects[project])
        paths, _ = QFileDialog.getOpenFileNames(self, '选择需要在其他设备提示的大文件', str(directory))
        if not paths:
            return
        try:
            register(monitor.ROOT, project, directory, paths)
            flow.request(project)
            self.control_message.setText('已登记大文件清单，其他设备将看到文件路径和大小。')
        except (OSError, ValueError) as error:
            self._popup('无法登记大文件', str(error))

    def borrow_large_file(self):
        flow = monitor.personal_sync
        choices = {}
        for project, rows in sorted(flow.snapshot().get('largeAvailable', {}).items()):
            for name, entry in sorted(rows.items()):
                for device, hostname in sorted(entry['devices'].items()):
                    choices[f'{project} · {name} · {hostname} ({device[-6:]})'] = (project, device, name)
        if not choices:
            self._popup('借用大文件', '尚无其他设备登记的大文件。')
            return
        name, accepted = QInputDialog.getItem(self, '借用大文件', '选择文件及来源电脑', list(choices), editable=False)
        if accepted:
            flow.file_exchange.request(*choices[name])
            self.control_message.setText('借用申请已提交，等来源电脑在线上传。')

    def large_file_transfers(self):
        flow = monitor.personal_sync
        exchange = flow.file_exchange
        states = {
            'requested': '等待来源电脑响应', 'uploading': '来源电脑正在上传',
            'ready': '等待借用电脑下载', 'editing': '借用电脑可编辑',
            'returning': '正在回传', 'returned': '等待来源电脑应用',
            'completed': '已完成，服务器已清理临时文件', 'conflict': '来源文件已变化，两份本机版本均保留',
        }
        rows = list(exchange.rows)
        note = '\n'.join(f"{item['project']} · {item['name']} · {states[item['state']]}"
                         + (f" · {exchange.errors[item['key']]}" if item['key'] in exchange.errors else '')
                         for item in rows)
        current = (flow.coordination or {}).get('device')
        choices = {f"{item['project']} · {item['name']}": item['key'] for item in rows
                   if item['receiver'] == current and item['state'] == 'editing'}
        if not choices:
            self._popup('大文件借用记录', note or exchange.errors.get('连接') or '暂无借用记录。')
            return
        label = note + '\n\n保存好本机文件后，选择要回传的文件。'
        selection, accepted = QInputDialog.getItem(self, '大文件借用记录', label, list(choices), editable=False)
        if accepted:
            try:
                exchange.submit(choices[selection])
                self.control_message.setText('已排队回传保存后的文件。')
            except OSError as error:
                self._popup('无法回传大文件', str(error))

    def open_device_pairing(self, parent):
        if monitor.config().get('coordination'):
            status = monitor.personal_sync.snapshot().get('connection') if monitor.personal_sync else None
            self._popup(CONNECT_CLOUD, status or '正在检查云服务器连接。')
            return
        enrolled = bool(monitor.saved_config().get('coordination'))
        if not (self.workspace_grant or enrolled):
            self.open_cloud_pairing(parent=parent)
            return
        dialog = WorkspaceConnectDialog(parent, monitor.ROOT, self.workspace_grant, enrolled=enrolled)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.activate_cloud_connection()

    def open_cloud_pairing(self, first=False, *, parent=None):
        dialog = PairingDialog(parent or self, monitor.ROOT, first=first)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.paired:
            self.control_message.setText('已保存项目母目录，正在扫描项目')
            return
        self.activate_cloud_connection()

    def activate_cloud_connection(self):
        monitor.set_workspace_online(True)
        try:
            monitor.activate_cloud_pairing(self.stop, self.worker)
            self.control_message.setText('本机已连接云服务器，正在同步项目')
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._popup(CONNECT_CLOUD, str(error))

    def enable_flow(self, settings):
        from handoff_sync import atomic_write
        try:
            if monitor.personal_sync:
                settings = {**monitor.personal_sync.settings, **settings}
            if settings.get('exchange'):
                share = Path(settings['exchange']).resolve()
                if any((p/'.git').exists() for p in (share,*share.parents)):
                    raise ValueError('请选择 Git 工作区以外的私人共享目录')
            atomic_write(monitor.ROOT/'personal-sync.json',json.dumps(settings,ensure_ascii=False).encode())
            if monitor.personal_sync:
                monitor.personal_sync.settings = settings
                monitor.personal_sync.wake.set()
            else:
                monitor.enable_personal_sync(settings,self.stop,self.worker)
            self.control_message.setText('跨机接续已启动')
        except (OSError,ValueError) as error:
            self._popup('跨机接续', str(error))

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        if self._tray_hidden and not self._quitting:
            self._tray_hidden = False
            monitor.set_tray_mode(False)
            self.refresh_tray_release()

    def refresh_tray_release(self):
        status = monitor.tray_release_status()
        state = status['state']
        text = {
            'releasing': '正在交还本机控制权',
            'waiting': '等待交还本机控制权',
            'released': '本机控制权已交还',
        }.get(state, '')
        if text and status['pending']:
            text += f" · 待处理 {status['pending']} 个任务"
        if state in ('releasing', 'waiting') and status['detail']:
            text += ' · ' + display_text(' '.join(status['detail'].split()))
        self._tray_release_action.setText(text)
        self._tray_release_action.setVisible(bool(text))
        tooltip = f'{WINDOW_TITLE}\n{text}' if text else WINDOW_TITLE
        if self.tray.toolTip() != tooltip:
            self.tray.setToolTip(tooltip)

    def tray_control_guard(self):
        if self._tray_hidden:
            return '请打开澜汐并重新执舟。'
        if monitor.tray_release_status()['state'] in ('releasing', 'waiting'):
            return '正在交还本机控制权，完成后可重新执舟。'
        return ''

    def quit_app(self):
        if not self.project_intro.confirm_exit():
            return
        self._quitting = True
        self.close()
        # Closing an already-hidden window does not emit lastWindowClosed.
        # Explicitly end the application loop after cleanup in both tray cases.
        QApplication.instance().quit()

    def shutdown(self):
        """Idempotent cleanup, including quit paths that bypass closeEvent."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self._quitting = True
        if self.notebook is not None:
            self.notebook.flush()
        self.workspace_splitter.flush()
        self.stop.set()
        self.organizer.close()
        self.timer.stop()
        self.approval_timer.stop()
        self.activity_timer.stop()
        self._approval_pool.shutdown(wait=False, cancel_futures=True)
        self._submission_pool.shutdown(wait=True)
        self._history_pool.shutdown(wait=True, cancel_futures=True)
        if self.worker:
            self.worker.join(timeout=8)
        self.tray.hide()

    def open_diagnostics(self):
        if self.diagnostics is None:
            self.diagnostics = DiagnosticsDialog(
                self, lambda: bool(monitor.config().get('coordination')), self.open_device_pairing,
            )
        self.diagnostics.show()
        self.diagnostics.raise_()
        self.diagnostics.activateWindow()

    def open_manual_sync(self, *, fresh=False):
        flow = monitor.personal_sync
        if flow is None:
            self._popup('合流 · 手动同步', '跨机同步尚未启用。请先连接云服务器，或从「跨机接续」启用 GitHub 同步。'
                       '\n本机 Codex 任务可在「案头诸事」继续查看。')
            return
        if self.manual_sync_dialog is None:
            self.manual_sync_dialog = ManualSyncDialog(self)
        self.manual_sync_dialog.observe(flow.request_manual_sync(fresh=fresh), flow.sync_progress)

    def show_details(self):
        self.refresh()
        self.details_dialog.show()
        self.details_dialog.raise_()

    def open_notebook(self):
        self.show_window()
        self.select_workspace('notebook')

    def select_workspace(self, section):
        if section == 'notebook':
            if self.notebook is None:
                try:
                    store = NotebookStore(monitor.ROOT / 'lanxi-notebook.sqlite3')
                    self.notebook = NotebookWorkspace(store, self.centralWidget(), self.organizer)
                except sqlite3.Error:
                    self.client_switch.select(self.client)
                    self._popup('吉光片羽暂未打开', '暂时无法读写本机笔记库。')
                    return
                self.workspace_pages.addWidget(self.notebook)
            width = self.workspace_splitter.sizes()[0]
            if width:
                self.notebook.list_panel.setFixedWidth(width)
            self.notebook.refresh_day()
            self.workspace_pages.setCurrentWidget(self.notebook)
            self.client_switch.select(section)
            return
        if self.workspace_pages.currentWidget() is self.notebook and not self.notebook.flush():
            self.client_switch.select('notebook')
            return
        self.workspace_pages.setCurrentWidget(self.task_workspace)
        self.switch_client(section)

    def select_task(self, current, _previous):
        tid = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        if tid:
            self.show_conversation_page()
            self.change_task(self.client, tid)
            self.render_selection(self._last_snapshot)
            self.schedule_local_project()
        elif current is not None and current.data(0, PROJECT_WORKSPACE_ROLE):
            self.show_project_intro(current, schedule=True)

    def show_project_intro(self, item, *, schedule=False):
        workspace = item.data(0, PROJECT_WORKSPACE_ROLE)
        path = Path(workspace)
        boundary = configured_root(monitor.ROOT)
        relative = path.relative_to(boundary) if boundary and path.is_relative_to(boundary) else path
        self._project_intro_group = item.data(0, TASK_DIRECTORY_ROLE)
        self.cancel_rename()
        self.show_intro_data(item, folder_id(relative.as_posix()), workspace)
        self.task_detail_pages.setCurrentWidget(self.project_intro_card)
        if schedule and monitor.personal_sync and path.is_dir():
            project = self.project_sync_id(workspace, boundary)
            if project and not (project.startswith('.folder/') and receiver_placeholder(path, project)):
                monitor.personal_sync.request_after_open(project)

    def show_conversation_page(self):
        if self._project_intro_group is not None:
            self._project_intro_group = None
            self.task_detail_pages.setCurrentWidget(self.conversation_card)

    def show_intro_data(self, item, key, workspace):
        entry = next((row for row in self._last_snapshot.get('indexedProjects', ())
                      if row.get('cwd') and Path(row['cwd']) == Path(workspace)), {})
        repository = entry.get('githubProject') or next((row['githubProject'] for row in self._last_snapshot.get('threads', ())
                           if row.get('githubProject') and row.get('cwd') and Path(row['cwd']) == Path(workspace)), '')
        if not repository:
            repository = next((project for project, directory in self._project_sync_view.get('projects', {}).items()
                               if Path(directory) == Path(workspace)), '')
        self.project_intro.show_project(key, item.text(0), workspace, repository=repository, branch=entry.get('branch', ''))

    def clicked_task(self, item, _column):
        if item.data(0, Qt.ItemDataRole.UserRole) == self.selected_thread:
            self.schedule_local_project()

    def change_task(self, client, tid):
        previous = (self.client, self.selected_thread)
        selected = (client, tid)
        if selected != previous:
            self._codex_request = None
            if self._codex_menu:
                self._codex_menu.close()
            self.message_edit.codex_slash = client == 'codex' and not tid.startswith(ENTRY_PREFIX)
            self.task_drafts[previous] = self.message_edit.toPlainText()
            self.client, self.selected_thread = selected
            self.message_edit.setPlainText(self.task_drafts.get(selected, ''))

    def focus_task(self, item, _column=0):
        if item is None:
            return
        tid = item.data(0, Qt.ItemDataRole.UserRole)
        if not tid or tid.startswith(ENTRY_PREFIX):
            return
        last = next((operation for (client, _), operation in reversed(self._pending_operations.items())
                     if client == self.client and operation['action'] == 'focus'), None)
        if not last or last['threadId'] != tid:
            self.operate('focus', tid=tid)

    def task_title(self):
        item = self.task_items.get(self.selected_thread)
        return item.data(0, TASK_TITLE_ROLE) if item is not None else ''

    def expand_project(self, item):
        if not self.task_search.text().strip():
            self.collapsed_projects[self.client].discard(item.data(0, PROJECT_CATEGORY_ROLE) or item.data(0, TASK_DIRECTORY_ROLE))

    def collapse_project(self, item):
        if not self.task_search.text().strip():
            self.collapsed_projects[self.client].add(item.data(0, PROJECT_CATEGORY_ROLE) or item.data(0, TASK_DIRECTORY_ROLE))

    def filter_tasks(self):
        query = self.task_search.text().strip().casefold()
        matches = 0
        with QSignalBlocker(self.task_choice):
            for key, group in self.project_items.items():
                group_matches = 0
                category = group.parent().text(0).casefold() if group.parent() is not None else ''
                empty_project = not group.childCount() and (query in group.text(0).casefold() or query in category)
                for row in range(group.childCount()):
                    item = group.child(row)
                    visible = (query in item.data(0, TASK_TITLE_ROLE).casefold()
                               or query in (item.data(0, TASK_DIRECTORY_ROLE) or '').casefold()
                               or query in group.text(0).casefold() or query in category)
                    item.setHidden(not visible)
                    group_matches += int(visible)
                group.setHidden(group_matches == 0 and not empty_project)
                group.setExpanded(bool(query) or key not in self.collapsed_projects[self.client])
                matches += group_matches + int(empty_project)
            for key, category in self.category_items.items():
                category.setHidden(all(category.child(i).isHidden() for i in range(category.childCount())))
                category.setExpanded(bool(query) or key not in self.collapsed_projects[self.client])
        self.task_empty.setText('案头未见这一件，换个词再寻。' if query else '案头暂空，待写新篇。')
        self.task_empty.setVisible(matches == 0)

    def show_task_menu(self, position):
        item = self.task_choice.itemAt(position)
        thread = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not thread:
            return
        menu = QMenu(self.task_choice)
        if thread.startswith(ENTRY_PREFIX):
            entry = next((row for row in self._last_snapshot.get('indexedProjects', []) if row['id'] == thread), None)
            if not entry:
                return
            menu.addAction('启卷', lambda: self.launch_project(Path(entry['cwd'])))
            if entry.get('memory'):
                menu.addAction('打开项目记忆目录', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(entry['memory'])))
        else:
            if self.client == 'codex':
                menu.addAction('查看会话 ID', lambda: self.show_session_id(thread))
            menu.addAction('移动…', lambda: self.choose_session_project(thread))
            menu.addAction('重命名', lambda: self.rename_session(thread))
            if self.client == 'codex':
                title = item.data(0, TASK_TITLE_ROLE)
                delete = menu.addAction('删除…', lambda: self.open_retirement(thread, title))
                delete.setEnabled(monitor.personal_sync is not None)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.task_choice.viewport().mapToGlobal(position))

    def rename_session(self, thread):
        item = self.task_items.get(thread)
        if item is not None:
            self.task_choice.setCurrentItem(item)
            self.begin_rename()

    def choose_session_project(self, thread):
        choices = {f'{item.text(0)} · {item.data(0, PROJECT_WORKSPACE_ROLE)}': key
                   for key, item in self.project_items.items() if item.data(0, PROJECT_WORKSPACE_ROLE)}
        choices = {'按原项目显示': None, **choices}
        picked, ok = QInputDialog.getItem(self, '移动会话', '选择显示位置', list(choices), editable=False)
        if ok:
            try:
                self.tree_preferences.move(self.client, thread, choices[picked])
            except (OSError, ValueError) as error:
                self._popup('移动未完成', str(error))
            self.refresh()

    @staticmethod
    def child_items(tree_or_item):
        if isinstance(tree_or_item, QTreeWidgetItem):
            return [tree_or_item.child(row) for row in range(tree_or_item.childCount())]
        return [tree_or_item.topLevelItem(row) for row in range(tree_or_item.topLevelItemCount())]

    def move_tree_item(self, source, target, zone):
        role = Qt.ItemDataRole.UserRole
        thread, other = source.data(0, role), target.data(0, role)
        if thread and other and zone == 'on' and not other.startswith(ENTRY_PREFIX):
            self.queue_session_transfer(source, target)
            return
        try:
            if thread:
                project = target.parent() if other else target
                key = project.data(0, TASK_DIRECTORY_ROLE)
                ids = [node.data(0, role) for node in self.child_items(project) if node.data(0, role) != thread]
                index = ids.index(other) + int(zone == 'after') if other and other in ids else len(ids)
                ids.insert(index, thread)
                if source.parent() is project:
                    self.tree_preferences.order(self.client, 'sessions:' + key, ids)
                else:
                    self.tree_preferences.reposition(self.client, thread, key, ids)
            else:
                parent = target.parent()
                key = source.data(0, TASK_DIRECTORY_ROLE)
                neighbor = target.data(0, TASK_DIRECTORY_ROLE)
                category = parent.data(0, PROJECT_CATEGORY_ROLE) if parent is not None else ''
                ids = [node.data(0, TASK_DIRECTORY_ROLE) for node in self.child_items(parent if parent is not None else self.task_choice)
                       if node.data(0, TASK_DIRECTORY_ROLE) is not None and node.data(0, TASK_DIRECTORY_ROLE) != key]
                ids.insert(ids.index(neighbor) + int(zone == 'after'), key)
                self.tree_preferences.order(self.client, 'projects:' + category, ids)
        except (OSError, ValueError) as error:
            self._popup('移动未完成', str(error))
        QTimer.singleShot(0, self.refresh)

    def queue_session_transfer(self, source, target):
        if self.client != 'codex' or monitor.personal_sync is None:
            self._popup('无法转入会话', '会话转入需要已配对的 Codex。')
            return
        role = Qt.ItemDataRole.UserRole
        thread, other = source.data(0, role), target.data(0, role)
        title, target_title = source.data(0, TASK_TITLE_ROLE), target.data(0, TASK_TITLE_ROLE)
        QTimer.singleShot(0, lambda: self.open_retirement(thread, title, target=other, target_title=target_title))

    def open_retirement(self, thread, title, *, target=None, target_title=None):
        flow = monitor.personal_sync
        if flow is None:
            return
        dialog = RetirementDialog(self, flow, thread, title, target=target, target_title=target_title,
                                  controller=monitor.get_controller('codex'))
        try:
            dialog.exec()
            if dialog.transfer_completed and (item := self.task_items.get(target)) is not None:
                self.task_choice.setCurrentItem(item)
        finally:
            dialog.deleteLater()

    def show_retired_sessions(self):
        flow = monitor.personal_sync
        if flow is None:
            return
        rows = flow.snapshot().get('retirements', {})
        text = []
        for thread, row in rows.items():
            devices = '、'.join(row['acknowledged']) or '等待设备回执'
            text.append(f'{row.get("document") or thread}\n{row["status"]}\n设备回执 · {devices}')
        self._popup('会话摘要与删除进度', '\n\n'.join(text) or '暂无待处理或已删除的会话。')

    def show_session_id(self, thread):
        dialog = QMessageBox(self)
        dialog.setObjectName('lanxiDialog')
        dialog.setWindowTitle('Codex 会话 ID')
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(thread)
        dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse |
                                       Qt.TextInteractionFlag.TextSelectableByKeyboard)
        copy_button = dialog.addButton('复制 ID', QMessageBox.ButtonRole.ActionRole)
        dialog.addButton('关闭', QMessageBox.ButtonRole.RejectRole)
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(thread))
        dialog.exec()
        dialog.deleteLater()

    def render_tasks(self, projects, threads, controls):
        if self.task_choice.drag_item is not None:
            return
        thread_status = {task.get('id'): (task.get('status') or {}).get('type') for task in threads}
        active_controls = {task.get('threadId'): task for task in controls.get('tasks') or []}
        attaching = {op.get('threadId') for op in controls.get('operations') or []
                     if op.get('action') in ('attach', 'focus') and op.get('status') in ('queued', 'running')}
        roots = frozenset(Path(row['cwd']) for row in self._last_snapshot.get('indexedProjects', [])
                          if row['id'].startswith(ENTRY_PREFIX))
        boundary = configured_root(monitor.ROOT)
        groups = {directory: project_group(directory, roots, boundary) for _, directory in projects.values()}
        configured = {project: (row['id'], row['name']) for row in self._project_sync_view.get('projectGroups', [])
                      for project in row['projects']}
        priorities = self._project_sync_view.get('projectPolicies', {})
        group_settings = {group[0]: (configured.get(project), priorities.get(project)) for group in groups.values()
                          if (project := self.project_sync_id(group[2], boundary)) in configured or project in priorities}
        projects_by_name = {}
        for group in groups.values():
            projects_by_name.setdefault(group[1].casefold(), set()).add(group)
        rows = []
        for tid, (title, directory) in projects.items():
            control = active_controls.get(tid) or {}
            status = (control.get('threadStatus') or {}).get('type') or thread_status.get(tid)
            if tid.startswith(ENTRY_PREFIX):
                caption = '项目入口'
            elif tid in attaching or control.get('status') == 'connecting':
                caption = '执舟中'
            elif not control.get('canControl'):
                caption = '待执舟 · 只读' if control.get('mode') == 'observe' else '待执舟'
            elif control.get('paused'):
                caption = '暂泊中' if self.client == 'claude' and status == 'active' else '已暂泊 · 待续航'
            else:
                caption = {'active': '行舟中', 'systemError': '执行异常'}.get(status, '已执舟')
            title_sync = (controls.get('titleSync') or {}).get(tid) if self.client == 'codex' else None
            if title_sync or (self.client, tid) in self._optimistic_titles:
                caption += ' · ' + ('标题冲突' if title_sync and title_sync.get('state') == 'conflict'
                                    and (self.client, tid) not in self._optimistic_titles else '标题待同步')
            group = groups[directory]
            model_label = self.organizer.label('task:' + self.client, tid)
            if group[0] not in group_settings and model_label and model_label.casefold() != group[1].casefold():
                matching = projects_by_name.get(model_label.casefold(), ())
                group = next(iter(matching)) if len(matching) == 1 else ('model:' + model_label, model_label, '')
            rows.append((tid, title, caption, directory, group))
        rows = self.order_tree_rows(rows, groups, group_settings)
        signature = (self.client, rows, group_settings)
        if signature != self._task_signature:
            previous_scroll = self.task_choice.verticalScrollBar().value()
            self._task_signature = signature
            group_names = Counter({row[4][0]: row[4][1].casefold() for row in rows}.values())
            with QSignalBlocker(self.task_choice):
                self.task_items.clear()
                self.project_items.clear()
                self.category_items.clear()
                self.task_choice.clear()
                seen_titles = {}
                for tid, title, caption, directory, (key, group_name, workspace) in rows:
                    group = self.project_items.get(key)
                    if group is None:
                        category, policy = group_settings.get(key, (None, None))
                        parent = self.task_choice
                        if category:
                            category_id, category_name = category
                            category_key = 'category:' + category_id
                            if category_key not in self.category_items:
                                heading = QTreeWidgetItem(self.task_choice, [category_name])
                                heading.setFont(0, ui_font(11, True))
                                heading.setData(0, PROJECT_CATEGORY_ROLE, category_key)
                                heading.setFlags(heading.flags() & ~(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled))
                                self.category_items[category_key] = heading
                            parent = self.category_items[category_key]
                        qualifier = folder_name(str(Path(workspace).parent)) if workspace else ''
                        caption_name = (f'{group_name} · {qualifier}' if qualifier and group_names[group_name.casefold()] > 1
                                        else group_name)
                        group = QTreeWidgetItem(parent, [caption_name])
                        group.setFont(0, ui_font(10, True))
                        group.setData(0, TASK_DIRECTORY_ROLE, key)
                        if workspace and Path(workspace).is_absolute():
                            group.setData(0, PROJECT_WORKSPACE_ROLE, workspace)
                        else:
                            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                        label = dict(POLICIES).get(policy, '') if policy else ''
                        group.setToolTip(0, '\n'.join(filter(None, (workspace, label))))
                        self.project_items[key] = group
                    if tid is None:
                        continue
                    title_key = key, title.casefold()
                    seen_titles[title_key] = seen_titles.get(title_key, 0) + 1
                    count = seen_titles[title_key]
                    visible_title = f'{title} · {count}' if count > 1 else title
                    item = QTreeWidgetItem(group, [f'{visible_title}\n{caption}'])
                    item.setData(0, Qt.ItemDataRole.UserRole, tid)
                    item.setData(0, TASK_TITLE_ROLE, title)
                    item.setData(0, TASK_DIRECTORY_ROLE, directory)
                    if tid.startswith(ENTRY_PREFIX):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                    item.setToolTip(0, '\n'.join(filter(None, (visible_title, caption, directory))))
                    self.task_items[tid] = item
                self.filter_tasks()
            self.task_choice.verticalScrollBar().setValue(previous_scroll)
        if self.selected_thread not in self.task_items:
            self.change_task(self.client, next(iter(self.task_items), ''))
        if self._project_intro_group is not None:
            header = self.project_items.get(self._project_intro_group)
            if header is not None and header.data(0, PROJECT_WORKSPACE_ROLE):
                self.show_intro_data(header, self.project_intro.key, header.data(0, PROJECT_WORKSPACE_ROLE))
                if self.task_choice.currentItem() is None:
                    with QSignalBlocker(self.task_choice):
                        self.task_choice.setCurrentItem(header)
                return
            self.show_conversation_page()
        item = self.task_items.get(self.selected_thread)
        if self.task_choice.currentItem() is None and item is not None and item.parent().isExpanded():
            with QSignalBlocker(self.task_choice):
                self.task_choice.setCurrentItem(item)

    def order_tree_rows(self, rows, original_groups, group_settings):
        preferences = self.tree_preferences
        groups = {group[0]: group for group in original_groups.values() if group[2]}
        groups.update((row[4][0], row[4]) for row in rows)
        sections, buckets = {}, {key: {} for key in groups}
        for key in groups:
            category, _policy = group_settings.get(key, (None, None))
            section = 'category:' + category[0] if category else ''
            sections.setdefault(section, []).append(key)
        for tid, title, caption, path, group in rows:
            dest = preferences.destination(self.client, tid) if not tid.startswith(ENTRY_PREFIX) else None
            view_group = groups.get(dest, group)
            buckets[view_group[0]][tid] = (tid, title, caption, path, view_group)
        ordered = []
        for section, keys in sections.items():
            for key in preferences.ordered(self.client, 'projects:' + section, keys):
                sessions = buckets[key]
                ordered.extend(sessions[tid] for tid in preferences.ordered(self.client, 'sessions:' + key, sessions))
                if not sessions:
                    ordered.append((None, '', '', '', groups[key]))
        return ordered

    def all_tasks(self, snap, client=None):
        client = client or self.client
        controls = snap.get('controls') or {}
        names = controls.get('names') or {}
        labels = (getattr(monitor, 'history_labels', {}) or {}) if client == 'codex' else {}
        threads = {task['id']: task for task in snap.get('threads') or [] if task.get('id')}
        registered = {task['threadId']: task for task in controls.get('tasks') or [] if task.get('threadId')}
        projects = {}
        for tid in dict.fromkeys((*threads, *registered)):
            thread = threads.get(tid, {})
            control = registered.get(tid, {})
            history = labels.get(tid, {})
            directory = next((value for value in (thread.get('projectRoot'), thread.get('cwd'), control.get('cwd'), history.get('cwd'))
                              if isinstance(value, str) and value), '')
            optimistic = self._optimistic_titles.get((client, tid))
            title = thread.get('title') if tid.startswith(ENTRY_PREFIX) else (optimistic[1] if optimistic else names.get(tid))
            if not title:
                project = project_group(directory)[1]
                title = display_text(self.session_titles.title(
                    tid, project, thread.get('title'), thread.get('name'), control.get('title'),
                    history.get('title'), codex=client == 'codex',
                ) or '未题会话')
            projects[tid] = title, directory
        return projects

    def switch_client(self, client):
        if client == self.client:
            self.client_switch.select(client)
            return
        self.show_conversation_page()
        self.client_selections[self.client] = self.selected_thread
        self.change_task(client, self.client_selections[client])
        self.client_switch.select(client)
        self.task_search.clear()
        self.control_message.setText('已切换到 ' + ('Claude Code' if client == 'claude' else 'Codex'))
        self.refresh()

    def refresh_verse(self):
        hour = int(time.time() // 3600)
        if hour != self.verse_hour:
            verse, author, title = COVER_VERSES[hour % len(COVER_VERSES)]
            self.hero_verse.setText(verse)
            self.hero_source.setText(f'唐 · {author}《{title}》')
            self.verse_hour = hour

    def refresh(self):
        self.refresh_tray_release()
        self.refresh_verse()
        snapshot = monitor.snapshot()
        snap = monitor.client_snapshot(snapshot, self.client)
        if self.client == 'codex' and snap.get('indexedProjects'):
            threads = {t['id']: t for t in snap.get('threads', [])}
            for indexed in snap['indexedProjects']:
                tid = indexed['id']
                live = threads.get(tid, {})
                threads[tid] = {**indexed, **live, 'cwd': live.get('cwd') or indexed['cwd']}
            snap['threads'] = list(threads.values())
        if self.notebook is not None and self.workspace_pages.currentWidget() is self.notebook:
            self.notebook.refresh_day()
        flow = monitor.personal_sync
        self._project_sync_view = self.local_project_settings.snapshot() if flow is None else {}
        if flow:
            view = flow.snapshot()
            self._project_sync_view = view
            revision = view.get('notebookRevision', 0)
            if self.notebook is not None and revision != self._notebook_revision and self.notebook.refresh_synced():
                self._notebook_revision = revision
            if self.client == 'codex':
                threads = {t['id']:t for t in snap.get('threads',[])}
                for tid, session in view['sessions'].items():
                    threads[tid] = {**threads.get(tid,{'id':tid,'title':'跨机会话 · '+session['project']}),'cwd':session['cwd']}
                retired = view.get('retirements', {})
                snap['threads'] = [row for tid, row in threads.items() if tid not in retired]
                if retired and snap.get('controls'):
                    controls = snap['controls']
                    snap['controls'] = {**controls, 'tasks': [row for row in controls['tasks'] if row['threadId'] not in retired]}
            if self._pending_open:
                project, epoch = self._pending_open
                paths = {**view['projects'], **view.get('folderProjects', {})}
                if project not in paths:
                    self._pending_open = None
                    self._popup('无法启卷','项目已移出本机同步清单。')
                elif view['completed'].get(project,0) > epoch or '配对' in view['status'] and project not in view['status']:
                    self._pending_open = None
                    status = view['status'].get(project,'项目尚未准备好')
                    if status == '已同步':
                        self.launch_project(Path(paths[project]))
                    else:
                        self._popup('无法启卷',status)
        self._last_snapshot = snap
        if self.client == 'codex':
            self.session_titles.refresh()
        connected = snap.get('connection') == 'connected'
        self.live.setText('● 已连接' if connected else '○ 连接待检查')
        if self.live.property('connected') != str(connected).lower():
            self.live.setProperty('connected', str(connected).lower())
            self.live.style().unpolish(self.live)
            self.live.style().polish(self.live)
        threads = snap.get('threads') or []
        controls = snap.get('controls') or {}
        projects = self.all_tasks(snap)
        if self.organizer.settings.base_url:
            for client in ('codex', 'claude'):
                candidates = projects if client == self.client else self.all_tasks(monitor.client_snapshot(snapshot, client), client)
                for key, (title, directory) in candidates.items():
                    if not key.startswith(ENTRY_PREFIX):
                        self.organizer.observe('task:' + client, key, title, project=project_group(directory)[1])
        running = {task.get('id') for task in threads if (task.get('status') or {}).get('type') == 'active'}
        for task in controls.get('tasks') or []:
            tid = task.get('threadId')
            if task.get('paused'):
                running.discard(tid)
            elif (task.get('threadStatus') or {}).get('type') == 'active':
                running.add(tid)
        self.stat_total.setText(str(len(projects)))
        self.stat_running.setText(str(len(running)))
        self.render_tasks(projects, threads, controls)
        self.render_selection(snap)
        self.render_guard_quota(snap)

    def render_selection(self, snap):
        if self._rename_target is not None and self._rename_target != (self.client, self.selected_thread):
            self.cancel_rename()
        task_title = self.task_title() or '未尽的话，可以接着说。'
        self.conversation_heading.setText(task_title)
        title_state = self.current_title_sync(snap)
        self.title_sync_button.setVisible(bool(title_state))
        if title_state:
            conflict = title_state.get('state') == 'conflict'
            self.title_sync_button.setText('标题冲突' if conflict else '待同步')
            self.title_sync_button.setEnabled(conflict)
            self.title_sync_button.setToolTip(title_state.get('message', '等待 Codex 确认标题。'))
        project_entry = self.selected_thread.startswith(ENTRY_PREFIX)
        renameable = self.selected_thread and not project_entry
        self.conversation_heading.setToolTip(f'{task_title}\n双击标题或按 F2 改名' if renameable else '')
        self.conversation_heading.setAccessibleDescription('双击标题或按 F2 改名' if renameable else '')
        self.message_edit.setReadOnly(project_entry)
        item = self.task_items.get(self.selected_thread)
        directory = item.data(0, TASK_DIRECTORY_ROLE) if item is not None else ''
        self._project_directory = Path(directory) if directory and Path(directory).is_absolute() else None
        self._sync_project = None
        if monitor.personal_sync:
            remote = next((t.get('githubProject') for t in snap.get('threads',[]) if t['id']==self.selected_thread),None)
            codex_thread = self.selected_thread if self.client == 'codex' else None
            mapping = monitor.personal_sync.resolve(codex_thread,directory,remote)
            if mapping:
                self._sync_project,self._project_directory = mapping
        self.render_controls(snap)
        if self._project_intro_group is None:
            self.refresh_conversation()

    def begin_rename(self):
        if not self.selected_thread or self.selected_thread.startswith(ENTRY_PREFIX):
            return
        self._rename_target = (self.client, self.selected_thread)
        self.title_editor.setText(self.task_title())
        self.title_stack.setCurrentWidget(self.title_editor)
        self.title_editor.setFocus()
        self.title_editor.selectAll()

    def cancel_rename(self):
        had_focus = self.title_editor.hasFocus()
        self._rename_target = None
        self.title_stack.setCurrentWidget(self.conversation_heading)
        if had_focus:
            self.conversation_heading.setFocus()

    def save_rename(self):
        target = self._rename_target
        title = self.title_editor.text().strip()
        if not title or target != (self.client, self.selected_thread):
            return
        self.cancel_rename()
        if title != self.task_title():
            if target[0] == 'codex':
                title = ' '.join(title.split())
                controls = self._last_snapshot.get('controls') or {}
                current = next((row for row in controls.get('tasks', ()) if row['threadId'] == target[1]), {})
                row = next((row for row in self._last_snapshot.get('threads', ()) if row['id'] == target[1]), {})
                state = self.current_title_sync(self._last_snapshot)
                base = state.get('nativeTitle') if state and state.get('state') == 'conflict' else current.get('nativeName', row.get('nativeName'))
                self.queue_title(target, 'rename_codex', {'title': title, 'base': base}, title)
            else:
                self.operate('rename', title, tid=target[1])

    def current_title_sync(self, snap):
        if self.client != 'codex':
            return None
        if (self.client, self.selected_thread) in self._optimistic_titles:
            return {'state': 'pending'}
        return (snap.get('controls') or {}).get('titleSync', {}).get(self.selected_thread)

    def queue_title(self, key, action, payload, title):
        controller = monitor.get_controller(key[0])
        if controller is None:
            self._popup('标题未保存', '后台正在启动，请稍候重试。')
            return
        revision = str(uuid.uuid4())
        if title is not None:
            self._optimistic_titles[key] = revision, title
            self.refresh()
        future = self._submission_pool.submit(controller.submit, key[1], action, json.dumps(payload), revision)
        future.add_done_callback(lambda result: self.title_submission_ready.emit(key, revision, result))

    def title_submission_finished(self, key, revision, future):
        current = self._optimistic_titles.get(key)
        if current and current[0] == revision:
            del self._optimistic_titles[key]
        self.refresh()
        try:
            future.result()
        except Exception as error:
            if current and current[0] == revision and key == (self.client, self.selected_thread):
                self.begin_rename()
                self.title_editor.setText(current[1])
            self._popup('标题未保存', str(error))

    def resolve_title_conflict(self):
        state = self.current_title_sync(self._last_snapshot)
        if not state or state.get('state') != 'conflict':
            return
        key = self.client, self.selected_thread
        dialog = QMessageBox(self)
        dialog.setWindowTitle('选择会话标题')
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f'待同步标题  {state["title"]}\nCodex 原生标题  {state.get("nativeTitle") or "未命名"}')
        native = dialog.addButton('采用原生标题', QMessageBox.ButtonRole.AcceptRole)
        submit = dialog.addButton('再次提交新标题', QMessageBox.ButtonRole.ActionRole)
        dialog.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() in (native, submit):
            self.queue_title(key, 'resolve_codex_title', {'revision': state['revision'],
                             'useNative': dialog.clickedButton() is native}, None)

    def open_project(self):
        if self.has_local_project():
            blocked = self.project_occupancy_message(project=self._sync_project) if self._sync_project else None
            if blocked:
                self.control_message.setText(blocked)
                return
            self.launch_project(self._project_directory)
            self.schedule_local_project()
        elif self._sync_project:
            self.request_project(self._sync_project)
        else:
            self.launch_project(self._project_directory)

    def schedule_local_project(self):
        flow = monitor.personal_sync
        if not flow or not self.has_local_project():
            return
        project = self._sync_project
        if project is None:
            boundary = configured_root(monitor.ROOT)
            _key, _title, workspace = project_group(str(self._project_directory), boundary=boundary)
            project = self.project_sync_id(workspace, boundary)
        if project:
            flow.request_after_open(project)

    def has_local_project(self):
        directory, project = self._project_directory, self._sync_project
        return bool(directory and directory.is_dir() and not (
            project and project.startswith('.folder/') and receiver_placeholder(directory, project)))

    def project_sync_id(self, workspace, boundary):
        if not workspace or not Path(workspace).is_absolute():
            return None
        path = Path(workspace)
        for key in ('folderProjects', 'projects'):
            mappings = self._project_sync_view.get(key, {})
            matches = [(len(Path(root).parts), name) for name, root in mappings.items() if path.is_relative_to(root)]
            if matches:
                return max(matches)[1]
        if boundary and path.is_relative_to(boundary):
            return folder_id(path.relative_to(boundary).as_posix())
        return folder_id(str(path))

    def open_project_settings(self):
        settings = monitor.personal_sync or self.local_project_settings
        boundary = configured_root(monitor.ROOT)
        projects = {}
        for key in ('projects', 'folderProjects'):
            for name, workspace in self._project_sync_view.get(key, {}).items():
                project = self.project_sync_id(workspace, boundary) or name
                projects[project] = folder_name(workspace) or name, workspace
        roots = frozenset(Path(row['cwd']) for row in self._last_snapshot.get('indexedProjects', [])
                          if row['id'].startswith(ENTRY_PREFIX))
        for _title, directory in self.all_tasks(self._last_snapshot).values():
            _key, label, workspace = project_group(directory, roots, boundary)
            if project := self.project_sync_id(workspace, boundary):
                projects.setdefault(project, (label, workspace))
        projects = dict(sorted(projects.items(), key=lambda row: row[1][0].casefold()))
        dialog = ProjectSyncDialog(self, settings, projects)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.refresh()
        finally:
            dialog.deleteLater()

    def request_project(self, project):
        blocked = self.project_occupancy_message(project=project)
        if blocked:
            self.control_message.setText(blocked)
            return
        self._pending_open = project, monitor.personal_sync.request(project)
        self.control_message.setText('正在后台同步 '+project+'，完成后打开 VS Code')

    def launch_project(self, directory):
        if directory is None or not directory.is_dir():
            self._popup('无法启卷', '当前任务的项目目录在本机不可用。')
            return
        editor = vscode_executable()
        if editor is None:
            self._popup('无法启卷', '本机未找到 VS Code。')
            return
        try:
            subprocess.Popen([str(editor), str(directory)])
        except OSError as error:
            self._popup('无法启卷', f'VS Code 启动失败。{error}')

    def _popup(self, title, message):
        QTimer.singleShot(0, lambda title=title, message=message: self._popup_now(title, message))

    def _popup_now(self, title, message, *, on_top=False):
        box = QMessageBox(self)
        if on_top:
            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        box.setObjectName('lanxiDialog')
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(display_text(message))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText('知道了')
        self._dialogs.append(box)
        box.finished.connect(lambda _result: self._dialogs.remove(box) if box in self._dialogs else None)
        if on_top:
            box.show()
            box.raise_()
        else:
            box.open()

    def submit_composer(self):
        text = self.message_edit.toPlainText().strip()
        if self.send_button.isEnabled() or (self.client == 'codex' and (text == '/' or text in Composer.SLASH_OPTIONS)):
            self.operate('message')

    def open_codex_options(self, command):
        if self.client != 'codex' or not self.selected_thread or self.selected_thread.startswith(ENTRY_PREFIX):
            self._popup('请先选择任务', '在案头选择一个 Codex 会话。')
            return
        controller = monitor.get_controller('codex')
        if controller is None:
            self._popup('Codex 尚未就绪', '后台正在启动，请稍后重试。')
            return
        field = command[1:]
        request = (self.selected_thread, field, uuid.uuid4())
        self._codex_request = request
        self.message_edit.slash.popup().hide()
        self.message_edit.clear()
        self.control_message.setText('正在读取 Codex 选项…')

        def read_options():
            try:
                result = controller.codex_options(request[0], field)
            except RPCError as error:
                result = (error.detail or '当前 Codex 版本暂未提供此选项。')[:240]
            except ValueError as error:
                result = str(error)
            except Exception:
                result = 'Codex 选项暂不可用，请检查连接后重试。'
            try:
                self.codex_options_ready.emit(request, result)
            except RuntimeError:
                pass  # The window closed during the read.

        threading.Thread(target=read_options, daemon=True, name='codex-options').start()

    def show_codex_options(self, request, result):
        if self._quitting or request != self._codex_request or self.workspace_pages.currentWidget() is not self.task_workspace:
            return
        self._codex_request = None
        self.control_message.clear()
        if self.message_edit.toPlainText():
            return
        if isinstance(result, str):
            self._popup('Codex 选项暂不可用', result)
            return
        settings, options = result
        if not options:
            self._popup('Codex 选项暂不可用', '当前 Codex 没有返回可显示的选项。')
            return
        target, field, _ = request
        menu = QMenu(self)
        menu.setMinimumWidth(190)
        menu.setToolTipsVisible(True)
        menu.setAccessibleName(Composer.SLASH_OPTIONS['/' + field])
        self._codex_menu = menu
        for option in options:
            action = menu.addAction(option['title'])
            action.setToolTip(option['detail'] or '')
            action.setCheckable(True)
            action.setChecked(settings.get(field) == option['value'])
            action.setEnabled(option['allowed'])
            action.triggered.connect(
                lambda _checked, value=option['value']: self.operate(
                    'codex_settings', json.dumps({'field': field, 'value': value}), tid=target))
        menu.aboutToHide.connect(lambda: self.close_codex_menu(menu))
        editor = self.message_edit
        menu.popup(editor.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 5)))

    def close_codex_menu(self, menu):
        if self._codex_menu is menu:
            self._codex_menu = None
        menu.deleteLater()

    def operate(self, action, text_override=None, tid=None):
        waiting = self.tray_control_guard()
        if waiting and action != 'rename':
            self.control_message.setText(waiting)
            return
        text = self.message_edit.toPlainText() if action == 'message' else (text_override or '')
        if action == 'message' and self.client == 'codex':
            command = text.strip()
            if command in Composer.SLASH_OPTIONS:
                self.open_codex_options(command)
                return
            if command == '/':
                self.message_edit.setFocus()
                self.message_edit.show_slash()
                return
        target = self.selected_thread if tid is None else tid
        if action != 'create_test':
            blocked = self.project_occupancy_message(target)
            if blocked:
                self.control_message.setText(blocked)
                return
        if target and target.startswith(ENTRY_PREFIX) and action != 'create_test':
            return
        if action != 'create_test' and not target:
            self._popup('无法操作', '请先选择一个任务。')
            return
        if action == 'message' and not text.strip():
            self._popup('消息未发送', '请输入要发送的内容。')
            return
        controller = monitor.get_controller(self.client)
        if controller is None:
            self._popup('操作失败', '后台仍在启动，请稍候重试。')
            return
        try:
            if action == 'message':
                self.queue_message(controller, target, text)
                return
            if any(not future.done() for future in self._submissions):
                operation = str(uuid.uuid4())
                self._pending_operations[(self.client, operation)] = {'action': action, 'threadId': target, 'text': text}
                self.submit_in_background(controller, (self.client, target), action, text, operation)
                self.control_message.setText('已排队，等待后台处理。')
                return
            operation = controller.submit(target, action, text, str(uuid.uuid4()))
            self._pending_operations[(self.client, operation)] = {'action': action, 'threadId': target, 'text': text}
            self.control_message.setText({
                'pause': '正在暂泊…', 'rename': '正在保存任务名称…', 'focus': '正在执舟…',
            }.get(action, '已提交，等待确认'))
        except Exception as error:
            self._popup('操作失败', str(error))

    def queue_message(self, controller, thread, text):
        if len(text) > 10000:
            raise ValueError('请输入消息，最多10000字。')
        operation = str(uuid.uuid4())
        key = self.client, thread
        self._local_messages.setdefault(key, {})[operation] = {'text': text, 'status': '待发送', 'time': time.time()}
        self.message_edit.clear()
        self.message_edit.setFocus()
        self.refresh_local_messages(key)
        self.submit_in_background(controller, key, 'message', text, operation)

    def submit_in_background(self, controller, key, action, text, operation):
        future = self._submission_pool.submit(controller.submit, key[1], action, text, operation)
        self._submissions.add(future)
        future.add_done_callback(lambda result: self.submission_ready.emit(key, operation, result))

    def submission_finished(self, key, operation, future):
        self._submissions.discard(future)
        try:
            future.result()
        except Exception as error:
            item = self._local_messages.get(key, {}).get(operation)
            if item is not None:
                item.update(status='发送失败', error=display_text(error), recoverable=True)
                self.refresh_local_messages(key)
            elif self._pending_operations.pop((key[0], operation), None) is not None:
                self._popup('操作失败', str(error))

    def restore_local_message(self, url):
        if url.scheme() != 'lanxi-draft':
            return
        key = self.client, self.selected_thread
        item = self._local_messages.get(key, {}).get(url.path().removeprefix('/'))
        if item and item.get('recoverable'):
            if self.message_edit.toPlainText():
                QApplication.clipboard().setText(item['text'])
                self.control_message.setText('原消息已复制，当前草稿已保留。')
            else:
                self.message_edit.setPlainText(item['text'])
                self.message_edit.setFocus()

    def update_local_messages(self, controls):
        changed = set()
        for operation in controls.get('operations', ()):
            key = self.client, operation.get('threadId')
            item = self._local_messages.get(key, {}).get(operation['id'])
            if item is None:
                continue
            state = operation.get('status')
            detail = operation.get('message') or ''
            status = {'queued': '等待重连' if '重连' in detail else '待发送', 'running': '待发送',
                      'succeeded': '已送达', 'failed': '发送失败', 'cancelled': '发送失败',
                      'unknown': '送达未确认'}.get(state, item['status'])
            values = {'status': status, 'turnId': operation.get('messageTurnId'),
                      'recoverable': state in ('failed', 'cancelled', 'unknown'),
                      'error': display_text(detail) if state in ('failed', 'cancelled', 'unknown') else ''}
            if any(item.get(field) != value for field, value in values.items()):
                item.update(values)
                changed.add(key)
                if key == (self.client, self.selected_thread) and detail:
                    self.control_message.setText(display_text(detail))
        for key in changed:
            self.refresh_local_messages(key)

    def reconcile_local_messages(self, key, messages):
        pending = self._local_messages.get(key)
        if not pending:
            return
        matched = self._matched_messages.setdefault(key, set())
        counts = Counter()
        for message in messages:
            if message.get('role') != 'user':
                continue
            turn, message_id = message.get('turnId'), message.get('id')
            stamp = message.get('time', 0)
            identity = (turn, message_id) if message_id else (turn, stamp, message['text'])
            counts[identity] += 1
            occurrence = identity, counts[identity]
            if occurrence in matched:
                continue
            operation = message_id if message_id in pending else next((identifier for identifier, item in pending.items()
                if turn and turn == item.get('turnId') and message['text'] == item['text']
                and stamp >= item['time']), None)
            if operation is not None:
                matched.add(occurrence)
                pending.pop(operation, None)

    def refresh_local_messages(self, key):
        if key != self._visible_history or key != (self.client, self.selected_thread):
            return
        cached = self._history_cache.get(key)
        if cached:
            self.reconcile_local_messages(key, cached[0].get('messages', ()))
        document = self.conversation.document()
        scroll = self.conversation.verticalScrollBar()
        position = scroll.value()
        follow = position >= scroll.maximum() - 4
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        start = getattr(document, 'lanxi_send_tail', None)
        if start is not None and start <= cursor.position():
            cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        document.lanxi_send_tail = cursor.position()
        historical = getattr(document, 'lanxi_history_frames', getattr(document, 'message_frames', ()))
        document.lanxi_history_frames = historical
        tails = []
        for operation, item in self._local_messages.get(key, {}).items():
            tails.append(message_html('你 · ' + item['status'], item['text'], False))
            if item.get('recoverable'):
                detail = escape(item.get('error') or '')
                tails.append(f'<p align="right" style="font-size:9pt;">{detail} '
                             f'<a href="lanxi-draft:/{operation}">取回原文</a></p>')
        if tails:
            cursor.insertHtml(''.join(tails))
        frames = tuple((frame, False) for frame in document.rootFrame().childFrames()
                       if frame.firstPosition() > document.lanxi_send_tail)
        document.message_frames = (*historical, *frames)
        scroll.setValue(scroll.maximum() if follow else min(position, scroll.maximum()))

    def check_operation_results(self, controls):
        if controls.get('ready') is not True:
            return  # The worker has not loaded persistent history yet.
        self.update_local_messages(controls)
        states = self._seen_operation_states[self.client]
        for operation in reversed(controls.get('operations') or []):
            key, status = operation['id'], operation.get('status')
            previous = states.get(key)
            own = self._pending_operations.get((self.client, key))
            fresh = operation.get('stamp', 0) >= self._started_at
            if own and own['action'] == 'message' and status in ('queued', 'running'):
                message = operation.get('message')
                if message and message != own.get('lastProgress') and own['threadId'] == self.selected_thread:
                    self.control_message.setText(display_text(message))
                    own['lastProgress'] = message
            if own and own['action'] == 'message' and status in ('failed', 'unknown', 'cancelled'):
                if own['threadId'] == self.selected_thread and not self.message_edit.toPlainText():
                    self.message_edit.setPlainText(own['text'])
                elif own['threadId'] != self.selected_thread:
                    draft_key = (self.client, own['threadId'])
                    if not self.task_drafts.get(draft_key):
                        self.task_drafts[draft_key] = own['text']
            local_message = operation.get('action') == 'message' and key in self._local_messages.get((self.client, operation.get('threadId')), {})
            if status in ('failed', 'unknown') and previous != status and (own or fresh) and not local_message:
                self._popup('消息发送失败' if operation.get('action') == 'message' else '操作未完成', operation.get('message') or '请检查当前任务状态。')
            if own and status in ('succeeded', 'cancelled', 'failed', 'unknown'):
                self.control_message.setText(display_text(operation.get('message') or '操作已处理'))
                self._pending_operations.pop((self.client, key), None)
            states[key] = status

    def render_controls(self, snap):
        controls = snap.get('controls') or {}
        tasks = controls.get('tasks') or []
        selected = next((task for task in tasks if task.get('threadId') == self.selected_thread), None)
        quota_wait = (snap.get('quotaWaits') or {}).get(self.selected_thread) if self.client == 'codex' else None
        if not selected or selected.get('paused') or not selected.get('managed'):
            quota_wait = None
        waiting_quota = bool(quota_wait and quota_wait.get('state') in ('waiting', 'checking', 'reserved'))
        controlled = bool(selected)
        paused = controlled and bool(selected.get('paused'))
        manageable = controlled and bool(selected.get('canControl'))
        active = controlled and (selected.get('threadStatus') or {}).get('type') == 'active'
        managed = controlled and bool(selected.get('managed'))
        allowed = manageable and not paused and not waiting_quota
        buttons = self.action_buttons
        flow = monitor.personal_sync if self.client == 'codex' else None
        state = flow.snapshot().get('taskOwners') if flow else None
        buttons['handoff'].setVisible(bool(state))
        buttons['handoff'].setEnabled(bool(state and state['tasks'].get(self.selected_thread) == state['device']))
        buttons['manage'].setVisible(not managed)
        buttons['unmanage'].setVisible(managed)
        buttons['attach'].setEnabled(bool(self.selected_thread) and not self.selected_thread.startswith(ENTRY_PREFIX))
        buttons['pause'].setEnabled(controlled and not paused)
        buttons['resume'].setEnabled(manageable and paused and (self.client == 'codex' or not active))
        buttons['resume'].setToolTip(
            '继续任务 · 解除本地暂停，不重复启动当前轮次' if active and self.client == 'codex' else '继续任务')
        buttons['manage'].setEnabled(controlled and not managed)
        buttons['unmanage'].setEnabled(managed)
        buttons['detach'].setEnabled(controlled)
        buttons['open_project'].setEnabled(self._project_directory is not None)
        buttons['open_project'].setToolTip(
            f'在 VS Code 打开项目\n{self._project_directory}' if self._project_directory else '任务未提供本机项目目录')
        if waiting_quota:
            message_hint = '额度暂不可用，刷新后自动继续；点击“暂泊”可取消自动恢复。'
        elif allowed:
            message_hint = '运行中也可发送，新指令会优先进入当前会话。' if self.client == 'claude' else '运行中补充指令，空闲时开始新一轮。'
        elif selected and selected.get('paused'):
            if self.client == 'codex' and (selected.get('threadStatus') or {}).get('type') == 'active' and selected.get('canControl'):
                message_hint = 'Codex 已在后台运行；点击“续航”解除本地暂停后即可发送。'
            else:
                message_hint = '已暂泊，点「续航」后即可发送。'
        else:
            message_hint = '选择案头任务执舟后即可发送。'
        blocked = self.project_occupancy_message(self.selected_thread)
        waiting = self.tray_control_guard()
        buttons['attach'].setToolTip(waiting or blocked or '接入任务')
        if blocked or waiting:
            for name in ('attach', 'pause', 'resume', 'manage', 'unmanage', 'handoff'):
                buttons[name].setEnabled(False)
            allowed = False
        if blocked:
            buttons['open_project'].setEnabled(False)
        if waiting:
            buttons['detach'].setEnabled(False)
        self.message_hint.setText(waiting or blocked or message_hint)
        self.send_button.setEnabled(allowed)
        self.check_operation_results(controls)

    def project_occupancy_message(self, thread=None, *, project=None):
        flow = monitor.personal_sync
        if self.client != 'codex' or not (thread or project) or not flow:
            return None
        view = flow.snapshot()
        state = view.get('taskOwners')
        if not state:
            return None
        task_owner = state['tasks'].get(thread)
        if task_owner and task_owner != state['device']:
            name = monitor.workspace_codex.device_name(task_owner)
            return f'{name} 持有此会话。请在该电脑的澜汐中选择此会话并点击「交还任务」。'
        if project is None:
            item = self.task_items.get(thread)
            cwd = item.data(0, TASK_DIRECTORY_ROLE) if isinstance(item, QTreeWidgetItem) else None
            mapping = flow.resolve(thread, cwd)
            project = mapping[0] if mapping else None
        owner = state['projects'].get(project)
        if not owner or owner == state['device']:
            return None
        name = monitor.workspace_codex.device_name(owner)
        if any(state['tasks'].get(tid) == owner and session.get('project') == project
               for tid, session in view['sessions'].items()):
            return f'{name} 持有此项目的其他会话。请在该电脑的澜汐中交还该项目的任务。'
        return f'{name} 正在使用此项目。请在该电脑关闭项目的 IDE 窗口，或在澜汐中交还任务。'

    def render_guard_quota(self, snap):
        connection = '已连接' if snap.get('connection') == 'connected' else '连接待检查'
        if self.client == 'claude':
            tasks = (snap.get('controls') or {}).get('tasks') or []
            count = sum(1 for task in tasks if task.get('managed') and not task.get('paused'))
            self.guard_text.setPlainText(f'Claude Code · {connection}\n自动推进已设置：{count} 个未暂停任务，每小时检查一次。\n只对本监控可控制、空闲、无审批且无执行错误的会话发送。')
        else:
            count = sum(1 for task in (snap.get('controls') or {}).get('tasks') or [] if task.get('managed') and not task.get('paused'))
            waits = snap.get('quotaWaits') or {}
            waiting = waits.get(self.selected_thread)
            description = (waiting.get('message', '') + '\n下次检查：' + self.local_time(waiting.get('due'))) if waiting else '额度不足时等待刷新，服务端确认恢复后继续原任务。'
            self.guard_text.setPlainText(f'Codex · {connection}\n自动推进已开启：{count} 个任务\n{description}\n仍需控制权、空闲、有效目标且无待审批。')
        if snap.get('quotaStatus') != 'official_api' or not snap.get('quota'):
            self.quota_text.setPlainText('Claude Code CLI 未提供官方剩余额度，显示未知。' if self.client == 'claude' else '暂时无法查询额度\n未知不代表额度为零。')
            return
        lines = []
        quota = snap['quota']
        permission = quota.get('ordinaryUsageAllowed')
        if permission is not None:
            lines.append('当前额度：可用' if permission else '当前额度：等待恢复')
        for name, group in [('默认', quota.get('default') or {}), *list((quota.get('models') or {}).items())]:
            for key, caption in (('primary', '短周期'), ('secondary', '长周期')):
                window = (group or {}).get(key) or {}
                if window.get('usedPercent') is not None:
                    lines.append(f'{name} · {caption}：已用 {window["usedPercent"]}%')
                    if window.get('resetsAt'):
                        lines.append('重置：' + self.local_time(window['resetsAt']))
        self.quota_text.setPlainText('\n'.join(lines) or '尚无额度窗口数据')

    def configure_external_approval_alerts(self):
        self.show_window()
        try:
            home = hook_home()
            status = hook_status(monitor.ROOT, home)
            dialog = QMessageBox(self)
            dialog.setWindowTitle('VS Code 审批提醒')
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText({'current': '本机提醒已配置。请在 Codex 中确认已信任「澜汐审批提醒」。',
                            'outdated': '本机提醒配置需要更新。', 'missing': '本机尚未配置 VS Code 审批提醒。'}[status])
            install = dialog.addButton('启用或更新', QMessageBox.ButtonRole.AcceptRole)
            disable = dialog.addButton('停用', QMessageBox.ButtonRole.DestructiveRole) if status != 'missing' else None
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.exec()
            choice = dialog.clickedButton()
            if choice is None or choice not in (install, disable):
                return
            enabled = choice is install
            configure_approval_alerts(monitor.ROOT, home, enabled)
        except (OSError, ValueError, RuntimeError) as error:
            self._popup('无法更新审批提醒', str(error))
            return
        if not enabled:
            self._popup('审批提醒已停用', '重启 VS Code 后应用配置。')
        else:
            self._popup('审批提醒已配置',
                        '重启 VS Code，在 Codex 输入框旁的 Review hooks 审查并信任「澜汐审批提醒」。'
                        '\n也可以在同一账号的 Codex 终端使用 /hooks。'
                        '\n澜汐完全退出后，Windows 会显示提醒。审批在发起请求的 Codex 窗口确认。')

    def set_remote_completion_notifications(self, enabled):
        try:
            monitor.set_remote_completion_enabled(enabled)
        except OSError as error:
            self._popup('无法保存提醒设置', str(error))

    def refresh_external_approval_alerts(self):
        if self._quitting:
            return
        threads = {request['details'].get('threadId') for request in self._pending_approvals.values()
                   if request['client'] == 'codex' and not request['remote']}
        alerts = [row for row in take_alerts(monitor.ROOT) if row['thread'] not in threads]
        if not alerts:
            return
        descriptions = dict.fromkeys(f"{row['project'] or 'Codex 会话'} · {row['tool'] or '审批请求'}" for row in alerts)
        text = '\n'.join(list(descriptions)[:6])
        title = 'Codex 等待审批'
        for box in self._dialogs:
            if box.windowTitle() == title:
                box.setText(display_text(text + '\n请在发起请求的 Codex 窗口确认。'))
                box.raise_()
                return
        self._popup_now(title, text + '\n请在发起请求的 Codex 窗口确认。', on_top=True)

    def refresh_approvals(self):
        if self._quitting:
            return
        snapshot = monitor.approval_snapshot()
        self._approval_scope = snapshot['workspace']
        self._pending_approvals = {item['token']: item for item in snapshot['pending']}
        now = time.time()
        self._shown_approvals = {token: max(expires, self._pending_approvals.get(token, {}).get('expires', 0))
                                 for token, expires in self._shown_approvals.items()
                                 if expires + 60 > now or token in self._pending_approvals}
        receipts = []
        for token, (item, decision, recorded) in list(self._approval_feedback.items()):
            if decision in ('queued', 'unknown_delivery'):
                result = snapshot['decisions'].get(token)
                if decision == 'queued' and result is None and (item['expires'] <= now or self._approval_scope == 'local'):
                    result = 'unknown_delivery'
                if result is not None and result != decision:
                    self._approval_feedback[token] = item, result, now
                    receipts.append(result)
                    continue
            if decision != 'queued' and now - recorded > 300:
                del self._approval_feedback[token]
        if receipts:
            self.report_approval_results(receipts)
        self.render_approvals()
        self.offer_approval()

    def render_approvals(self):
        self.stat_pending.setText(str(len(self._pending_approvals)))
        reviewable = self._pending_approvals or self._approval_sending or self._approval_feedback
        self.review_button.setVisible(bool(reviewable))
        self.review_button.setText('查看待审批请求' if self._pending_approvals else '查看审批结果')
        if self.approval_dialog is None:
            return
        if reviewable:
            self.approval_dialog.update_requests(self._pending_approvals, self._approval_feedback,
                                                 self._approval_sending, self._approval_scope)
        else:
            self.approval_dialog.reject()

    def offer_approval(self, force=False):
        if self._quitting:
            return
        new_requests = self._pending_approvals.keys() - self._shown_approvals.keys()
        if not force and not new_requests:
            return
        if not (self._pending_approvals or self._approval_feedback or self._approval_sending):
            return
        if self.approval_dialog is None:
            self.approval_dialog = ApprovalQueueDialog(self, self.approval_task_title, self.submit_approval)
            self.approval_dialog.finished.connect(self.approval_closed)
            self.render_approvals()
        self.approval_dialog.showNormal()
        self.approval_dialog.raise_()
        self.approval_dialog.activateWindow()
        self._shown_approvals.update((token, item['expires']) for token, item in self._pending_approvals.items())

    def approval_task_title(self, token, request):
        params = request['details']
        tid = params.get('threadId')
        cwd = params.get('cwd')
        fallback = folder_name(cwd if isinstance(cwd, str) else '') or tid or '未提供'
        if token.startswith('remote:') or not isinstance(tid, str):
            return fallback
        client = 'claude' if request['client'] == 'claude' else 'codex'
        snapshot = monitor.client_snapshot(monitor.snapshot(), client)
        return self.all_tasks(snapshot, client).get(tid, (fallback, ''))[0]

    def approval_closed(self, _result):
        self.approval_dialog = None

    def submit_approval(self, tokens, allow):
        if self._quitting or self._approval_sending:
            return
        for token in tokens:
            item = self._pending_approvals.get(token)
            decision = self._approval_feedback.get(token, (None, 'pending', None))[1]
            if item and not item.get('decisionQueued') and decision not in INACTIVE and (not allow or item.get('canAllow')):
                self._approval_sending[token] = item
                self._approval_feedback.pop(token, None)
        if not self._approval_sending:
            return
        self.render_approvals()
        future = self._approval_pool.submit(monitor.decide_approvals, list(self._approval_sending), allow)

        def completed(result):
            if not self.stop.is_set():
                try:
                    self.approval_ready.emit(result)
                except RuntimeError:
                    pass  # Qt can close the window before a pending receipt arrives.

        future.add_done_callback(completed)

    def approval_finished(self, future):
        if self._quitting:
            return
        try:
            results = {row['token']: row['decision'] for row in future.result()}
        except Exception:
            results = {}
        now = time.time()
        decisions = []
        for token, item in self._approval_sending.items():
            decision = results.get(token, 'unknown_delivery')
            self._approval_feedback[token] = item, decision, now
            decisions.append(decision)
        self._approval_sending.clear()
        self.report_approval_results(decisions)
        self.refresh_approvals()

    def report_approval_results(self, decisions):
        counts = Counter(approval_status(decision) for decision in decisions)
        self.control_message.setText('审批 · ' + ' · '.join(f'{status} {count} 项' for status, count in counts.items()))

    def refresh_activity(self):
        thread = self.selected_thread
        if (self.client != 'codex' or self._project_intro_group is not None or not thread or thread.startswith(ENTRY_PREFIX)
                or not self.task_workspace.isVisible()):
            return
        key = thread, monitor.codex_activity.revision(thread)
        if key != self._activity_seen:
            previous = self._activity_seen
            self._activity_seen = key
            if key[1] or (previous and previous[0] == thread and previous[1]):
                self.refresh_conversation()

    def refresh_conversation(self):
        key = (self.client, self.selected_thread)
        if self.selected_thread.startswith(ENTRY_PREFIX):
            if key != self._visible_history:
                self.save_history_scroll()
            self._visible_history = key
            entry = next((row for row in self._last_snapshot.get('indexedProjects', []) if row['id'] == key[1]), None)
            if entry:
                lines = [entry['cwd']]
                if entry.get('branch'):
                    lines.append('分支 · ' + entry['branch'])
                if entry.get('memory'):
                    lines.append('项目记忆 · ' + entry['memory'])
                if entry.get('notes'):
                    lines.append('本机记忆文件 · ' + '、'.join(entry['notes']))
                lines.append('点击「启卷」在 VS Code 打开项目。')
                self._history_placeholder.setPlainText('\n\n'.join(lines))
                self.conversation.setDocument(self._history_placeholder)
            return
        cached = self._history_cache.get(key)
        if key != self._visible_history:
            self.save_history_scroll()
            self._visible_history = key
            if cached:
                self._history_cache.move_to_end(key)
                self.show_history_document(cached[1])
            else:
                text = '正在读取对话…' if key[1] else '未尽的话，可以接着说。选择一个任务查看对话。'
                self._history_placeholder.setPlainText(text)
                self._history_placeholder.lanxi_send_tail = None
                self._history_placeholder.message_frames = ()
                self._history_placeholder.lanxi_history_frames = ()
                self.conversation.setDocument(self._history_placeholder)
                self.refresh_local_messages(key)
        if not key[1] or self.stop.is_set():
            return
        if self._history_busy:
            self._history_repeat = True
            return
        self._history_busy = True
        future = self._history_pool.submit(load_conversation, key, cached[0] if cached else None,
                                           self._history_placeholder.defaultFont(),
                                           self._history_placeholder.defaultTextOption(),
                                           self.conversation.viewport().width(), self.thread(), self.stop)

        def completed(result):
            try:
                self.history_ready.emit(key, result)
            except RuntimeError:
                pass  # The window was already destroyed.

        future.add_done_callback(completed)

    def save_history_scroll(self):
        if self.conversation.document() is not self._history_placeholder:
            scroll = self.conversation.verticalScrollBar()
            self._history_scroll[self._visible_history] = (scroll.value(), scroll.value() >= scroll.maximum() - 4)

    def show_history_document(self, document):
        self.conversation.setDocument(document)
        scroll = self.conversation.verticalScrollBar()
        value, at_bottom = self._history_scroll.get(self._visible_history, (0, True))
        scroll.setValue(scroll.maximum() if at_bottom else min(value, scroll.maximum()))
        self.refresh_local_messages(self._visible_history)

    def history_loaded(self, key, future):
        self._history_busy = False
        repeat, self._history_repeat = self._history_repeat, False
        try:
            result = future.result()
        except Exception as error:
            result = error
        if self.stop.is_set() or key != (self.client, self.selected_thread):
            if isinstance(result, tuple):
                result[1].deleteLater()
            if not self.stop.is_set():
                self.refresh_conversation()
            return
        if isinstance(result, Exception):
            if key not in self._history_cache:
                self._history_placeholder.setPlainText('暂时无法读取对话：' + str(result))
                self.conversation.setDocument(self._history_placeholder)
            return
        if repeat:
            QTimer.singleShot(0, self.refresh_conversation)
        if result is None:
            return
        data, document, errors = result
        previous = self._error_events.get(key)
        if previous is not None:
            for encoded in errors - previous:
                self._popup('任务执行失败', str(json.loads(encoded)['status']['error']))
        self._error_events[key] = errors
        self.save_history_scroll()
        old = self._history_cache.pop(key, None)
        document.setParent(self)
        self._history_cache[key] = (data, document)
        self.show_history_document(document)
        if old:
            old[1].deleteLater()
        if len(self._history_cache) > 5:
            dropped, (_, stale_document) = self._history_cache.popitem(last=False)
            self._history_scroll.pop(dropped, None)
            stale_document.deleteLater()

    @staticmethod
    def local_time(value):
        if not value:
            return '暂无记录'
        try:
            return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(value) / (1000 if float(value) > 1e12 else 1)))
        except (ValueError, TypeError, OSError):
            return '时间不可用'

    def closeEvent(self, event):
        if not self._quitting and not getattr(QApplication.instance(), 'dispatching_quit', False):
            event.ignore()
            if self.notebook is not None and not self.notebook.flush():
                return
            if self._tray_hidden:
                return
            self._tray_hidden = True
            monitor.set_tray_mode(True)
            self.hide()
            self.refresh_tray_release()
            self.tray.showMessage('正在交还本机控制权', '右键点击澜汐托盘图标查看交还状态。',
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
            return
        if not self._quitting and not self.project_intro.confirm_exit():
            event.ignore()
            return
        self.shutdown()
        event.accept()


class DesktopApplication(QApplication):
    dispatching_quit = False

    def event(self, event):
        if event.type() != QEvent.Type.Quit:
            return super().event(event)
        # Qt closes top-level windows before it emits aboutToQuit.
        self.dispatching_quit = True
        try:
            return super().event(event)
        finally:
            self.dispatching_quit = False


def select_legacy_data(candidates):
    from desktop_storage import has_legacy_data
    dialog = QMessageBox()
    dialog.setWindowTitle('澜汐用户数据')
    dialog.setText('有旧版数据时可选择旧版 EXE 所在文件夹。全新开始会设置本机项目母目录。')
    choose = dialog.addButton('选择旧版目录', QMessageBox.ButtonRole.AcceptRole)
    fresh = dialog.addButton('全新开始', QMessageBox.ButtonRole.ActionRole)
    dialog.addButton('取消', QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(choose if candidates else fresh)
    directory = str(candidates[-1]) if candidates else str(Path.home())
    while True:
        dialog.exec()
        if dialog.clickedButton() == fresh:
            return False
        if dialog.clickedButton() != choose:
            return None
        selected = QFileDialog.getExistingDirectory(dialog, '选择旧版澜汐 EXE 所在的文件夹', directory)
        if selected:
            directory = selected
            if has_legacy_data(Path(selected)):
                return Path(selected)
            QMessageBox.information(dialog, '澜汐用户数据', '该文件夹中没有找到旧版澜汐数据。')


def choose_project_folder(parent=None):
    base = configured_root(monitor.ROOT) or Path.home()
    selected = QFileDialog.getExistingDirectory(parent, '选择集中存放项目的母文件夹', str(base))
    return Path(selected) if selected else None


def choose_workspace_startup(workspace):
    enrolled = bool(monitor.saved_config().get('coordination'))
    dialog = WorkspaceConnectDialog(None, monitor.ROOT, workspace, enrolled=enrolled, startup=True)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    if configured_root(monitor.ROOT) is None:
        folder = choose_project_folder()
        if folder is None:
            return False
        set_project_root(monitor.ROOT, folder)
    monitor.set_workspace_online(dialog.online)
    return True


def main():
    app = DesktopApplication(sys.argv)
    instance_key = 'CodexMobileMonitor.QtDesktop'
    verification = False
    probe = os.environ.get('LANXI_VERIFICATION_INSTANCE', '')
    if (re.fullmatch(r'[0-9a-f]{32}', probe) and app.platformName() == 'offscreen'
            and Path(os.environ.get('CODEX_HOME', '')) == monitor.PROGRAM_ROOT / 'codex'):
        try:
            if (monitor.PROGRAM_ROOT / '.lanxi-verification').read_text(encoding='ascii') == probe:
                verification = True
                instance_key += '.verification.' + probe
        except (OSError, UnicodeError):
            pass
    shared = QSharedMemory(instance_key)
    if not shared.create(1):
        import ctypes
        user32 = ctypes.windll.user32
        for title in (WINDOW_TITLE, 'Codex / Claude Code 任务运行监控', 'Codex 任务运行监控', 'Codex 本机守护'):
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
                return 0
        QMessageBox.information(None, '任务监控', '程序已在后台运行，请从系统托盘打开。')
        return 1
    window = None
    try:
        workspace = None if verification else bundled_workspace()
        try:
            parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
            parser.add_argument('--import-data-from', type=Path)
            options, _ = parser.parse_known_args()
            def legacy(candidates):
                return False if workspace and not candidates else select_legacy_data(candidates)

            if not monitor.prepare_desktop_data(legacy, options.import_data_from):
                return 0
            if workspace and not choose_workspace_startup(workspace):
                return 0
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            if verification:
                (monitor.PROGRAM_ROOT / '.lanxi-verification-error').write_text(str(error), encoding='utf-8')
                return 1
            QMessageBox.critical(None, '无法启动澜汐', f'打开或迁移用户数据失败。\n{error}')
            return 1
        window = MainWindow(shared, workspace)
        app.aboutToQuit.connect(window.shutdown)
        window.show()
        if getattr(sys, 'frozen', False) and not verification:
            window.updater.watch()
        if getattr(sys, 'frozen', False) and not verification and not workspace and configured_root(monitor.ROOT) is None:
            if monitor.config().get('coordination'):
                QTimer.singleShot(0, lambda: window.choose_project_root(first=True))
            else:
                QTimer.singleShot(0, lambda: window.open_cloud_pairing(first=True))
        return app.exec()
    finally:
        try:
            if window is not None:
                window.shutdown()
        finally:
            # Hold the lock through worker cleanup and event-loop shutdown, not
            # merely until the tray/window disappears.
            shared.detach()


if __name__ == '__main__':
    raise SystemExit(main())
