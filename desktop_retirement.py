"""Preview and explicitly confirm deletion of one summarized Codex session."""

import threading
from concurrent.futures import Future

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout

from session_retirement import publish_retirement, summarize


class RetirementDialog(QDialog):
    def __init__(self, parent, flow, tid, title):
        super().__init__(parent)
        self.flow, self.tid = flow, tid
        self.stop = threading.Event()
        self.job = None
        self.version = None
        self.setObjectName('lanxiDialog')
        self.setWindowTitle('压缩并删除会话')
        self.resize(620, 460)
        layout = QVBoxLayout(self)
        caption = QLabel(title or 'Codex 会话')
        caption.setTextFormat(Qt.TextFormat.PlainText)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.status = QLabel('Codex 将根据这条会话生成 Markdown 摘要。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText('生成后可在此核对和编辑摘要')
        layout.addWidget(self.editor, 1)
        self.generate = QPushButton('生成摘要')
        self.generate.clicked.connect(self.start_summary)
        layout.addWidget(self.generate)
        self.commit = QPushButton('保存摘要并删除…')
        self.commit.setEnabled(False)
        self.commit.clicked.connect(self.confirm)
        layout.addWidget(self.commit)
        self.open_document = QPushButton('打开 Markdown 摘要')
        self.open_document.hide()
        layout.addWidget(self.open_document)
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.timer = QTimer(self, interval=300)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def done(self, code):
        self.stop.set()
        super().done(code)

    def launch(self, function, finishing):
        self.generate.setEnabled(False)
        self.commit.setEnabled(False)
        self.editor.setReadOnly(True)
        future = Future()
        self.job = future, finishing

        def run():
            try:
                future.set_result(function())
            except Exception as error:
                future.set_exception(error)

        threading.Thread(target=run, name='codex-retirement', daemon=True).start()

    def start_summary(self):
        self.version = None
        self.status.setText('正在读取原会话并生成摘要…')
        home, tid, stop = self.flow.home, self.tid, self.stop
        self.launch(lambda: summarize(home, tid, stop), self.show_summary)

    def show_summary(self, result):
        text, self.version = result
        self.editor.setPlainText(text)
        self.status.setText('请核对摘要。保存并删除后，配对电脑会在联网时处理原会话。')

    def confirm(self):
        markdown = self.editor.toPlainText()
        if not markdown.strip():
            self.status.setText('摘要不能为空。')
            return
        action = QMessageBox.question(
            self, '确认永久删除原对话',
            '保存并核验这份摘要后，永久删除本机、个人协调服务器和配对电脑上的原对话？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if action != QMessageBox.StandardButton.Yes:
            return
        self.status.setText('正在保存摘要并提交删除…')
        flow, tid, version = self.flow, self.tid, self.version
        self.launch(lambda: publish_retirement(flow, tid, markdown, version), self.published)

    def published(self, path):
        self.version = None
        self.status.setText(f'摘要已保存至 {path}。服务器已接受，等待设备回执。')
        self.generate.hide()
        self.commit.hide()
        self.open_document.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        self.open_document.show()

    def refresh(self):
        if self.job and self.job[0].done():
            future, finishing = self.job
            self.job = None
            self.generate.setEnabled(True)
            self.editor.setReadOnly(False)
            try:
                finishing(future.result())
            except Exception as error:
                self.status.setText(getattr(error, 'detail', '') or str(error) or '操作暂未完成。')
            self.commit.setEnabled(self.version is not None)
