import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_intro import ProjectIntroStore

try:
    from PySide6.QtCore import QTimer, Qt, QUrl
    from PySide6.QtGui import QInputMethodEvent, QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from desktop_project_intro import ProjectIntroPage
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Qt desktop dependencies are unavailable')
class ProjectIntroPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = ProjectIntroStore(self.root)
        self.page = ProjectIntroPage(self.store)
        self.page.resize(600, 450)
        self.page.show()
        self.addCleanup(self.page.close)
        self.page.show_project('first', '第一项目', str(self.root))

    def edit(self, field, value):
        QTest.mouseDClick(field.preview, Qt.MouseButton.LeftButton)
        self.assertIs(field.currentWidget(), field.editor)
        field.editor.setPlainText(value)

    def test_keyboard_save_restores_preview_and_reopens_multiline_value(self):
        field = self.page.purpose
        self.assertIs(field.currentWidget(), field.reading)
        self.assertEqual(field.preview.text(), '双击添加')
        self.edit(field, '第一行')
        field.editor.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClick(field.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        field.editor.insertPlainText('第二行')
        QTest.keyClick(field.editor, Qt.Key.Key_Return)
        self.assertIs(field.currentWidget(), field.reading)
        self.assertFalse(field.editor.isVisible())
        self.assertEqual(field.preview.text(), '第一行\n第二行')
        reopened = ProjectIntroPage(self.store)
        self.addCleanup(reopened.close)
        reopened.show_project('first', '第一项目', str(self.root))
        self.assertEqual(reopened.purpose.preview.text(), '第一行\n第二行')
        self.assertFalse(reopened.purpose.editor.isVisible())

    def test_switch_preserves_draft_and_write_failure_keeps_editor(self):
        self.edit(self.page.progress, '项目一草稿')
        self.page.show_project('second', '第二项目', str(self.root))
        self.edit(self.page.progress, '项目二草稿')
        QTest.keyClick(self.page.progress.editor, Qt.Key.Key_Escape)
        self.assertEqual(self.store.load('second'), ('', ''))
        self.page.show_project('first', '第一项目', str(self.root), branch='main')
        field = self.page.progress
        self.assertIs(field.currentWidget(), field.editor)
        self.assertEqual(field.editor.toPlainText(), '项目一草稿')
        self.assertEqual(self.store.load('first'), ('', ''))
        with patch.object(self.store, 'save', side_effect=OSError('临时写入失败')):
            QTest.keyClick(field.editor, Qt.Key.Key_Return)
        self.assertEqual(self.page.status.text(), '临时写入失败')
        self.assertIs(field.currentWidget(), field.editor)
        self.assertEqual(field.editor.toPlainText(), '项目一草稿')
        QTest.keyClick(field.editor, Qt.Key.Key_Return)
        self.assertEqual(self.store.load('first'), ('', '项目一草稿'))

    def test_cached_metadata_and_open_folder(self):
        self.page.show_project('first', '第一项目', str(self.root), repository='owner/repo', branch='main')
        self.assertEqual(self.page.repository.text(), 'Git 仓库 · owner/repo')
        self.assertEqual(self.page.branch.text(), '当前分支 · main')
        with patch('desktop_project_intro.QDesktopServices.openUrl', return_value=True) as open_url:
            self.page.open_button.click()
        open_url.assert_called_once_with(QUrl.fromLocalFile(str(self.root)))
        self.page.show_project('second', '第二项目', str(self.root))
        self.assertTrue(self.page.repository.isHidden())
        self.assertTrue(self.page.branch.isHidden())

    def test_exit_can_cancel_or_save_drafts_for_multiple_projects(self):
        self.edit(self.page.purpose, '第一项目草稿')
        self.page.show_project('second', '第二项目', str(self.root))
        self.edit(self.page.progress, '第二项目草稿')

        def choose(text):
            box = QApplication.activeModalWidget()
            button = next(item for item in box.buttons() if item.text() == text)
            button.click()

        QTimer.singleShot(0, lambda: choose('取消'))
        self.assertFalse(self.page.confirm_exit())
        self.assertEqual(self.store.load('first'), ('', ''))
        QTimer.singleShot(0, lambda: choose('保存并退出'))
        self.assertTrue(self.page.confirm_exit())
        self.assertEqual(self.store.load('first'), ('第一项目草稿', ''))
        self.assertEqual(self.store.load('second'), ('', '第二项目草稿'))

    def test_ime_preedit_does_not_save_and_long_preview_can_scroll(self):
        field = self.page.purpose
        self.edit(field, '用途')
        QApplication.sendEvent(field.editor, QInputMethodEvent('候选字', []))
        QTest.keyClick(field.editor, Qt.Key.Key_Return)
        self.assertIs(field.currentWidget(), field.editor)
        self.assertEqual(self.store.load('first'), ('', ''))
        QApplication.sendEvent(field.editor, QInputMethodEvent())
        field.editor.setPlainText('\n'.join(f'进度第 {index} 行' for index in range(100)))
        QTest.keyClick(field.editor, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertTrue(field.preview.isVisible())
        self.assertGreater(field.reading.verticalScrollBar().maximum(), 0)
