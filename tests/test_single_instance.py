"""A second desktop launch must not create competing background workers."""
import sys
import unittest
import uuid
from unittest.mock import patch

try:
 from PySide6.QtCore import QSharedMemory
 import desktop_qt
except ImportError:
 desktop_qt = None


@unittest.skipUnless(sys.platform == 'win32' and desktop_qt is not None, 'Windows Qt desktop required')
class Tests(unittest.TestCase):
 def test_second_desktop_launch_does_not_start_another_workspace(self):
  key = 'CodexMonitorTest.' + uuid.uuid4().hex
  # Keep the real shared-memory lock. Only the window/event loop are replaced so
  # this check cannot start CLI processes or present an interactive message box.
  with patch.object(desktop_qt, 'DesktopApplication') as application, \
       patch.object(desktop_qt, 'QSharedMemory', side_effect=lambda _name: QSharedMemory(key)), \
       patch.object(desktop_qt, 'MainWindow') as window, \
       patch.object(desktop_qt.QMessageBox, 'information') as notice, \
       patch('ctypes.windll.user32.FindWindowW', return_value=0):
   def first_event_loop():
    first_lock = window.call_args.args[0]
    self.assertTrue(first_lock.isAttached())
    self.assertEqual(desktop_qt.main(), 1)
    window.assert_called_once()
    notice.assert_called_once()
    self.assertTrue(first_lock.isAttached(), 'Keep ownership until the first event loop exits')
    return 0
   application.return_value.exec.side_effect = first_event_loop
   self.assertEqual(desktop_qt.main(), 0)
   self.assertFalse(window.call_args.args[0].isAttached())
   application.return_value.exec.side_effect = None
   application.return_value.exec.return_value = 0
   self.assertEqual(desktop_qt.main(), 0)
   self.assertEqual(window.call_count, 2)
   self.assertFalse(window.call_args.args[0].isAttached())


if __name__ == '__main__':unittest.main()
