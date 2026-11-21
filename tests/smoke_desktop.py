"""Click real production Qt widgets against synthetic state (no model requests)."""
import argparse
import copy
import json
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import Future
from contextlib import closing
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QEnterEvent, QFontDatabase, QImage, QMouseEvent, QRawFont, QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton

import monitor
from automation import Approvals
from cloud_pairing import encode_setup
from codex_activity import COMMAND_DELTA, MESSAGE_DELTA, CodexActivity
from codex_history import read_codex_conversation
from codex_workspace import CodexWorkspace
from desktop_glass import FrostedPanel, OceanBackdrop
from desktop_model_api import ModelApiDialog
from desktop_notebook import GROUP_HEADER, NotebookStore
from desktop_pairing import CONNECT_CLOUD, PairingDialog, WorkspaceConnectDialog
from desktop_poetry import COVER_VERSES
from desktop_qt import MainWindow, choose_workspace_startup
from desktop_theme import configure_text, message_html, set_reading_html, ui_font
from desktop_wallpapers import WallpaperDialog, WallpaperLibrary
from model_groups import Organizer, read_settings
from personal_relay import Records
from reply_log import ReplyLog

CODEX = '00000000-0000-4000-8000-000000000001'
CLAUDE = '11111111-2222-4333-8444-555555555555'


class NoWorkers:
    def join(self, timeout=None):
        pass


class Shared:
    def detach(self):
        pass


def check_first_setup(window):
    from project_library import configured_root, set_project_root

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        folder = root / '开发项目'
        folder.mkdir()
        for invitation in ('', 'pairing-code-fixture'):
            data = root / ('paired' if invitation else 'local')
            data.mkdir()
            errors = []

            def submit():
                dialog = QApplication.activeModalWidget()
                try:
                    assert isinstance(dialog, PairingDialog)
                    assert dialog.windowTitle() == '设置项目母目录'
                    assert dialog.action.text() == '开始使用'
                    dialog.folder.setText(str(folder))
                    dialog.code.setText(invitation)
                    assert dialog.action.text() == ('连接并同步' if invitation else '开始使用')
                    QTest.mouseClick(dialog.action, Qt.MouseButton.LeftButton)
                except Exception as error:
                    errors.append(error)
                    if dialog is not None:
                        dialog.reject()

            with (patch.object(monitor, 'ROOT', data),
                  patch.object(monitor, 'activate_cloud_pairing') as activate,
                  patch('desktop_pairing.join', side_effect=lambda profile, _code, path: set_project_root(profile, path)) as join):
                QTimer.singleShot(0, submit)
                window.open_cloud_pairing(first=True)
                if errors:
                    raise errors[0]
                assert configured_root(data) == folder.resolve()
                if invitation:
                    join.assert_called_once_with(data, invitation, folder)
                    activate.assert_called_once_with(window.stop, window.worker)
                else:
                    join.assert_not_called()
                    activate.assert_not_called()


def check_workspace_startup():
    from project_library import configured_root

    with tempfile.TemporaryDirectory() as directory:
        profile = Path(directory)
        errors = []

        def select(connect):
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, WorkspaceConnectDialog)
                assert dialog.windowTitle() == '选择启动方式'
                assert dialog.local.text() == '本机使用' and dialog.connect.text() == CONNECT_CLOUD
                QTest.mouseClick(dialog.connect if connect else dialog.local, Qt.MouseButton.LeftButton)
            except Exception as error:
                errors.append(error)
                if dialog is not None:
                    dialog.reject()

        def connect(root, _grant):
            (root / 'local-config.json').write_text('{"coordination":{"url":"https://example.test"}}')

        with (patch.object(monitor, 'ROOT', profile), patch.object(monitor, '_workspace_online', True),
              patch('desktop_qt.QFileDialog.getExistingDirectory', return_value=str(profile)) as choose,
              patch('desktop_pairing.join_workspace', side_effect=connect) as join):
            QTimer.singleShot(0, lambda: select(False))
            assert choose_workspace_startup('fixture') and not errors
            assert configured_root(profile) == profile.resolve()
            assert monitor.personal_settings({'personalSync': {}}) is None
            choose.assert_called_once()
            join.assert_not_called()
            choose.reset_mock()

            QTimer.singleShot(0, lambda: select(True))
            assert choose_workspace_startup('fixture') and not errors
            join.assert_called_once_with(profile, 'fixture')
            assert monitor.config()['coordination']['url'] == 'https://example.test'
            choose.assert_not_called()

            QTimer.singleShot(0, lambda: select(False))
            assert choose_workspace_startup('fixture') and not errors
            assert 'coordination' not in monitor.config() and monitor.saved_config()['coordination']
            choose.assert_not_called()
            join.assert_called_once()


def check_diagnostics_pairing(window):
    settings = {}
    with (patch.object(monitor, 'config', return_value=settings),
          patch.object(window, 'open_device_pairing') as pair,
          patch('desktop_diagnostics.DiagnosticsDialog.run', autospec=True) as run):
        names = {button.text() for button in window.task_panel.findChildren(QPushButton)}
        assert names.isdisjoint({'测试', CONNECT_CLOUD, '合流'})
        buttons = {button.text(): button for button in window.edge_menu.panel.findChildren(QPushButton)}
        buttons[CONNECT_CLOUD].click()
        pair.assert_called_once_with(window)
        buttons['测试'].click()
        QTest.qWait(30)
        dialog = window.diagnostics
        run.assert_called_once_with(dialog, 'local')
        shortcut = next(button for button in dialog.findChildren(QPushButton) if button.text() == CONNECT_CLOUD)
        QTest.mouseClick(shortcut, Qt.MouseButton.LeftButton)
        pair.assert_called_with(dialog)
        assert {button.text() for button in dialog.findChildren(QPushButton)} == {
            '本机自检', '服务器测试', '项目会话诊断', CONNECT_CLOUD, '复制结果',
        }
        folder = r'C:\项目\my project'
        with patch('desktop_diagnostics.QFileDialog.getExistingDirectory', return_value=folder) as select:
            dialog.project_test.click()
            run.assert_called_with(dialog, 'project', ['--project-dir', folder])
            settings['coordination'] = {'url': 'https://example.test'}
            dialog.project_test.click()
            run.assert_called_with(dialog, 'project', ['--project-dir', folder, '--with-server'])
            select.return_value = ''
            run.reset_mock()
            dialog.project_test.click()
            run.assert_not_called()
        dialog.self_test.click()
        run.assert_called_with(dialog, 'local')
        dialog.server_test.click()
        run.assert_called_with(dialog, 'server')
        dialog.output.setPlainText('已检查服务器\n项目会话 2 条')
        with patch('desktop_diagnostics.QApplication.clipboard') as clipboard:
            dialog.copy.click()
            clipboard.return_value.setText.assert_called_once_with(dialog.output.toPlainText())
        dialog.close()
        from desktop_diagnostics import DiagnosticsDialog
        paired_dialog = DiagnosticsDialog(window, lambda: True, pair)
        QTest.qWait(30)
        run.assert_called_with(paired_dialog, 'server')
        paired_dialog.close()


def check_manual_sync(window):
    from personal_sync import PersonalSync

    flow = PersonalSync(Path('unused-fixture'), {})
    future = Future()
    button = next(b for b in window.edge_menu.panel.findChildren(QPushButton) if b.text() == '合流')
    assert '手动同步' in button.toolTip()
    with (patch.object(monitor, 'personal_sync', flow), patch.object(flow, 'request_manual_sync', return_value=future) as request):
        button.click()
        request.assert_called_once_with(fresh=False)
        dialog = window.manual_sync_dialog
        assert dialog.isVisible() and '等待后台' in dialog.body.toPlainText()
        future.set_running_or_notify_cancel()
        assert wait_until(lambda: '正在检查' in dialog.body.toPlainText())
        with patch.object(dialog, 'progress', return_value=('正在检查 GitHub 与服务器代码备份', 15, 1, 2,
                                                           {'owner/b': 'GitHub'})):
            assert wait_until(lambda: 'owner/b · GitHub' in dialog.body.toPlainText())
            assert '仓库已处理 1/2' in dialog.body.toPlainText()
        future.set_result({'connection': '已连接协调服务', 'projects': 2, 'memoryChecked': True,
                           'projectPaths': {'owner/a': 'D:/work/a', 'owner/b': 'D:/work/b'},
                           'status': {'owner/a': '已同步', 'owner/b': '两端修改发生冲突'},
                           'codeBackups': {'owner/a': '服务器存储暂不可用', 'owner/b': '已备份'},
                           'largeInventory': {'owner/a': {'data.bin': {'localSize': None}}},
                           'untrustedGit': ['D:/work/b']})
        assert wait_until(lambda: 'data.bin · 本机缺少' in dialog.body.toPlainText())
        assert all(text in dialog.body.toPlainText() for text in ('已同步 1 个，未同步 1 个', '项目记忆已检查', '发生冲突',
                                                                 'owner/a · 已同步', 'D:/work/b',
                                                                 '服务器代码备份已确认 1 个', '服务器备份 · 已备份'))
        assert dialog.trust_button.isVisible()
        retry = Future()
        with (patch('desktop_qt.QInputDialog.getItem', return_value=('D:/work/b', True)),
              patch('desktop_qt.trust_git_directory') as trust):
            request.return_value = retry
            dialog.trust_button.click()
            assert wait_until(lambda: request.call_count == 2)
            trust.assert_called_once_with('D:/work/b')
            request.assert_called_with(fresh=True)
            assert not dialog.trust_button.isVisible()
        dialog.close()


def check_external_approval_alert(window):
    from codex_approval_alerts import ALERTS

    editor = QFrame()
    editor.setWindowTitle('VS Code test window')
    editor.resize(580, 420)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inbox = root / '.local' / ALERTS
        inbox.mkdir(parents=True)
        with patch.object(monitor, 'ROOT', root):
            window.hide()
            editor.show()
            editor.raise_()
            editor.activateWindow()
            try:
                (inbox / ('a' * 32 + '.json')).write_text(json.dumps({
                    'project': 'Sample project', 'tool': 'Bash', 'thread': CODEX,
                }), encoding='utf-8')
                assert wait_until(lambda: any(box.windowTitle() == 'Codex 等待审批' for box in window._dialogs))
                alert = next(box for box in window._dialogs if box.windowTitle() == 'Codex 等待审批')
                assert alert.isVisible() and not window.isVisible()
                assert alert.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
                assert all(text in alert.text() for text in ('Sample project', '请在发起请求的 Codex 窗口确认'))
                assert not list(inbox.iterdir())
                alert.accept()
            finally:
                editor.close()
                window.show()
                window.raise_()
                window.activateWindow()


def check_project_occupancy(window):
    from uuid import uuid4

    from personal_sync import PersonalSync

    worker = PersonalSync(Path('unused-fixture'), {})
    project, thread = 'owner/repo', str(uuid4())
    worker.threads[thread] = {'project': project}
    worker.task_owners = {'device': 'local', 'tasks': {thread: 'peer'}, 'projects': {project: 'peer'}}
    with (patch.object(monitor, 'personal_sync', worker),
          patch.object(monitor.workspace_codex, 'device_name', return_value='OTHER-PC')):
        blocked = window.project_occupancy_message(thread)
        assert 'OTHER-PC 持有此会话' in blocked and '「交还任务」' in blocked
        assert '持有此项目的其他会话' in window.project_occupancy_message(project=project)
        worker.task_owners['tasks'].clear()
        assert '关闭项目的 IDE 窗口' in window.project_occupancy_message(project=project)
        worker.task_owners['projects'].clear()
        assert window.project_occupancy_message(project=project) is None


def check_automatic_pairing(window):
    with tempfile.TemporaryDirectory() as directory:
        profile = Path(directory)
        document = encode_setup({'url': 'https://example.test'}, 'A' * 43)
        setup = profile / 'fixture.lanxi-pairing'
        setup.write_text(document, encoding='utf-8')
        dialog = PairingDialog(window, profile)

        def wait_for_peer(_profile, _setup, _folder, *, waiting, cancel):
            waiting()
            assert cancel.wait(5)
            return False

        with (patch('desktop_pairing.QFileDialog.getOpenFileName', return_value=(str(setup), '')),
              patch('desktop_pairing.join', side_effect=wait_for_peer) as join):
            dialog.show()
            dialog.folder.setText(str(profile))
            load = next(button for button in dialog.findChildren(QPushButton) if button.text() == '导入双机设置…')
            QTest.mouseClick(load, Qt.MouseButton.LeftButton)
            assert dialog.action.text() == '申请自动配对'
            assert dialog.code.echoMode() == QLineEdit.EchoMode.Password
            QTest.mouseClick(dialog.action, Qt.MouseButton.LeftButton)
            assert wait_until(lambda: '已提交申请' in dialog.status.text())
            assert join.call_args.args == (profile, document, profile)
            QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
            assert wait_until(lambda: not dialog.isVisible()) and not dialog.paired


class FakeApprovalClient:
    client_type = 'claude'

    def __init__(self):
        self.sent = []

    def send(self, reply):
        self.sent.append(reply)


class FakeControl:
    def __init__(self, tid, title, managed):
        self.calls = []
        self.data = {'tasks': [{'threadId': tid, 'title': title, 'canControl': True, 'paused': False,
                               'managed': managed, 'mode': 'control', 'threadStatus': {'type': 'active'}}],
                     'operations': [], 'names': {}, 'ready': True}

    def snapshot(self):
        return copy.deepcopy(self.data)

    def submit(self, tid, action, text, request_id):
        self.calls.append((tid, action, text, request_id))
        self.data['operations'].insert(0, {'id': request_id, 'threadId': tid, 'action': action,
                                          'status': 'queued', 'message': '等待确认'})
        return request_id


def wait_until(condition, seconds=2):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.005)
    return condition()


def check_markdown(window, data, output):
    data['messages'] = [
        {'role': 'user', 'text': '帮我检查这次构建，把结果列出来。'},
        {'role': 'assistant', 'text': (
            '## 构建结果\n\n'
            '**桌面构建已通过。** 改动集中在 `desktop_qt.py`。\n\n'
            '1. 复核窗口布局\n'
            '2. 验证小窗口下的换行\n'
            '   - 输入区保持可用\n\n'
            '| 检查项 | 结果 |\n| --- | --- |\n| 桌面界面 | 通过 |\n\n'
            '~~~powershell\nif ($ready) {\n    .\\scripts\\build-exe.ps1\n}\n~~~\n\n'
            '> 下一步，打开[构建说明](https://example.org/build)核对发布包。'
        )},
        {'role': 'assistant', 'text': (
            '边界校验 [本地文件](file:///C:/private.txt) ![图片说明](https://example.org/image.png)'
            ' <b>原生标签</b>'
        )},
    ]
    size = window.size()
    window.resize(1320, 990)
    window.message_edit.clear()
    window.refresh()
    browser = window.conversation
    assert wait_until(lambda: '<b>原生标签</b>' in browser.toPlainText())
    document = browser.document()
    source = document.toHtml()
    assert '<h2 ' in source and '<ol ' in source and '<ul ' in source
    assert 'background-color:#e4eff7' in source and 'background-color:#eaf4fa' in source
    assert 'bgcolor="#dfedf6"' in source
    assert '    .\\scripts\\build-exe.ps1' in browser.toPlainText()
    assert '![图片说明]' not in browser.toPlainText() and '图片说明' in browser.toPlainText()
    assert '<img ' not in source and 'file:///' not in source
    assert 'href="https://example.org/build"' in source
    with patch('desktop_qt.QDesktopServices.openUrl') as open_url:
        browser.anchorClicked.emit(QUrl('file:///C:/private.txt'))
        browser.anchorClicked.emit(QUrl('https://example.org/build'))
        open_url.assert_called_once_with(QUrl('https://example.org/build'))
    browser.verticalScrollBar().setValue(0)
    assert browser.horizontalScrollBar().maximum() == 0
    window.grab().save(str(output / 'markdown-desktop.png'))
    window.resize(size)


def check_process_bubbles(window, output):
    window.select_workspace('codex')
    thread = window.selected_thread
    assert thread
    activity = CodexActivity()
    with tempfile.TemporaryDirectory() as directory:
        journal = ReplyLog(Path(directory) / 'replies.sqlite3', activity=activity)
        path = Path(directory) / 'replies.sqlite3'
        with closing(journal.db), patch.object(monitor, 'codex_activity', activity), \
             patch.dict('os.environ', CODEX_HOME=directory), \
             patch.object(monitor, 'conversation_for',
                          side_effect=lambda tid, _: read_codex_conversation(path, tid, activity=activity.snapshot(tid))):
            def event(method, **fields):
                journal.record({'method': method, 'params': {'threadId': thread, 'turnId': 'process-test', **fields}},
                               {thread})

            window.resize(1280, 910)
            chat = window.conversation
            event('item/completed', item={'type': 'userMessage', 'id': 'input',
                                         'content': [{'type': 'text', 'text': '检查这次桌面打包，给我看一下运行结果。'}]})
            command = dict(id='check', type='commandExecution', status='inProgress',
                           command=r'.\.venv\Scripts\python.exe build-exe/verify-lanxi-launch.py Lanxi-Glass-R16-Markdown.exe')
            event('item/started', item=command)
            assert wait_until(lambda: '执行札记 · 运行中' in chat.toPlainText())
            started = time.perf_counter()
            event(COMMAND_DELTA, itemId='check', delta='正在检查 Windows 原生窗口…\n')
            assert wait_until(lambda: '正在检查 Windows 原生窗口' in chat.toPlainText())
            delta_ms = (time.perf_counter() - started) * 1000
            assert chat.toPlainText().count('执行札记') == 1
            window.grab().save(str(output / 'process-running.png'))

            event('item/completed', item={**command, 'status': 'completed', 'exitCode': 0, 'durationMs': 1630,
                                          'aggregatedOutput': 'Native window and SQLite verified in 1.63s\n  └ 已在隔离目录核对启动。'})
            assert wait_until(lambda: '执行札记 · 已完成 · 1.63 秒' in chat.toPlainText())
            assert '  └ 已在隔离目录核对启动。' in chat.toPlainText()
            assert chat.toPlainText().count('执行札记') == 1 and '运行中' not in chat.toPlainText()
            event(MESSAGE_DELTA, itemId='answer', delta='## 验证结果\n\n')
            assert wait_until(lambda: '验证结果' in chat.toPlainText())
            event('item/completed', item={'id': 'answer', 'type': 'agentMessage',
                                         'text': '## 验证结果\n\n**窗口与本地数据库均已通过验证。**'})
            assert wait_until(lambda: '窗口与本地数据库均已通过验证。' in chat.toPlainText())
            assert [role for _, role in chat.document().message_frames] == [False, 'process', True]
            assert '<h2 ' in chat.document().toHtml()
            assert chat.horizontalScrollBar().maximum() == 0
            window.grab().save(str(output / 'process-completed.png'))
            for number in range(55):
                event('item/completed', item={
                    'id': f'command-{number}', 'type': 'commandExecution', 'status': 'completed',
                    'command': f'fixture command {number}', 'exitCode': 0,
                })
            assert wait_until(lambda: 'fixture command 54' in chat.toPlainText())
            assert chat.toPlainText().count('检查这次桌面打包，给我看一下运行结果。') == 1
            assert '窗口与本地数据库均已通过验证。' in chat.toPlainText()
            print(f'Command delta became visible in {delta_ms:.1f} ms in the native synthetic test.')


def check_edge_menu(window, output):
    root, menu = window.centralWidget(), window.edge_menu
    geometry = window.client_switch.geometry()

    def move(x, y):
        QTest.mouseMove(window.windowHandle(), root.mapTo(window, QPoint(x, y)))

    move(80, 90)
    assert menu.panel.isHidden() and menu.height() == menu.HOT_ZONE
    assert menu.width() == root.width()
    assert root.childAt(QPoint(80, 1)) is menu
    with patch('desktop_qt.motion_enabled', return_value=True):
        move(80, 1)
        assert wait_until(lambda: menu.revealed and menu.fade.state() == menu.fade.State.Running)
        menu.fade.setCurrentTime(65)
        assert 0.0 < menu.opacity.opacity() < 1.0
        move(90, 12)
        assert wait_until(lambda: menu.opacity.opacity() == 1.0)
        assert menu.revealed and not menu.leave_delay.isActive()
        assert window.client_switch.geometry() == geometry
        window.grab().save(str(output / 'top-menu-open.png'))
        move(80, 90)
        assert wait_until(lambda: not menu.revealed and not menu.panel.isHidden())
        menu.fade.setCurrentTime(50)
        partial = menu.opacity.opacity()
        assert 0.0 < partial < 1.0
        move(80, 1)
        assert wait_until(lambda: menu.revealed)
        assert 0.0 < menu.fade.startValue() <= partial
        assert wait_until(lambda: menu.opacity.opacity() == 1.0)
        move(80, 90)
        assert wait_until(menu.panel.isHidden)
    with patch('desktop_qt.motion_enabled', return_value=False):
        move(80, 1)
        assert wait_until(lambda: menu.opacity.opacity() == 1.0)
        assert list(menu.menus) == ['澜汐', '任务', '显示', '设置', '窗口']
        assert menu.menus['设置'].actions()[0].text() == '大模型 API…'
        connect = next(button for button in menu.panel.findChildren(QPushButton) if button.text() == CONNECT_CLOUD)
        with patch.object(window, 'open_device_pairing') as pair:
            QTest.mouseClick(connect, Qt.MouseButton.LeftButton)
            pair.assert_called_once_with(window)
        check_app_menu(window, output)
        move(80, 1)
        tasks = menu.menus['任务']
        tasks.popup(menu.mapToGlobal(QPoint(80, menu.HEIGHT)))
        details = next(action for action in tasks.actions() if action.text() == '运行与额度')
        QTest.mouseClick(tasks, Qt.MouseButton.LeftButton, pos=tasks.actionGeometry(details).center())
        assert window.details_dialog.isVisible()
        window.details_dialog.hide()
        move(80, 90)
        assert wait_until(menu.panel.isHidden) and menu.height() == menu.HOT_ZONE
        assert window.client_switch.geometry() == geometry
        window.grab().save(str(output / 'top-menu-hidden.png'))


def check_app_menu(window, output):
    edge = window.edge_menu
    app_menu = edge.menus['澜汐']
    tray_menu = window.tray.contextMenu()
    tray_labels = [action.text() for action in tray_menu.actions()]
    assert [action.text() for action in app_menu.actions() if action.text() != '关于澜汐'] == tray_labels
    assert '检查更新…' not in tray_labels

    settings = {}
    result = []

    def inspect_popup():
        sync = next(action for action in app_menu.actions() if action.text() == '跨机接续')
        submenu = sync.menu()
        try:
            assert app_menu.isVisible() and edge.active_menu is app_menu
            app_menu.grab().save(str(output / 'top-menu-lanxi.png'))
            app_menu.setActiveAction(sync)
            QTest.keyClick(app_menu, Qt.Key.Key_Right)
            assert wait_until(submenu.isVisible)
            assert edge.revealed and edge.active_menu is app_menu
            assert [action.text() for action in submenu.actions()][0] == '使用邀请码连接…'
            QTest.keyClick(submenu, Qt.Key.Key_Escape)
            details = next(action for action in app_menu.actions() if action.text() == '运行与额度')
            QTest.mouseClick(app_menu, Qt.MouseButton.LeftButton, pos=app_menu.actionGeometry(details).center())
            assert window.details_dialog.isVisible()
            window.details_dialog.hide()
        except Exception as error:
            result.append(error)
        else:
            result.append(None)
        finally:
            submenu.hide()
            app_menu.hide()

    with (patch.object(monitor, 'config', return_value=settings), patch.object(monitor, 'personal_sync', None)):
        QTimer.singleShot(20, inspect_popup)
        button = app_menu.parentWidget()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert wait_until(lambda: len(result) == 1)
        if result[0] is not None:
            raise result[0]
        settings['coordination'] = {'url': 'https://example.test'}
        for menu in (app_menu, tray_menu):
            submenu = next(action.menu() for action in menu.actions() if action.text() == '跨机接续')
            submenu.popup(edge.mapToGlobal(QPoint(100, edge.HEIGHT)))
            assert submenu.actions()[0].text() == '邀请另一台电脑…'
            submenu.hide()

    with patch.object(sys, 'frozen', True, create=True), patch.object(window.updater, 'check') as check:
        for include_about in (False, True):
            packaged_menu = QMenu(window)
            window.populate_app_menu(packaged_menu, include_about=include_about)
            next(action for action in packaged_menu.actions() if action.text() == '检查更新…').trigger()
            packaged_menu.deleteLater()
        assert check.call_count == 2 and check.call_args.kwargs == {'manual': True}


def check_model_settings(window, output):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        organizer = Organizer(root, lambda _scope: None)
        seen = []

        def fill(dialog):
            dialog.show()
            dialog.address.setText('http://127.0.0.1:9/v1')
            dialog.model.setText('fixture-model')
            dialog.grab().save(str(output / 'model-api-dialog.png'))
            dialog.accept()
            seen.append(dialog.isHidden())
            return dialog.result()

        try:
            with patch.object(window, 'organizer', organizer), patch.object(window, 'notebook', None), \
                 patch.object(ModelApiDialog, 'exec', fill):
                window.edge_menu.menus['设置'].actions()[0].trigger()
                tid = window.selected_thread
                organizer.assign('task:' + window.client, tid, '发布准备')
                window.refresh()
                assert window.task_items[tid].parent().text(0) == '发布准备'
            assert seen == [True] and read_settings(root).model == 'fixture-model'
        finally:
            organizer.close()
            if organizer.worker:
                organizer.worker.join(3)


def check_wallpaper_manager(window, output, root):
    backdrop = window.centralWidget()
    first = backdrop.current_entry
    source_a, source_b = (root / folder / '临海.png' for folder in ('one', 'two'))
    for path, size, color in ((source_a, (120, 90), '#1c6c81'), (source_b, (90, 120), '#31486b')):
        path.parent.mkdir()
        image = QImage(*size, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(path))
    invalid = root / '无法读取.png'
    invalid.write_bytes(b'not an image')
    errors = []
    left = Qt.MouseButton.LeftButton

    def inspect():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, WallpaperDialog)
            assert dialog.selected_key() == first.key
            assert '当前显示' in dialog.info.text() and 'px' in dialog.info.text()
            assert dialog.location.text() == first.path.name and not dialog.preview.pixmap().isNull()
            assert dialog.grab().save(str(output / 'wallpaper-manager.png'))

            QTest.mouseClick(dialog.add, left)
            assert '已添加 2 张' in dialog.status.text() and '1 张无法读取' in dialog.status.text()
            managed = backdrop.library.images
            assert (managed / '临海.png').read_bytes() == source_a.read_bytes()
            assert (managed / '临海 (2).png').read_bytes() == source_b.read_bytes()
            assert dialog.selected_key() == 'imported/临海.png' and '120 × 90 px' in dialog.info.text()
            QTest.mouseClick(dialog.show_now, left)
            assert backdrop.current_entry.key == 'imported/临海.png' and backdrop.rotation.isActive()
            assert dialog.info.text().endswith('当前显示')
            QTest.mouseClick(dialog.toggle, left)
            assert backdrop.current_entry.key == 'imported/临海 (2).png'
            assert dialog.toggle.text() == '恢复轮换' and (managed / '临海.png').is_file()
            assert source_a.is_file() and source_b.is_file()
            QTest.mouseClick(dialog.current, left)
            assert dialog.selected_key() == backdrop.current_entry.key

            for entry in backdrop.entries:
                if entry.key not in backdrop.library.hidden:
                    dialog.reload(entry.key)
                    QTest.mouseClick(dialog.toggle, left)
            assert backdrop.current_entry is None and backdrop.current[0].isNull() and not backdrop.rotation.isActive()
            assert not dialog.current.isEnabled()
            reloaded = WallpaperLibrary(backdrop.library.assets, root)
            assert reloaded.hidden == {entry.key for entry in backdrop.entries}
            assert first.path.is_file()

            dialog.reload(first.key)
            QTest.mouseClick(dialog.toggle, left)
            assert backdrop.current_entry == first and not backdrop.rotation.isActive()
            assert first.key not in WallpaperLibrary(backdrop.library.assets, root).hidden
        except Exception as error:
            errors.append(error)
        finally:
            if dialog is not None:
                dialog.reject()

    selected_files = [str(path) for path in (source_a, source_b, invalid)]
    with (patch('desktop_wallpapers.QFileDialog.getOpenFileNames', return_value=(selected_files, '')),
          patch('desktop_glass.motion_enabled', return_value=False)):
        QTimer.singleShot(0, inspect)
        action = next(action for action in window.edge_menu.menus['设置'].actions() if action.text() == '壁纸管理…')
        action.trigger()
    if errors:
        raise errors[0]

    with patch('desktop_glass.local_wallpapers', return_value=[source_a, source_b]):
        fixed = OceanBackdrop(backdrop.library.assets.parent.parent, 0, root)
        try:
            assert fixed.current_entry == first and not fixed.rotation.isActive()
            local = next(entry for entry in fixed.entries if entry.path == source_b)
            assert fixed.show_wallpaper(local)
            assert fixed.current_entry == local and not fixed.rotation.isActive()
        finally:
            fixed.deleteLater()


def check_approval_queue(window, output):
    left = Qt.MouseButton.LeftButton
    local, peer = Approvals(), Approvals()
    source, target = CodexWorkspace(local), CodexWorkspace(peer)
    remote_rpc = FakeApprovalClient()
    remote_rpc.client_type = 'codex'
    local_rpc = FakeApprovalClient()
    method = 'item/commandExecution/requestApproval'
    tokens = [
        'remote:home-pc:' + peer.add(remote_rpc, {
            'id': number, 'method': method, 'params': {'cwd': 'D:/Projects/Vision', **details},
        })
        for number, details in ((101, {'command': 'python -m unittest'}),
                                (102, {'command': 'git status --short'}), (103, {}))
    ]

    with tempfile.TemporaryDirectory() as directory:
        records = Records(Path(directory) / 'records.sqlite3', 'test-workspace')

        def store(device):
            return SimpleNamespace(client=SimpleNamespace(device=device), index=records.index,
                                   read=records.read, put=records.put)

        requester, origin = store('this-pc'), store('home-pc')

        def publish_peer():
            with patch('codex_workspace.socket.gethostname', return_value='家庭工作站'):
                target.sync(origin)

        def sync_receipts():
            source.sync(requester)
            publish_peer()
            source.sync(requester)
            window.refresh_approvals()

        prior_scope = monitor.snapshot().get('codexWorkspaceWatch', 'local')
        window._approval_feedback.clear()
        with patch.object(monitor, 'approvals', local), patch.object(monitor, 'workspace_codex', source):
            try:
                monitor.update(codexWorkspaceWatch='connected')
                publish_peer()
                source.sync(requester)
                window.refresh_approvals()
                dialog = window.approval_dialog
                assert dialog is not None and dialog.isVisible() and len(dialog.items) == 3
                assert isinstance(dialog.queue_panel, FrostedPanel) and isinstance(dialog.detail_panel, FrostedPanel)
                assert dialog.checked_tokens() == [] and not dialog.approve_selected.isEnabled()
                assert '家庭工作站' in dialog.meta.text() and 'D:/Projects/Vision' in dialog.meta.text()
                assert dialog.details.toPlainText() == 'python -m unittest'
                assert tokens[2] not in dialog.eligible_tokens()
                QTest.keyClick(dialog.queue, Qt.Key.Key_Space)
                assert dialog.checked_tokens() == [tokens[0]]
                assert dialog.grab().save(str(output / 'approval-queue.png'))
                QTest.mouseClick(dialog.approve_selected, left)
                assert wait_until(lambda: dialog.items[tokens[0]].text(2) == '等待回执')
                assert not remote_rpc.sent and dialog.items[tokens[1]].text(2) == '待审批'
                assert dialog.grab().save(str(output / 'approval-awaiting-receipt.png'))
                sync_receipts()
                assert remote_rpc.sent == [{'id': 101, 'result': {'decision': 'accept'}}]
                assert dialog.items[tokens[0]].text(2) == '已同意' and window.approval_dialog is dialog

                local_token = local.add(local_rpc, {'id': 104, 'method': method, 'params': {'command': 'npm test'}})
                window.refresh_approvals()
                assert window.approval_dialog is dialog and dialog.items[local_token].text(1) == '本机'
                assert set(dialog.eligible_tokens()) == {tokens[1], local_token}
                QTest.mouseClick(dialog.approve_all, left)
                assert wait_until(lambda: dialog.items[tokens[1]].text(2) == '等待回执')
                assert local_rpc.sent == [{'id': 104, 'result': {'decision': 'accept'}}]
                assert [message['id'] for message in remote_rpc.sent] == [101]
                sync_receipts()
                assert [message['id'] for message in remote_rpc.sent] == [101, 102]
                assert dialog.items[tokens[1]].text(2) == '已同意' and not dialog.approve_all.isEnabled()
                dialog.queue.setCurrentItem(dialog.items[tokens[2]])
                assert dialog.reject_current.isEnabled()
                QTest.mouseClick(dialog.reject_current, left)
                assert wait_until(lambda: dialog.items[tokens[2]].text(2) == '等待回执')
                sync_receipts()
                assert remote_rpc.sent[-1] == {'id': 103, 'result': {'decision': 'decline'}}
                assert dialog.items[tokens[2]].text(2) == '已拒绝' and window.stat_pending.text() == '0'
            finally:
                if window.approval_dialog is not None:
                    window.approval_dialog.reject()
                monitor.update(codexWorkspaceWatch=prior_scope)
                window._approval_feedback.clear()
        window.refresh_approvals()


def check_client_switch(window, output):
    switch = window.client_switch
    codex, claude, notebook = switch.buttons.values()
    left = Qt.MouseButton.LeftButton
    for button in (codex, claude, notebook):
        assert button.font().bold()
    with patch('desktop_glass.motion_enabled', return_value=True):
        QTest.mousePress(claude, left)
        assert claude.isDown() and window.client == 'codex'
        QTest.mouseRelease(claude, left)
        QTest.qWait(65)
        assert .05 * switch.offsets['claude'] < switch.position < .92 * switch.offsets['claude'] and switch.velocity > 0
        midpoint, velocity = switch.position, switch.velocity
        QTest.mouseClick(codex, left, pos=QPoint(4, codex.height() // 2))
        assert window.client == 'codex'
        assert switch.position == midpoint and switch.velocity == velocity, 'Reverse from current position and velocity'
        QTest.qWait(400)
        assert not switch.spring.isActive() and switch.position == 0.0

        origin = codex.rect().center()
        drag_to = origin + QPoint(64, 0)
        QTest.mousePress(codex, left, pos=origin)
        global_pos = QPointF(codex.mapToGlobal(drag_to))
        QApplication.sendEvent(codex, QMouseEvent(QEvent.Type.MouseMove, QPointF(drag_to), global_pos,
                                                  Qt.MouseButton.NoButton, left, Qt.KeyboardModifier.NoModifier))
        assert abs(switch.position - 64) < .1 and window.client == 'codex'
        switch.grab().save(str(output / 'client-switch-drag.png'))
        QApplication.sendEvent(codex, QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(drag_to), global_pos,
                                                  left, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        assert window.client == 'claude'
        QTest.qWait(400)
        assert switch.position == switch.offsets['claude'] and not switch.spring.isActive()
    with patch('desktop_glass.motion_enabled', return_value=False):
        QTest.mouseClick(codex, left)
        assert switch.position == 0.0 and not switch.spring.isActive()


def check_codex_slash(window, codex, output):
    def choices(thread, field):
        assert thread == window.selected_thread
        if field == 'model':
            return {'model': 'model-a'}, [
                {'value': 'model-a', 'title': 'Model A', 'detail': '', 'allowed': True},
                {'value': 'model-b', 'title': 'Model B', 'detail': '', 'allowed': True},
            ]
        return {'permissions': ':read-only'}, [
            {'value': ':read-only', 'title': '只读', 'detail': '', 'allowed': True},
            {'value': ':workspace', 'title': '工作区', 'detail': '', 'allowed': True},
            {'value': ':danger-full-access', 'title': '完全访问', 'detail': '', 'allowed': False},
        ]

    editor = window.message_edit
    editor.clear()
    editor.setFocus()
    before = len(codex.calls)
    with patch.object(codex, 'codex_options', side_effect=choices, create=True):
        QTest.keyClicks(editor, '/')
        assert editor.slash.popup().isVisible() and editor.slash.completionCount() == 2
        QTest.keyClick(editor, Qt.Key.Key_Down)
        assert editor.slash.popup().currentIndex().data().startswith('/permissions')
        QTest.keyClick(editor, Qt.Key.Key_Up)
        assert editor.slash.popup().currentIndex().data().startswith('/model')
        editor.slash.popup().grab().save(str(output / 'codex-slash.png'))
        QTest.keyClick(editor, Qt.Key.Key_Return)
        assert wait_until(lambda: window._codex_menu and window._codex_menu.isVisible())
        menu = window._codex_menu
        assert menu.actions()[0].isChecked()
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(menu.actions()[1]).center())
        assert len(codex.calls) == before + 1
        model = codex.calls[-1]
        assert model[1] == 'codex_settings' and json.loads(model[2]) == {'field': 'model', 'value': 'model-b'}
        editor.setFocus()
        QTest.keyClicks(editor, '/p')
        assert editor.slash.popup().isVisible() and editor.slash.completionCount() == 1
        QTest.keyClick(editor, Qt.Key.Key_Return)
        assert wait_until(lambda: window._codex_menu and window._codex_menu.isVisible())
        menu = window._codex_menu
        assert menu.actions()[0].isChecked() and not menu.actions()[2].isEnabled()
        menu.grab().save(str(output / 'codex-permissions.png'))
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(menu.actions()[1]).center())
        permission = codex.calls[-1]
        assert permission[1] == 'codex_settings' and json.loads(permission[2]) == {'field': 'permissions', 'value': ':workspace'}
        assert len(codex.calls) == before + 2 and not editor.toPlainText()


def check_notebook(window, output):
    with tempfile.TemporaryDirectory() as directory, patch.object(monitor, 'ROOT', Path(directory)):
        database = Path(directory) / 'lanxi-notebook.sqlite3'
        store = NotebookStore(database)
        with closing(sqlite3.connect(database)) as db, db:
            db.executemany('INSERT INTO notebook(kind, title, body, filed) VALUES(?, ?, ?, ?)', [
                ('note', '旧的随记', '从前写下的正文。', 0),
                ('note', '旧的归档', '留在旧笺里的文字。', 1),
                ('todo', '把昨日的预览收进档案', '', 1),
                ('todo', '把同一项目的几段对话归拢', '', 0),
            ])
        switch = window.client_switch
        left = Qt.MouseButton.LeftButton
        previous_client = window.client
        previous_task = window.selected_thread
        original_draft = window.message_edit.toPlainText()
        panels = (window.task_panel, window.conversation_card)
        positions = [(panel.mapTo(window, QPoint()), panel.size()) for panel in panels]
        QTest.mouseClick(switch.buttons['notebook'], left)
        workspace = window.notebook
        assert workspace is not None and workspace.isVisible()
        assert not window.task_workspace.isVisible() and window.client == previous_client
        assert wait_until(lambda: switch.position == switch.offsets['notebook'])
        for panel, (position, size) in zip((workspace.list_panel, workspace.editor_panel), positions):
            assert isinstance(panel, FrostedPanel)
            assert (panel.mapTo(window, QPoint()), panel.size()) == (position, size)
        assert workspace.heading.text() == '拾得片羽' and workspace.list.count() == 1
        categories = workspace.category_switch
        assert categories.thumb_radius < window.client_switch.thumb_radius
        QTest.mouseClick(categories.buttons['note'], left)
        assert categories.buttons['note'].isChecked()
        assert workspace.body.toPlainText() == '旧的随记\n\n从前写下的正文。'
        assert workspace.list.item(0).data(Qt.ItemDataRole.CheckStateRole) is None
        workspace.show_archive()
        assert workspace.body.toPlainText() == '旧的归档\n\n留在旧笺里的文字。'
        workspace.file_note()
        assert not store.entries('note', True)
        QTest.mouseClick(workspace.create, left)
        workspace.body.setPlainText('给下次打开留一条线索。')
        QTest.qWait(520)
        assert workspace.list.currentItem().text() == '给下次打开留一条线索。'
        assert store.entries('note')[0][1:3] == ('', '给下次打开留一条线索。')
        assert workspace.saved_status.text() == '已收于此'
        window.grab().save(str(output / 'notebook-desktop.png'))

        QTest.mouseClick(categories.buttons['todo'], left)
        QTest.mouseClick(workspace.create, left)
        assert categories.buttons['todo'].isChecked() and workspace.heading.text() == '待理诸事'
        workspace.body.setPlainText('让海色随窗口慢慢透进来。')
        QTest.mouseClick(categories.buttons['note'], left)
        assert workspace.heading.text() == '拾得片羽' and workspace.body.toPlainText() == '给下次打开留一条线索。'
        QTest.mouseClick(categories.buttons['todo'], left)
        assert workspace.body.toPlainText() == '让海色随窗口慢慢透进来。'
        rows = [workspace.list.item(index) for index in range(workspace.list.count())]
        assert len(rows) == 3
        pending = rows[1]
        assert pending.checkState() == Qt.CheckState.Unchecked and rows[2].checkState() == Qt.CheckState.Checked
        delegate = workspace.list.itemDelegate()
        checkbox = delegate.check_rect(workspace.list.visualItemRect(pending)).center().toPoint()
        QTest.mouseClick(workspace.list.viewport(), left, pos=checkbox)
        assert pending.checkState() == Qt.CheckState.Checked and workspace.list.row(pending) == 1
        assert [row[3] for row in store.entries('todo')] == [0, 1, 1]
        workspace.list.setCurrentRow(0)
        window.grab().save(str(output / 'todo-desktop.png'))
        task_row = workspace.list.visualItemRect(rows[0])
        workspace.list.setFocus()
        focused = workspace.list.viewport().grab(task_row).toImage()
        workspace.body.setFocus()
        assert workspace.list.viewport().grab(task_row).toImage() == focused
        workspace.list.setCurrentItem(pending)
        QTest.keyClick(workspace.list, Qt.Key.Key_Space)
        assert pending.checkState() == Qt.CheckState.Unchecked and workspace.list.row(pending) == 1
        assert [row[3] for row in store.entries('todo')] == [0, 0, 1]

        QTest.mouseClick(categories.buttons['note'], left)
        QTest.mouseClick(workspace.create, left)
        workspace.body.setPlainText('离开工作区时保存。')
        QTest.mouseClick(switch.buttons[previous_client], left)
        assert not workspace.isVisible() and window.task_workspace.isVisible()
        assert window.selected_thread == previous_task and window.message_edit.toPlainText() == original_draft
        assert store.entries('note')[0][1:3] == ('', '离开工作区时保存。')
        organizer = Organizer(Path(directory), window.grouping_changed.emit)
        original_organizer = workspace.organizer
        workspace.organizer = organizer
        try:
            window.select_workspace('notebook')
            workspace.select_kind('todo')
            ids = [row[0] for row in store.entries('todo')]
            organizer.assign('todo', ids[-1], '发布准备')
            assert wait_until(lambda: any(workspace.list.item(i).data(GROUP_HEADER) == '发布准备'
                                          for i in range(workspace.list.count())))
            header = next(workspace.list.item(i) for i in range(workspace.list.count())
                          if workspace.list.item(i).data(GROUP_HEADER) == '发布准备')
            mime = QMimeData()
            mime.setText(str(ids[0]))
            drop = Mock()
            drop.source.return_value = workspace.list
            drop.position.return_value = QPointF(workspace.list.visualItemRect(header).center())
            drop.mimeData.return_value = mime
            workspace.list.dropEvent(drop)
            drop.acceptProposedAction.assert_called_once()
            assert store.groups('todo')[ids[0]] == '发布准备'
            assert workspace.list.row(workspace.items[ids[0]]) == workspace.list.row(
                next(workspace.list.item(i) for i in range(workspace.list.count())
                     if workspace.list.item(i).data(GROUP_HEADER) == '发布准备')) + 1
            assert store.entries('todo')[-1][3] == 1
            QApplication.processEvents()
            window.grab().save(str(output / 'grouped-todo-desktop.png'))
            check_daily_records(workspace, database, window, output)
            window.select_workspace(previous_client)
        finally:
            workspace.organizer = original_organizer
            organizer.close()


def check_daily_records(workspace, database, window, output):
    today = date.today()
    yesterday = today - timedelta(days=1)
    stamp = datetime.combine(yesterday, clock_time(12)).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    with closing(sqlite3.connect(database)) as db, db:
        db.executemany('INSERT INTO notebook(kind,title,body,filed,updated_at) VALUES(?, ?, ?, ?, ?)', [
            ('note', '', '昨日随记', 0, stamp),
            ('todo', '', '昨日已经办好', 1, stamp),
            ('todo', '', '留到今天接着做', 0, stamp),
        ])
    switch = workspace.category_switch
    left = Qt.MouseButton.LeftButton
    with patch('desktop_glass.motion_enabled', return_value=False):
        QTest.mouseClick(switch.buttons['note'], left)
        assert '昨日随记' not in [workspace.list.item(i).text() for i in range(workspace.list.count())]
        QTest.mouseClick(switch.buttons['todo'], left)
        visible = [workspace.list.item(i).text() for i in range(workspace.list.count())]
        assert '留到今天接着做' in visible and '昨日已经办好' not in visible
        assert switch.position == switch.offsets['todo']

    with patch('desktop_glass.motion_enabled', return_value=True):
        todo = switch.buttons['todo']
        start = todo.rect().center()
        end = start + QPoint(70, 0)
        QTest.mousePress(todo, left, pos=start)
        point = QPointF(todo.mapToGlobal(end))
        QApplication.sendEvent(todo, QMouseEvent(QEvent.Type.MouseMove, QPointF(end), point,
                                                 Qt.MouseButton.NoButton, left, Qt.KeyboardModifier.NoModifier))
        assert switch.offsets['todo'] < switch.position < switch.offsets['daily'] and workspace.kind == 'todo'
        QApplication.sendEvent(todo, QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(end), point,
                                                 left, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        assert workspace.kind == 'daily'
        assert wait_until(lambda: switch.position == switch.offsets['daily'] and not switch.spring.isActive())

    assert [workspace.list.item(i).text() for i in range(workspace.list.count())] == [
        today.isoformat(), yesterday.isoformat(),
    ]
    QTest.mouseClick(workspace.list.viewport(), left, pos=workspace.list.visualItemRect(workspace.list.item(1)).center())
    assert workspace.heading.text() == yesterday.isoformat() and workspace.body.isReadOnly()
    text = workspace.body.toPlainText()
    for content in ('拾得片羽', '昨日随记', '待理诸事', '已完成 · 昨日已经办好', '待完成 · 留到今天接着做'):
        assert content in text
    assert not workspace.create.isVisible() and not workspace.more.isVisible()
    window.grab().save(str(output / 'daily-records-desktop.png'))
    with patch('desktop_notebook.date') as calendar:
        calendar.today.return_value = today + timedelta(days=1)
        workspace.refresh_day()
        QTest.mouseClick(switch.buttons['note'], left)
        assert workspace.list.count() == 0 and not workspace.body.isReadOnly()
    workspace.refresh_day()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    codex = FakeControl(CODEX, '中文界面测试', True)
    claude = FakeControl(CLAUDE, 'Claude 运行测试', False)
    original_state = monitor.snapshot()
    window = None
    approvals = Approvals()
    approvals_rpc = FakeApprovalClient()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    data = {'messages': [{'role': 'user', 'text': '先查看进度，再继续下一步。'},
                         {'role': 'assistant', 'text': '当前任务正在执行，可以继续发送新指令。'}], 'events': []}
    project_directory = str(Path(__file__).resolve().parents[1])
    monitor.update(connection='connected', threads=[{'id': CODEX, 'title': '中文界面测试', 'status': {'type': 'active'}, 'cwd': project_directory}],
                   controls=codex.snapshot(), claude={'connection': 'connected', 'threads': [
                       {'id': CLAUDE, 'title': 'Claude 运行测试', 'status': {'type': 'active'}}], 'controls': claude.snapshot()})
    try:
        with tempfile.TemporaryDirectory() as wallpaper_directory, \
             patch('desktop_glass.WallpaperLibrary', side_effect=lambda assets, _: WallpaperLibrary(assets, Path(wallpaper_directory))), \
             patch('desktop_glass.local_wallpapers', return_value=[]), \
             patch.object(monitor, 'approvals', approvals), patch.object(monitor, 'controller', codex), patch.object(monitor, 'claude_controller', claude), \
             patch.object(monitor, 'conversation_for', side_effect=lambda *_: copy.deepcopy(data)), \
             patch.object(monitor, 'start_background_service', side_effect=lambda stop: (stop, NoWorkers())):
            window = MainWindow(Shared())
            window.resize(1020, 640)
            window.show()
            window.timer.stop()  # Explicit refreshes make assertions deterministic.
            QTest.qWait(120)
            assert window.windowTitle() == '澜汐'
            ocean = window.centralWidget()
            assert isinstance(ocean, OceanBackdrop) and not ocean.current[0].isNull()
            assert len(ocean.wallpapers) == 3 and ocean.rotation.interval() == 30 * 60_000
            assert abs(window.client_switch.geometry().center().x() - ocean.rect().center().x()) <= 1
            verse = window.findChild(QLabel, 'heroVerse')
            source = window.findChild(QLabel, 'heroSource')
            intro = window.findChild(QLabel, 'heroIntro')
            formatted = QTextDocument()
            formatted.setHtml(intro.text())
            assert verse.font().bold() and verse.font().pointSize() == 42
            assert [verse.y(), source.y(), intro.y()] == sorted([verse.y(), source.y(), intro.y()])
            with patch('desktop_qt.time', wraps=time) as clock:
                for index, (line, author, title) in enumerate((*COVER_VERSES, COVER_VERSES[0])):
                    clock.time.return_value = index * 3600
                    window.refresh()
                    assert (verse.text(), source.text()) == (line, f'唐 · {author}《{title}》')
                    assert verse.width() >= verse.fontMetrics().horizontalAdvance(line)
                clock.time.return_value += 3599
                window.refresh()
                assert verse.text() == COVER_VERSES[0][0]
            assert ' '.join(formatted.toPlainText().split()) == (
                '一处开始，处处继续。 白日写到的那一行，暮归时仍等着你。 '
                '终端续上未尽的话，掌中一瞥，便知今日行至何处。'
            )
            assert verse.width() >= verse.fontMetrics().horizontalAdvance(verse.text())
            task_heading = window.findChild(QLabel, 'taskPanelTitle')
            current_heading = window.conversation_heading
            assert task_heading.font() == current_heading.font()
            assert task_heading.mapTo(ocean, task_heading.rect().center()).y() == (
                current_heading.mapTo(ocean, current_heading.rect().center()).y())
            columns = window.summary.findChildren(QFrame, 'statColumn')
            assert len(columns) == 3 and len({column.width() for column in columns}) == 1
            for column in columns:
                caption, number = column.findChildren(QLabel)
                assert caption.font().pointSizeF() == 8.5 and not number.font().bold()
            assert len(window.summary.findChildren(QFrame, 'statDivider')) == 2
            normal = ocean.grab().toImage()
            original = ocean.current
            ocean.current = (original[0], QImage())
            window.client_switch.update()
            unblurred = ocean.grab().toImage()
            ocean.current = original
            window.client_switch.update()
            for card in (window.task_panel, window.conversation_card):
                assert isinstance(card, FrostedPanel)
                pos = card.mapTo(ocean, QPoint(card.width() // 2, card.height() * 2 // 3))
                pos *= normal.devicePixelRatio()
                assert normal.pixel(pos) != unblurred.pixel(pos), 'Frosted panel must sample the blurred wallpaper'
            switch = window.client_switch
            pos = switch.mapTo(ocean, QPoint(switch.width() - 9, switch.height() // 2))
            pos *= normal.devicePixelRatio()
            assert normal.pixel(pos) != unblurred.pixel(pos), 'The switch must sample the blurred wallpaper'
            thumb = switch.thumb_rect()
            pos = switch.mapTo(ocean, QPoint(round(thumb.center().x()), round(thumb.top()) + 4))
            pos *= normal.devicePixelRatio()
            assert normal.pixel(pos) != unblurred.pixel(pos), 'The thumb must also transmit wallpaper'
            assert switch.graphicsEffect() is None
            check_client_switch(window, args.output)
            assert window.conversation_header.opacity.opacity() == 0.0
            original_top = window.conversation.y()
            window.action_buttons['attach'].setFocus()
            QTest.qWait(190)
            assert window.conversation_header.opacity.opacity() == 1.0
            assert window.conversation.y() == original_top
            window.message_edit.setFocus()
            QTest.qWait(190)
            assert window.conversation_header.opacity.opacity() == 0.0
            window.task_search.setText('不存在的任务')
            assert window.task_items[CODEX].isHidden() and window.task_empty.isVisible()
            window.task_search.clear()
            assert not window.task_items[CODEX].isHidden()
            assert not hasattr(window, 'operation_log'), 'History must not be in the visible UI'
            # Reproduce the cold-start sequence, not only a preloaded snapshot:
            # placeholder -> DB ready with old failures -> more old pages arrive.
            cold_window = None
            initial_ready = codex.data.pop('ready')
            try:
                cold_window = MainWindow(Shared())
                cold_window.timer.stop()
                codex.data['ready'] = initial_ready
                old_failure = {'id': 'historic-not-attached', 'threadId': CODEX, 'action': 'message',
                               'status': 'failed', 'message': '请先接入这个任务。', 'stamp': 1}
                codex.data['operations'].append(old_failure)
                cold_window.refresh()
                codex.data['operations'].append(dict(old_failure, id='historic-late-arrival'))
                cold_window.refresh()
                QTest.qWait(40)
                assert not any(p.isVisible() for p in cold_window.findChildren(QMessageBox)), 'Startup must not replay old failures'
                assert not codex.calls, 'Startup must not submit control operations'
            finally:
                codex.data['ready'] = initial_ready
                codex.data['operations'].clear()
                if cold_window:
                    cold_window._quitting = True
                    cold_window.close()
                    cold_window.deleteLater()
                    app.processEvents()
            assert window.task_title() == '中文界面测试'
            other = '22222222-2222-4222-8222-222222222222'
            separate = '33333333-3333-4333-8333-333333333333'
            primary = {'id': CODEX, 'title': '中文界面测试', 'status': {'type': 'active'}, 'cwd': project_directory}
            monitor.update(threads=[
                primary, {'id': other, 'title': '整理笔记归档', 'cwd': project_directory},
                {'id': separate, 'title': '不同项目的会话', 'cwd': 'D:/Archive/mobile-monitor'},
            ])
            window.refresh()
            assert window.conversation_heading.text() == '中文界面测试'
            group = window.task_items[CODEX].parent()
            assert group.text(0).startswith(Path(project_directory).name) and group.childCount() == 2
            assert window.task_items[other].parent() is group
            assert window.task_items[separate].parent() is not group
            assert window.task_items[other].text(0) == '整理笔记归档\n待执舟'
            group.setExpanded(False)
            window.task_search.setText('笔记归档')
            assert group.isExpanded() and not window.task_items[other].isHidden()
            window.task_search.clear()
            assert not group.isExpanded()
            group.setExpanded(True)
            tree = window.task_choice
            viewport = tree.viewport()
            group_rect = tree.visualItemRect(group)
            branch_rect = QRect(0, group_rect.y(), group_rect.x(), group_rect.height())
            QTest.mouseMove(viewport, QPoint(viewport.width() - 1, viewport.height() - 1))
            open_branch = viewport.grab(branch_rect).toImage()
            QTest.mouseMove(viewport, group_rect.center())
            assert viewport.grab(branch_rect).toImage() == open_branch
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=group_rect.center())
            assert tree.currentItem() == group and group.isExpanded()
            assert viewport.grab(branch_rect).toImage() == open_branch
            window.grab().save(str(args.output / 'selected-project-group.png'))
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=branch_rect.center())
            assert not group.isExpanded(), 'The branch remains clickable'
            assert viewport.grab(branch_rect).toImage() != open_branch, 'The triangle follows the expansion state'
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=branch_rect.center())
            assert group.isExpanded() and viewport.grab(branch_rect).toImage() == open_branch
            window.message_edit.setPlainText('单个任务的草稿')
            second_row = window.task_choice.visualItemRect(window.task_items[other]).center()
            tree.customContextMenuRequested.emit(second_row)
            menu = QApplication.activePopupWidget()
            assert menu is not None and menu.actions()[0].text() == '查看会话 ID'
            menu.hide()
            captured_id = []
            def copy_session_id():
                dialog = QApplication.activeModalWidget()
                captured_id.append(dialog.text())
                next(btn for btn in dialog.buttons() if btn.text() == '复制 ID').click()
                dialog.reject()
            QTimer.singleShot(0, copy_session_id)
            with patch('desktop_qt.QApplication.clipboard') as clipboard:
                menu.actions()[0].trigger()
                clipboard.return_value.setText.assert_called_once_with(other)
            assert captured_id == [other] and window.selected_thread != other
            QTest.mouseClick(window.task_choice.viewport(), Qt.MouseButton.LeftButton, pos=second_row)
            assert window.selected_thread == other and codex.calls[-1][:2] == (other, 'focus')
            task_rect = tree.visualItemRect(window.task_items[other])
            focused_row = viewport.grab(task_rect).toImage()
            window.grab().save(str(args.output / 'selected-task.png'))
            window.task_search.setFocus()
            assert viewport.grab(task_rect).toImage() == focused_row
            assert window.message_edit.toPlainText() == ''
            window.refresh()
            assert window.task_items[other].text(0).endswith('执舟中')
            first_row = window.task_choice.visualItemRect(window.task_items[CODEX]).center()
            QTest.mouseClick(window.task_choice.viewport(), Qt.MouseButton.LeftButton, pos=first_row)
            assert codex.calls[-1][:2] == (CODEX, 'focus')
            assert window.message_edit.toPlainText() == '单个任务的草稿'
            window.message_edit.clear()
            for operation in codex.data['operations']:
                if operation['action'] == 'focus':
                    operation.update(status='succeeded', message='已执舟')
            window.refresh()
            editor = Path('C:/Program Files/Microsoft VS Code/Code.exe')
            with patch('desktop_qt.vscode_executable', return_value=editor), patch('desktop_qt.subprocess.Popen') as launch:
                QTest.mouseClick(window.action_buttons['open_project'], Qt.MouseButton.LeftButton)
                launch.assert_called_once_with([str(editor), project_directory])
            from personal_sync import PersonalSync
            with tempfile.TemporaryDirectory() as checkout:
                flow = PersonalSync(Path(checkout), {})
                project = 'fixture/work'
                destination = Path(checkout)/'work'
                flow.projects[project] = str(destination)
                flow.threads[CODEX] = {'project':project,'cwd':str(destination)}
                with patch.object(monitor,'personal_sync',flow), patch('desktop_qt.vscode_executable',return_value=editor), patch('desktop_qt.subprocess.Popen') as launch:
                    window.refresh()
                    QTest.mouseClick(window.action_buttons['open_project'], Qt.MouseButton.LeftButton)
                    assert flow.priority == project and flow.wake.is_set()
                    window.refresh()
                    launch.assert_not_called()
                    destination.mkdir()
                    flow.status[project] = '已同步'
                    flow.completed[project] = 1
                    window.refresh()
                    window.refresh()
                    launch.assert_called_once_with([str(editor),str(destination)])
                    QTest.mouseClick(window.action_buttons['open_project'], Qt.MouseButton.LeftButton)
                    del flow.projects[project]
                    with patch.object(window, '_popup') as popup:
                        window.refresh()
                        window.refresh()
                        popup.assert_called_once_with('无法启卷', '项目已移出本机同步清单。')
                    assert window._pending_open is None and launch.call_count == 1
                    flow.projects[project] = str(destination)
                    flow.threads[CLAUDE] = {'project': project, 'cwd': str(destination)}
                    window.switch_client('claude')
                    assert window.selected_thread == CLAUDE and window._sync_project is None
                    window.switch_client('codex')
                window.refresh()
            assert not window.action_buttons['manage'].isEnabled()
            assert window.action_buttons['unmanage'].isEnabled()
            assert window.action_buttons['pause'].isEnabled()
            # A running Codex turn can coexist with an older local pause after
            # reattach. Continue must stay clickable; actual routing is covered
            # with a persistent controller in test_codex_desktop.py.
            codex.data['tasks'][0]['paused'] = True
            window.refresh()
            assert not window.send_button.isEnabled()
            assert window.action_buttons['resume'].isEnabled()
            QTest.mouseClick(window.action_buttons['resume'], Qt.MouseButton.LeftButton)
            assert codex.calls[-1][:2] == (CODEX, 'resume')
            codex.data['tasks'][0]['paused'] = False
            window.refresh()
            assert window.send_button.isEnabled()
            if app.platformName() == 'windows':
                assert QFontDatabase.families(), 'Native Windows fonts must be available'
                assert QRawFont.fromFont(ui_font()).supportsCharacter(ord('澜')), 'Chinese glyphs are missing'
                icon = window.windowIcon().pixmap(128, 128).toImage()
                assert not icon.isNull() and icon.pixelColor(0, 0).alpha() == 0
            window.message_edit.setPlainText('Codex 草稿')
            window.grab().save(str(args.output / 'codex-desktop.png'))
            QTest.mouseClick(window.client_buttons['claude'], Qt.MouseButton.LeftButton)
            assert window.client == 'claude' and window.selected_thread == CLAUDE
            assert window.message_edit.toPlainText() == ''
            assert window.send_button.isEnabled(), 'An active controlled Claude session must accept input'
            assert window.action_buttons['manage'].isEnabled(), 'Disabled only after explicit enable, not for the whole Claude client'
            QTest.mouseClick(window.action_buttons['manage'], Qt.MouseButton.LeftButton)
            assert claude.calls[-1][1] == 'manage'
            claude.data['tasks'][0]['managed'] = True
            claude.data['tasks'][0]['autoProgress'] = {'state': 'scheduled', 'due': 2000000000}
            window.refresh()
            assert not window.action_buttons['manage'].isEnabled()
            assert window.action_buttons['manage'].text() == '扬帆'
            assert window.action_buttons['unmanage'].isEnabled()
            QTest.mouseClick(window.action_buttons['unmanage'], Qt.MouseButton.LeftButton)
            assert claude.calls[-1][1] == 'unmanage'
            claude.data['tasks'][0]['managed'] = False
            window.refresh()
            assert window.action_buttons['manage'].isEnabled()
            assert window.action_buttons['pause'].isEnabled()
            window.message_edit.setPlainText('请优先处理这个新指令')
            QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
            assert claude.calls[-1][:3] == (CLAUDE, 'message', '请优先处理这个新指令')
            message_id = claude.calls[-1][3]
            op = next(o for o in claude.data['operations'] if o['id'] == message_id)
            op.update(status='queued', message='连接中断，指令尚未发送。重连后将继续发送。')
            window.refresh()
            assert '重连后将继续发送' in window.control_message.text()
            assert window.message_edit.toPlainText() == ''
            QTest.mouseClick(window.action_buttons['pause'], Qt.MouseButton.LeftButton)
            assert claude.calls[-1][1] == 'pause'
            QTest.mouseDClick(window.conversation_heading, Qt.MouseButton.LeftButton)
            assert window.title_stack.currentWidget() == window.title_editor
            assert window.title_editor.selectedText() == 'Claude 运行测试'
            before = len(claude.calls)
            window.title_editor.setText('这笔不落款')
            QTest.keyClick(window.title_editor, Qt.Key.Key_Escape)
            assert window.title_stack.currentWidget() == window.conversation_heading
            assert len(claude.calls) == before
            QTest.mouseDClick(window.conversation_heading, Qt.MouseButton.LeftButton)
            window.title_editor.setText('我自己的任务名')
            window.refresh()
            assert window.title_editor.text() == '我自己的任务名'
            QTest.qWait(260)
            window.grab().save(str(args.output / 'inline-rename-desktop.png'))
            QTest.keyClick(window.title_editor, Qt.Key.Key_Return)
            assert claude.calls[-1][:3] == (CLAUDE, 'rename', '我自己的任务名')
            claude.data['names'][CLAUDE] = '我自己的任务名'
            window.refresh()
            assert window.task_title() == '我自己的任务名'
            # A newer draft must never be overwritten by failure of an earlier send.
            window.message_edit.setPlainText('新的未发送草稿')
            op.update(status='failed', message='Synthetic send failure ' + CLAUDE)
            window.refresh()
            QTest.qWait(40)
            popups = window.findChildren(QMessageBox)
            assert len([p for p in popups if p.isVisible()]) == 1
            assert CLAUDE not in popups[0].text()
            assert window.message_edit.toPlainText() == '新的未发送草稿'
            popups[0].accept()
            QTest.qWait(30)
            window.refresh()
            QTest.qWait(30)
            assert not any(p.isVisible() for p in window.findChildren(QMessageBox)), 'Do not repeat a failure popup'
            # Pause is in progress until the actual turn stops, not merely its ack.
            task = claude.data['tasks'][0]
            task['paused'] = True
            window.refresh()
            assert not window.send_button.isEnabled()
            assert not window.action_buttons['resume'].isEnabled()
            task['threadStatus'] = {'type': 'idle'}
            window.refresh()
            assert window.action_buttons['resume'].isEnabled()
            # New execution errors show a real popup; historical errors do not repeat.
            assert wait_until(lambda: ('claude', CLAUDE) in window._error_events)
            data['events'] = [{'method': 'error', 'time': 100, 'status': {'error': 'Synthetic execution error ' + CLAUDE}}]
            window.refresh_conversation()
            assert wait_until(lambda: any('Synthetic execution error' in p.text()
                                         for p in window.findChildren(QMessageBox) if p.isVisible()))
            popups = [p for p in window.findChildren(QMessageBox) if p.isVisible()]
            assert len(popups) == 1 and 'Synthetic execution error' in popups[0].text()
            assert CLAUDE not in window.conversation.toPlainText()
            popups[0].accept()
            QTest.qWait(30)
            window.message_edit.setPlainText('Claude 草稿')
            window.grab().save(str(args.output / 'claude-desktop.png'))
            QTest.mouseClick(window.client_buttons['codex'], Qt.MouseButton.LeftButton)
            assert window.message_edit.toPlainText() == 'Codex 草稿'
            QTest.mouseClick(window.client_buttons['claude'], Qt.MouseButton.LeftButton)
            assert window.message_edit.toPlainText() == 'Claude 草稿'
            assert window.task_title() == '我自己的任务名'
            assert not any(p.isVisible() for p in window.findChildren(QMessageBox))
            labels = '\n'.join(w.text() for w in window.findChildren(QLabel))
            assert CODEX not in labels and CLAUDE not in labels
            # Enter adds a newline; Ctrl+Enter submits once through the same route.
            task['paused'] = False
            task['threadStatus'] = {'type': 'active'}
            monitor.update(claude={'connection': 'connected', 'threads': [
                {'id': CLAUDE, 'title': '我自己的任务名', 'status': {'type': 'active'}}],
                'controls': claude.snapshot()})
            window.refresh()
            before = len(claude.calls)
            window.message_edit.setPlainText('键盘发送')
            window.message_edit.moveCursor(QTextCursor.MoveOperation.End)
            QTest.keyClick(window.message_edit, Qt.Key.Key_Return)
            assert len(claude.calls) == before and window.message_edit.toPlainText().endswith('\n')
            QTest.keyClick(window.message_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
            assert len(claude.calls) == before + 1
            assert claude.calls[-1][:3] == (CLAUDE, 'message', '键盘发送\n')
            assert window.message_edit.toPlainText() == ''
            task['paused'] = True
            task['threadStatus'] = {'type': 'idle'}
            window.refresh()
            window.message_edit.setPlainText('暂停时不能快捷发送')
            QTest.keyClick(window.message_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
            assert len(claude.calls) == before + 1
            # Exercise a long reply at the minimum window size. The send button
            # remains in view while conversation and details scroll independently.
            sample = ('已完成 **桌面布局优化**，保留双客户端独立任务。\n\n'
                      '```python\nprint("中文消息与代码")\n' + 'long_identifier_' * 70 + '\n```\n\n'
                      + '继续核对结果。 ' * 100)
            assert '&lt;script&gt;' in message_html('Codex', '<script>alert(1)</script>', True)
            data['messages'] = [{'role': 'assistant', 'text': sample}]
            data['events'] = []
            window.resize(1020, 640)
            window.refresh()
            assert wait_until(lambda: '中文消息与代码' in window.conversation.toPlainText())
            send_corner = window.send_button.mapTo(window.centralWidget(), window.send_button.rect().bottomRight())
            assert window.centralWidget().rect().contains(send_corner), 'Send must stay inside the small window'
            assert window.conversation.height() >= 130
            assert window.conversation.horizontalScrollBar().maximum() == 0, 'Replies must wrap without horizontal scrolling'
            before_position = window.message_edit.mapTo(window.centralWidget(), QPoint(0, 0))
            window.conversation.verticalScrollBar().setValue(0)
            window.refresh_conversation()
            assert window.message_edit.mapTo(window.centralWidget(), QPoint(0, 0)) == before_position
            window.grab().save(str(args.output / 'compact-desktop.png'))
            # Final previews show normal production copy, not synthetic errors.
            window.resize(1180, 760)
            data['messages'] = [
                {'role': 'user', 'text': '检查当前进度，继续下一步，并保留处理结果。'},
                {'role': 'assistant', 'text': '已整理当前进度，接下来会核对任务状态，再推进下一项工作。\n\n有新的结果，我会记在这里。'}]
            task['paused'] = False
            task['threadStatus'] = {'type': 'active'}
            claude.data['operations'].clear()
            window.message_edit.setPlainText('')
            window.control_message.setText('已连接，准备接收下一步指令')
            window.refresh()
            QTest.qWait(60)
            window.conversation.verticalScrollBar().setValue(0)
            window.grab().save(str(args.output / 'claude-desktop.png'))
            QTest.mouseClick(window.client_buttons['codex'], Qt.MouseButton.LeftButton)
            window.control_message.setText('任务正在运行，你可以随时补充指令')
            window.refresh()
            assert wait_until(lambda: '已整理当前进度，接下来会' in window.conversation.toPlainText())
            QTest.qWait(270)
            window.conversation.verticalScrollBar().setValue(0)
            document = window.conversation.document()
            frames = document.message_frames
            assert [assistant for _, assistant in frames] == [False, True]
            viewport = window.conversation.viewport()
            painted = viewport.grab().toImage()
            document.message_frames = ()
            bare = viewport.grab().toImage()
            document.message_frames = frames
            for table, assistant in frames:
                cell = table.cellAt(0, 0 if assistant else 1)
                text = document.documentLayout().blockBoundingRect(cell.firstCursorPosition().block())
                top_left = QPoint(round(text.left()) - 10, round(text.top()) - 10)
                dpr = painted.devicePixelRatio()
                corner = top_left * dpr
                center = (top_left + QPoint(30, 2)) * dpr
                assert painted.pixel(corner) == bare.pixel(corner), 'The corner must show the glass behind it'
                assert painted.pixel(center) != bare.pixel(center), 'The bubble fill must be visible past the radius'
            window.grab().save(str(args.output / 'codex-desktop.png'))
            window.task_search.setFocus()
            header = window.conversation_header
            point = QPoint(10, 10)
            app.sendEvent(header, QEnterEvent(QPointF(point), QPointF(header.mapTo(window, point)),
                                              QPointF(header.mapToGlobal(point))))
            QTest.qWait(200)
            assert window.conversation_header.opacity.opacity() == 1.0
            window.grab().save(str(args.output / 'hover-desktop.png'))
            app.sendEvent(header, QEvent(QEvent.Type.Leave))
            QTest.qWait(200)
            assert window.conversation_header.opacity.opacity() == 0.0
            # A request from the inactive client must still open the correct native dialog.
            token = approvals.add(approvals_rpc, {
                'id': 37, 'method': 'item/commandExecution/requestApproval',
                'params': {'threadId': CLAUDE, 'cwd': 'D:/Projects/Demo', 'command': 'npm run test'},
            })
            window.refresh_approvals()
            dialog = window.approval_dialog
            assert dialog and dialog.isVisible() and dialog.current_token() == token
            assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            assert any('D:/Projects/Demo' in node.text() for node in dialog.findChildren(QLabel))
            dialog.grab().save(str(args.output / 'approval-desktop.png'))
            dialog.reject()
            window.refresh_approvals()
            assert window.approval_dialog is None and not approvals_rpc.sent
            QTest.mouseClick(window.review_button, Qt.MouseButton.LeftButton)
            QTest.qWait(30)
            assert window.approval_dialog is not None
            dialog = window.approval_dialog
            dialog.items[token].setCheckState(0, Qt.CheckState.Checked)
            QTest.mouseClick(dialog.approve_selected, Qt.MouseButton.LeftButton)
            assert wait_until(lambda: dialog.items[token].text(2) == '已同意')
            assert approvals_rpc.sent == [{'id': 37, 'result': {'decision': 'accept'}}]
            assert window.stat_pending.text() == '0'
            dialog.reject()
            for expected_index, name in ((1, 'big-sur'), (2, 'catalina')):
                ocean.rotation.timeout.emit()
                QTest.qWait(700)
                assert ocean.current_entry == ocean.wallpapers[expected_index] and not ocean.current[1].isNull()
                assert ocean.previous is None and ocean.fade == 1.0
                window.grab().save(str(args.output / f'{name}-desktop.png'))
            ocean.rotation.timeout.emit()
            QTest.qWait(700)
            assert ocean.current_entry == ocean.wallpapers[0]
            # A quota wait is separate from a manual pause and has a real reset
            # timestamp. The current task makes that distinction visible.
            codex.data['tasks'][0]['threadStatus'] = {'type': 'idle'}
            reset_time = int(time.time()) + 1800
            monitor.update(threads=[{'id': CODEX, 'title': '中文界面测试', 'status': {'type': 'idle'}, 'cwd': project_directory}],
                           quotaStatus='official_api', quota={'ordinaryUsageAllowed': False, 'default': {
                               'primary': {'usedPercent': 100, 'resetsAt': reset_time},
                               'secondary': {'usedPercent': 62, 'resetsAt': reset_time + 3600}}, 'models': {}},
                           quotaWaits={CODEX: {'state': 'waiting', 'resetsAt': reset_time, 'due': reset_time + 2,
                                               'message': '额度已用完，等待刷新后自动继续。'}})
            window.control_message.setText('额度等待是自动暂停，不会反复重发指令')
            window.refresh()
            QTest.qWait(40)
            assert not window.send_button.isEnabled()
            assert '刷新后自动继续' in window.message_hint.text()
            assert '下次检查' in window.guard_text.toPlainText()
            assert window.action_buttons['pause'].isEnabled()
            window.grab().save(str(args.output / 'quota-wait-desktop.png'))
            codex.data['tasks'][0]['paused'] = True
            window.refresh()
            QTest.qWait(20)
            assert window.task_items[CODEX].text(0).endswith('已暂泊 · 待续航')
            assert window.message_hint.text() == '已暂泊，点「续航」后即可发送。'
            assert window.action_buttons['resume'].isEnabled()
            codex.data['tasks'][0]['paused'] = False
            monitor.update(quotaWaits={})
            window.refresh()
            assert window.send_button.isEnabled()
            window.close()
            assert not window.isVisible() and not window.stop.is_set(), 'Closing should keep workers in the tray'
            window.timer.stop()
            approvals.add(approvals_rpc, {
                'id': 38, 'method': 'item/commandExecution/requestApproval', 'params': {'command': 'npm run test'},
            })
            assert wait_until(lambda: window.approval_dialog is not None and window.approval_dialog.isVisible())
            assert not window.isVisible()
            dialog = window.approval_dialog
            dialog.hide()
            next_token = approvals.add(approvals_rpc, {
                'id': 39, 'method': 'item/commandExecution/requestApproval', 'params': {'command': 'git status'},
            })
            assert wait_until(lambda: dialog.isVisible() and next_token in dialog.items)
            assert window.approval_dialog is dialog
            QTest.mouseClick(dialog.reject_current, Qt.MouseButton.LeftButton)
            assert wait_until(lambda: window.stat_pending.text() == '1')
            assert approvals_rpc.sent[-1] == {'id': 38, 'result': {'decision': 'decline'}}
            dialog.queue.setCurrentItem(dialog.items[next_token])
            QTest.mouseClick(dialog.reject_current, Qt.MouseButton.LeftButton)
            assert wait_until(lambda: window.stat_pending.text() == '0')
            assert approvals_rpc.sent[-1] == {'id': 39, 'result': {'decision': 'decline'}}
            dialog.reject()
            window.show_window()
            window.timer.start(1000)
            QTest.qWait(30)
            assert window.isVisible(), 'The tray action must reopen the workspace'
            check_codex_slash(window, codex, args.output)
            check_notebook(window, args.output)
            check_edge_menu(window, args.output)
            check_model_settings(window, args.output)
            check_first_setup(window)
            check_workspace_startup()
            check_diagnostics_pairing(window)
            check_manual_sync(window)
            check_project_occupancy(window)
            check_external_approval_alert(window)
            check_automatic_pairing(window)
            check_markdown(window, data, args.output)
            check_process_bubbles(window, args.output)
            from project_library import ENTRY_PREFIX, ProjectLibrary, configured_root
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                project = root / 'projects' / 'new-project'
                project.mkdir(parents=True)
                (project / 'pyproject.toml').touch()
                (project.parent / '.git').mkdir()
                second_project = project.parent / 'second-project'
                second_project.mkdir()
                (second_project / 'package.json').touch()
                with patch.object(monitor, 'ROOT', root), patch('desktop_qt.QFileDialog.getExistingDirectory', return_value=str(project.parent)):
                    window.choose_project_root(first=True)
                assert configured_root(root) == project.parent
                library = ProjectLibrary(root, root / 'codex')
                monitor.update(indexedProjects=library.snapshot())
                with patch.object(monitor, 'ROOT', root), patch.object(monitor, 'personal_sync', None):
                    window.refresh()
                key = ENTRY_PREFIX + str(project)
                item = window.task_items[key]
                assert item.text(0).startswith(project.name)
                assert item.parent().text(0) == project.name
                assert window.task_items[ENTRY_PREFIX + str(second_project)].parent().text(0) == second_project.name
                window.task_choice.scrollToItem(item)
                calls = len(codex.calls)
                QTest.mouseClick(window.task_choice.viewport(), Qt.MouseButton.LeftButton,
                                 pos=window.task_choice.visualItemRect(item).center())
                assert window.selected_thread == key and len(codex.calls) == calls
                assert window.action_buttons['open_project'].isEnabled()
                assert not window.action_buttons['attach'].isEnabled()
                assert str(project) in window.conversation.toPlainText()
                monitor.update(indexedProjects=[])
                window.refresh()
            check_approval_queue(window, args.output)
            check_wallpaper_manager(window, args.output, Path(wallpaper_directory))
            print('Qt smoke passed: Markdown, glass, rotation, hover/focus, approval, dual-client controls, drafts, rename, failure dialogs, Chinese glyphs, layout and tray.')
    finally:
        if window is not None:
            window._quitting = True
            for dialog in window.findChildren(QDialog):
                dialog.reject()
            window.close()
            app.processEvents()
        monitor.update(**original_state)


if __name__ == '__main__':
    main()
