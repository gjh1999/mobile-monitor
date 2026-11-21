"""Exercise the real Qt application loop in an isolated subprocess, without CLIs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]


def probe(mode):
    sys.path.insert(0, str(PROJECT))
    from PySide6.QtCore import QSharedMemory, QTimer
    from PySide6.QtWidgets import QApplication
    import desktop_qt
    import desktop_update
    import monitor

    report = {'mode': mode, 'fallbackNeeded': False, 'joinCalls': 0, 'startedWorkers': 0}
    shared_key = 'CodexMonitorShutdownTest.' + uuid.uuid4().hex
    holder = {}
    tray_modes = []
    tray_status = {'state': 'idle', 'pending': 0, 'detail': ''}

    def set_tray_mode(hidden):
        tray_modes.append(hidden)
        if hidden:
            tray_status.update(state='waiting', pending=2, detail='等待当前对话完成')

    def start_workers(stop):
        entered = [threading.Event(), threading.Event()]
        finished = [threading.Event(), threading.Event()]

        def run(index):
            entered[index].set()
            stop.wait(3)
            finished[index].set()

        threads = [threading.Thread(target=run, args=(index,), daemon=True) for index in range(2)]
        for thread in threads:
            thread.start()
        assert all(event.wait(1) for event in entered)
        report['startedWorkers'] = len(threads)

        class Workers:
            def join(self, timeout=None):
                report['joinCalls'] += 1
                report['lockHeldDuringCleanup'] = holder['shared'].isAttached()
                for thread in threads:
                    thread.join(timeout=1)
                report['workersStopped'] = all(event.is_set() for event in finished)
        return stop, Workers()

    original_window = desktop_qt.MainWindow

    class ObservedWindow(original_window):
        def __init__(self, shared, workspace=None):
            holder['shared'] = shared
            holder['window'] = self
            super().__init__(shared, workspace)
            app = QApplication.instance()

            def about_to_quit():
                report['lockHeldUntilLoopExit'] = shared.isAttached()
                report['stopRequestedAtLoopExit'] = self.stop.is_set()
                report['trayHiddenAtLoopExit'] = not self.tray.isVisible()
            # main() connects shutdown after the window constructor returns.
            # Observe afterwards so direct QApplication.quit checks see the
            # completed aboutToQuit cleanup, not an earlier subscriber's state.
            QTimer.singleShot(0, lambda: app.aboutToQuit.connect(about_to_quit))

            def hide_first():
                self.close()
                report['hiddenBeforeExit'] = not self.isVisible()
                report['keptWorkersWhenHidden'] = not self.stop.is_set()
                report['lockHeldWhenHidden'] = shared.isAttached()
                report['trayPresentWhenHidden'] = self.tray.isVisible()
                self.close()
                item = self._tray_release_action
                report['waitShownWhileHidden'] = (item in self.tray.contextMenu().actions() and item.isVisible()
                                                  and not item.isEnabled() and '2 个任务' in item.text()
                                                  and '等待当前对话完成' in item.text())

            def restore_from_tray():
                action = next(action for action in self.tray.contextMenu().actions()
                              if action.text() == '打开澜汐')
                action.trigger()
                self.operate('focus', tid=str(uuid.uuid4()))
                report['visibleWhenRestored'] = self.isVisible()
                report['guardedUntilReleased'] = '正在交还' in self.control_message.text()
                tray_status.update(state='released', pending=0, detail='')
                self.refresh_tray_release()
                report['releaseSeenWhenRestored'] = ('已交还' in self._tray_release_action.text()
                                                     and not self.tray_control_guard())

            def minimize_to_taskbar():
                self.showMinimized()
                report['taskbarMinimized'] = self.isMinimized() and not self._tray_hidden

            def update_in_place():
                self.updater.staged = PROJECT / 'replacement.exe'
                self.updater.restart()

            def exit_from_menu():
                action = next(action for action in self.tray.contextMenu().actions()
                              if action.text() == '退出程序')
                action.trigger()
                # Cleanup is idempotent, including a second explicit close.
                self.close()

            def watchdog():
                report['fallbackNeeded'] = True
                app.quit()

            if mode in ('hidden', 'restored'):
                QTimer.singleShot(30, hide_first)
            if mode == 'restored':
                QTimer.singleShot(60, restore_from_tray)
            if mode == 'minimized':
                QTimer.singleShot(30, minimize_to_taskbar)
            exit_action = {'application_quit': app.quit, 'update': update_in_place}.get(mode, exit_from_menu)
            QTimer.singleShot(90, exit_action)
            QTimer.singleShot(500, watchdog)

    monitor.update(threads=[], controls={'ready': True, 'tasks': [], 'operations': []},
                   claude={'threads': [], 'controls': {'ready': True, 'tasks': [], 'operations': []}})
    with patch.object(desktop_qt, 'QSharedMemory', side_effect=lambda _: QSharedMemory(shared_key)), \
         patch.object(desktop_qt, 'MainWindow', ObservedWindow), \
         patch.object(monitor, 'start_background_service', side_effect=start_workers), \
         patch.object(monitor, 'conversation_for', return_value={'messages': [], 'events': []}), \
         patch.object(monitor, 'set_tray_mode', side_effect=set_tray_mode, create=True), \
         patch.object(monitor, 'tray_release_status', side_effect=lambda: dict(tray_status), create=True), \
         patch.object(desktop_update, 'restart_after_exit') as launch_new, \
         patch.object(monitor, 'controller', None), patch.object(monitor, 'claude_controller', None):
        report['exitCode'] = desktop_qt.main()
    report['trayModes'] = tray_modes
    report['updateRestartCalls'] = launch_new.call_count
    report['lockReleasedAfterMain'] = not holder['shared'].isAttached()
    replacement = QSharedMemory(shared_key)
    report['replacementCanAcquireLock'] = replacement.create(1)
    if replacement.isAttached():
        replacement.detach()
    report['stopRequested'] = holder['window'].stop.is_set()
    report['timerStopped'] = not holder['window'].timer.isActive()
    print(json.dumps(report, ensure_ascii=True), flush=True)


class Tests(unittest.TestCase):
    def test_application_and_tray_exit_finish_the_process_and_release_the_lock(self):
        try:
            import PySide6
        except ImportError:
            self.skipTest('Qt desktop dependencies not installed')
        for mode in ('hidden', 'restored', 'minimized', 'visible', 'update', 'application_quit'):
            with self.subTest(mode=mode):
                environment = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
                result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--probe', mode],
                                        cwd=PROJECT, env=environment, capture_output=True, text=True,
                                        encoding='utf-8', timeout=12,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout.strip().splitlines()[-1])
                self.assertFalse(report['fallbackNeeded'], report)
                self.assertEqual(report['exitCode'], 0)
                self.assertEqual(report['startedWorkers'], 2)
                self.assertEqual(report['joinCalls'], 1, report)
                for field in ('workersStopped', 'stopRequested', 'timerStopped', 'lockHeldDuringCleanup',
                              'lockHeldUntilLoopExit', 'stopRequestedAtLoopExit', 'trayHiddenAtLoopExit',
                              'lockReleasedAfterMain', 'replacementCanAcquireLock'):
                    self.assertTrue(report.get(field), (field, report))
                self.assertEqual(report['trayModes'], {'hidden': [True], 'restored': [True, False]}.get(mode, []))
                self.assertEqual(report['updateRestartCalls'], int(mode == 'update'))
                if mode in ('hidden', 'restored'):
                    for field in ('hiddenBeforeExit', 'keptWorkersWhenHidden', 'lockHeldWhenHidden',
                                  'trayPresentWhenHidden', 'waitShownWhileHidden'):
                        self.assertTrue(report.get(field), (field, report))
                if mode == 'restored':
                    for field in ('visibleWhenRestored', 'guardedUntilReleased', 'releaseSeenWhenRestored'):
                        self.assertTrue(report.get(field), (field, report))
                if mode == 'minimized':
                    self.assertTrue(report.get('taskbarMinimized'), report)


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--probe':
        probe(sys.argv[2])
    else:
        unittest.main()
