"""Blue desktop workspace tokens, native fonts and readable conversation HTML."""
import re
from functools import lru_cache
from html import escape

from PySide6.QtCore import QUrl
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QTextBlockFormat, QTextCharFormat, QTextCursor,
    QTextDocument, QTextDocumentFragment, QTextFormat, QTextFrameFormat, QTextOption,
)

from codex_activity import terminal_text

PALETTE = {
    'paper': '#f3f6fb', 'surface': '#ffffff', 'ink': '#24344d',
    'heading': '#1c3151', 'muted': '#61738c', 'line': '#e1e8f2',
    'primary': '#2b63c6', 'primary_hover': '#2458b4', 'primary_pressed': '#1e4b9c',
    'soft': '#edf3ff', 'soft_ink': '#315d9b', 'soft_hover': '#e3edff',
    'soft_pressed': '#d8e6fc', 'disabled': '#f1f3f7', 'disabled_ink': '#8996a8',
    'warning': '#825719', 'warning_surface': '#fff7e9',
}
# Prefer a native family with explicit Regular/Bold faces. Some locally installed
# Noto variants resolve to a light face when Qt applies stylesheet font weights.
FONT_CANDIDATES = ('Microsoft YaHei UI', 'Microsoft YaHei', 'Noto Sans SC', 'DengXian')
LATIN_FONTS = ('Segoe UI', 'Arial')


@lru_cache(maxsize=1)
def font_families():
    available = set(QFontDatabase.families())
    chinese = next((name for name in FONT_CANDIDATES if name in available), 'Sans Serif')
    latin = next((name for name in LATIN_FONTS if name in available), 'Arial')
    return chinese, latin


def ui_font(size=10, strong=False):
    chinese, latin = font_families()
    font = QFontDatabase.font(chinese, 'Bold' if strong else 'Regular', round(size))
    font.setFamilies([chinese, latin])
    font.setPointSizeF(float(size))
    font.setWeight(QFont.Weight.Bold if strong else QFont.Weight.Normal)
    font.setStyleName('Bold' if strong else 'Regular')
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def configure_text(widget, size=10, inset=10):
    font = ui_font(size)
    widget.setFont(font)
    widget.document().setDefaultFont(font)
    widget.setCurrentFont(font)
    widget.document().setDocumentMargin(inset)
    widget.document().setDefaultStyleSheet('p { margin-top:3px; margin-bottom:8px; }')
    widget.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    block = QTextBlockFormat()
    block.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
    widget.textCursor().mergeBlockFormat(block)


def set_reading_html(widget, html):
    widget.setHtml(html)
    format_reading_document(widget.document())


def format_reading_document(document):
    block = document.begin()
    while block.isValid():
        cursor = QTextCursor(block)
        fmt = block.blockFormat()
        table = cursor.currentTable()
        if table is not None:
            cell_background = table.cellAt(cursor).format().background()
            # HTML import also assigns the cell color to its text runs.
            if fmt.background() == cell_background:
                fmt.clearBackground()
            runs = []
            fragment = block.begin()
            while not fragment.atEnd():
                item = fragment.fragment()
                char_format = item.charFormat()
                if char_format.hasProperty(char_format.Property.BackgroundBrush) and char_format.background() == cell_background:
                    char_format.clearBackground()
                    runs.append((item.position(), item.length(), char_format))
                fragment += 1
            for position, length, char_format in runs:
                selection = QTextCursor(document)
                selection.setPosition(position)
                selection.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
                selection.setCharFormat(char_format)
        if not fmt.hasProperty(QTextFormat.Property.LineHeight):
            fmt.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setBlockFormat(fmt)
        block = block.next()


def _inline(text):
    content = escape(text)
    content = re.sub(r'`([^`\n]+)`', r'<span style="background:#edf2fa;color:#284e82;font-family:Consolas;">\1</span>', content)
    content = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', content)
    return content.replace('\n', '<br>')


def web_link(url):
    return url.isValid() and url.scheme() in {'https', 'http'} and bool(url.host())


def insert_markdown(cursor, text):
    document = QTextDocument()
    document.setLayoutEnabled(False)
    document.setUndoRedoEnabled(False)
    document.setDefaultFont(cursor.document().defaultFont())
    features = QTextDocument.MarkdownFeature
    document.setMarkdown(str(text or ''), features.MarkdownDialectGitHub | features.MarkdownNoHTML)
    if document.isEmpty():
        return

    for table in document.rootFrame().childFrames():
        fmt = table.format().toTableFormat()
        fmt.setBorder(1)
        fmt.setBorderBrush(QColor('#bcd9e9'))
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
        fmt.setCellPadding(6)
        fmt.setBorderCollapse(True)
        table.setFormat(fmt)
        for column in range(table.columns()):
            cell = table.cellAt(0, column)
            header = cell.format()
            header.setBackground(QColor('#dfedf6'))
            cell.setFormat(header)

    image_replacements = []
    previous_code = False
    block = document.begin()
    while block.isValid():
        line = QTextCursor(block)
        fmt = block.blockFormat()
        heading = fmt.headingLevel()
        code = fmt.nonBreakableLines()
        quote = fmt.intProperty(QTextFormat.Property.BlockQuoteLevel)
        if heading:
            fmt.setTopMargin(9)
            fmt.setBottomMargin(4)
        elif code:
            fmt.setNonBreakableLines(False)
            fmt.setBackground(QColor('#e4eff7'))
            fmt.setLeftMargin(8)
            fmt.setRightMargin(8)
            fmt.setTopMargin(0 if previous_code else 6)
            following = block.next()
            fmt.setBottomMargin(0 if following.isValid() and following.blockFormat().nonBreakableLines() else 6)
            fmt.setLineHeight(100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        elif quote:
            fmt.setBackground(QColor('#eaf4fa'))
            fmt.setLeftMargin(12 + 10 * (quote - 1))
            fmt.setRightMargin(8)
        elif line.currentTable() is None:
            fmt.setTopMargin(3)
            fmt.setBottomMargin(6)
        line.setBlockFormat(fmt)

        changes = []
        for item in block:
            run = item.fragment()
            char = run.charFormat()
            if char.isImageFormat():
                alt = char.stringProperty(QTextFormat.Property.ImageAltText)
                image_replacements.append((run.position(), run.length(), alt or '图片'))
                continue
            if char.isAnchor():
                if web_link(QUrl(char.anchorHref())):
                    char.setForeground(QColor('#215b86'))
                    char.setFontUnderline(True)
                else:
                    char.setAnchor(False)
                    char.setAnchorHref('')
                    char.clearForeground()
            if char.fontFamilies() == ['monospace']:
                char.setFontFamilies(['Cascadia Mono', 'Consolas', 'monospace'])
                char.setForeground(QColor('#194b66'))
                char.setFontPointSize(9)
                if not code:
                    char.setBackground(QColor('#dfedf6'))
            elif heading:
                char.clearProperty(QTextFormat.Property.FontSizeAdjustment)
                char.setFontPointSize({1: 16, 2: 14}.get(heading, 12))
                char.setFontWeight(QFont.Weight.Bold)
                char.setForeground(QColor(PALETTE['heading']))
            elif quote and not char.isAnchor():
                char.setForeground(QColor('#365b70'))
            if char != run.charFormat():
                changes.append((run.position(), run.length(), char))
        for position, length, char in changes:
            line.setPosition(position)
            line.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            line.setCharFormat(char)
        previous_code = code
        block = block.next()

    for position, length, alt in reversed(image_replacements):
        image = QTextCursor(document)
        image.setPosition(position)
        image.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        image.insertText(alt, QTextCharFormat())
    cursor.insertBlock(document.begin().blockFormat(), QTextCharFormat())
    # Start with its own block so list structure and the author label stay separate.
    cursor.insertFragment(QTextDocumentFragment(document))


def message_html(role, text, assistant):
    blocks = []
    for index, part in enumerate(re.split(r'```[^\n]*\n([\s\S]*?)```', str(text or ''))):
        if not part:
            continue
        if index % 2:
            content = escape(part.rstrip('\n')).replace('\n', '<br>').replace(' ', '&nbsp;')
            blocks.append('<table width="100%" cellspacing="0" cellpadding="10" bgcolor="#f0f4fa">'
                          '<tr><td style="color:#294667;font-family:Consolas;font-size:9pt;">' + content + '</td></tr></table>')
        else:
            blocks.extend('<p style="color:#24344d;font-size:10pt;">' + _inline(paragraph) + '</p>'
                          for paragraph in part.strip().split('\n\n') if paragraph)
    title = ('<p style="margin-top:0;margin-bottom:7px;font-size:9pt;color:#61738c;">'
             f'<b>{escape(role)}</b></p>')
    return bubble_html(title + ''.join(blocks), assistant)


def bubble_html(content, left, width=93):
    spacer = f'<td width="{100 - width}%"></td>'
    bubble = f'<td width="{width}%" style="padding:10px;">{content}</td>'
    return ('<table width="100%" cellspacing="0" cellpadding="0"><tr>' +
            (bubble + spacer if left else spacer + bubble) +
            '</tr></table><p style="margin:0;font-size:5pt;">&nbsp;</p>')


def process_html(item):
    status = item['status']
    exit_code = item['exitCode']
    failed = status in ('failed', 'declined') or (exit_code is not None and exit_code != 0)
    caption = {'inProgress': '运行中', 'completed': '已完成', 'failed': '未完成',
               'declined': '已取消', 'unknown': '执行状态待确认'}[status]
    if status == 'completed' and failed:
        caption = '未完成'
    if failed and exit_code is not None:
        caption += f' · 退出码 {exit_code}'
    if item['durationMs'] is not None:
        caption += f" · {item['durationMs'] / 1000:.2f} 秒"
    state_color = '#854d28' if failed else '#21616f'
    blocks = [f'<p style="margin-top:0;margin-bottom:5px;font-size:9pt;color:{state_color};">'
              f'<b>执行札记 · {caption}</b></p>']
    command = terminal_text(item['command']).strip()
    if command:
        blocks.append('<p style="font-family:Consolas;font-size:9pt;color:#173d54;white-space:pre-wrap;'
                      'margin:0;"><b>› ' + escape(command).replace('\n', '<br/>') + '</b></p>')
    output = terminal_text(item['text']).strip('\n').split('\n')
    if item['truncated'] or len(output) > 30:
        blocks.append('<p style="font-size:8pt;color:#415d70;margin:4px 0 0;">前面的输出已省略</p>')
    if any(output):
        blocks.append('<p style="font-family:Consolas;font-size:9pt;color:#29495e;white-space:pre-wrap;'
                      'margin:4px 0 0;">' + '<br/>'.join(escape(line) for line in output[-30:]) + '</p>')
    return bubble_html(''.join(blocks), True, width=89)


def warning_html(text):
    return ('<table width="100%" cellspacing="0" cellpadding="12" bgcolor="#fff7e9">'
            '<tr><td><p style="font-size:9pt;color:#825719;"><b>! 执行未完成</b></p>'
            '<p style="font-size:10pt;color:#665437;">' + escape(str(text)).replace('\n', '<br>') +
            '</p></td></tr></table>')


STYLESHEET = '''
QMainWindow { background:#25465e; color:#24344d; }
QFrame#statDivider { background:rgba(183, 222, 245, 59); }
QFrame#composerBox { background:rgba(225, 247, 255, 80); border:1px solid rgba(226, 248, 255, 160); border-radius:11px; }
QFrame#divider { background:rgba(230, 249, 255, 118); border:0; max-height:1px; }
QLabel { color:#24344d; background:transparent; }
QLabel#status[connected="true"] { color:#154e41; }
QLineEdit { background:rgba(234, 249, 255, 188); color:#15364d; border:1px solid rgba(234, 250, 255, 182); border-radius:8px; padding:5px 9px; min-height:22px; }
QLineEdit:focus { border-color:#327fb2; }
QTreeWidget#taskList { background:transparent; border:0; color:#16384d; outline:0; show-decoration-selected:0; }
QTreeWidget#taskList::item { border:1px solid transparent; border-radius:9px; padding:6px 7px; }
QTreeWidget#taskList::item:hover { background:rgba(226, 246, 255, 100); }
QTreeWidget#taskList::item:selected { background:rgba(212, 238, 255, 165); color:#113951; }
QTextEdit, QTextBrowser { color:#24344d; background:transparent; border:0; padding:0; selection-background-color:#d9e6fc; selection-color:#1c3151; }
QTextEdit#composer { background:transparent; border:0; }
QTextEdit#projectIntroEditor { background:rgba(234, 249, 255, 182); border:1px solid rgba(234, 250, 255, 175); border-radius:8px; }
QTextEdit#projectIntroEditor:focus { border-color:#327fb2; }
QTextBrowser#conversation, QTextBrowser#infoText { background:transparent; border:0; }
QPushButton { background:#ffffff; color:#425a7b; border:1px solid #dae3f0; border-radius:8px; padding:7px 11px; }
QPushButton:hover { background:#f2f6fe; border-color:#a9c1e7; color:#28599f; }
QPushButton:pressed { background:#e3edff; border-color:#83a7dd; }
QPushButton:focus { border-color:#7399db; }
QPushButton#hoverAction { background:rgba(225, 247, 255, 154); color:#153b53; border-color:rgba(226, 245, 255, 138); padding:5px 8px; }
QPushButton#hoverAction:hover, QPushButton#hoverAction:focus { background:#d4efff; border-color:#70a8cc; }
QPushButton#hoverAction:pressed { background:#acd8f1; }
QPushButton#edgeMenuItem { background:transparent; border:0; border-radius:5px; color:#f3fbff; padding:0 8px; }
QPushButton#edgeMenuItem:hover { background:rgba(220,247,255,34); }
QPushButton#edgeMenuItem:pressed { background:rgba(220,247,255,54); }
QPushButton#edgeMenuItem:disabled { background:transparent; color:#b1ccd8; }
QPushButton#edgeMenuItem::menu-indicator { image:none; width:0; }
QPushButton#send, QPushButton#approvalAllow { background:#096cb9; color:white; border-color:#096cb9; }
QPushButton#send { padding:0; border-radius:17px; font-size:18px; }
QPushButton#send:hover, QPushButton#approvalAllow:hover { background:#055eaa; border-color:#055eaa; }
QPushButton#send:pressed, QPushButton#approvalAllow:pressed { background:#044e90; }
QPushButton:disabled, QPushButton#send:disabled, QPushButton#hoverAction:disabled, QPushButton#approvalAllow:disabled { background:rgba(234, 246, 252, 97); color:#507087; border-color:rgba(225, 245, 255, 80); }
QDialog#lanxiDialog, QMessageBox {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #eaf7fc,stop:1 #cee5f1);
    color:#24344d;
}
QDialog#lanxiDialog QLineEdit {
    background:rgba(249,253,255,218);
    border:1px solid #bbd8e8;
}
QDialog#lanxiDialog QLineEdit:focus { border-color:#327fb2; }
QDialog#lanxiDialog QLabel#dialogHint { color:#476477; }
QListWidget#wallpaperList { background:#e9f5fb; border:1px solid #bed9e9; border-radius:8px; padding:4px; }
QListWidget#wallpaperList::item { padding:7px 8px; border-radius:5px; color:#254459; }
QListWidget#wallpaperList::item:selected { background:#b8dcec; color:#143b54; }
QLabel#wallpaperPreview { background:#193b52; color:#e1f1fa; border-radius:9px; }
QTextBrowser#approvalDetails { background:#e6f2fb; border:1px solid #bfd9ea; border-radius:8px; }
QLabel#approvalSummary, QLabel#approvalScope { color:#f1faff; }
QLabel#approvalMeta { color:#24495f; }
QTreeWidget#approvalQueue { background:transparent; border:0; color:#254459; }
QTreeWidget#approvalQueue::item { padding:7px 5px; border-bottom:1px solid rgba(73,133,158,40); }
QTreeWidget#approvalQueue::item:selected { color:#15394c; background:rgba(157, 212, 233, 195); }
QTreeWidget#approvalQueue::indicator { width:13px; height:13px; border-radius:3px; border:1px solid #6a94ad; }
QTreeWidget#approvalQueue::indicator:unchecked { background:#e8f5fc; }
QTreeWidget#approvalQueue::indicator:checked { background:#096cb9; border-color:#096cb9; }
QTreeWidget#approvalQueue QHeaderView::section { color:#23495b; background:rgba(228,246,252,130); border:0; padding:6px 5px; }
QTreeWidget#approvalQueue QHeaderView { background:transparent; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:transparent; width:7px; margin:3px 0; }
QScrollBar::handle:vertical { background:#d0dae9; border-radius:3px; min-height:26px; }
QScrollBar::handle:vertical:hover { background:#afc0d8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QMenu { background:#eaf6fc; color:#254459; border:1px solid #c5dce9; border-radius:8px; padding:5px; }
QMenu::item { padding:8px 20px; border-radius:5px; }
QMenu::item:selected { background:#edf3ff; color:#275db5; }
QToolTip { background:#1c3151; color:#ffffff; border:0; padding:7px; }
QFrame#notebookDivider { background:rgba(210, 237, 250, 45); border:0; }
QPushButton#notebookIcon { background:transparent; border:1px solid transparent; border-radius:8px; color:#e3f3fa; padding:0; }
QPushButton#notebookIcon:hover { background:rgba(207, 234, 249, 28); color:#f3faff; }
QPushButton#notebookIcon:pressed { background:rgba(207, 234, 249, 45); }
QPushButton#notebookIcon:focus { border-color:rgba(201, 232, 248, 145); }
QPushButton#notebookIcon:disabled { color:#7799a8; }
QListWidget#notebookList { background:transparent; border:0; outline:0; }
QListWidget#notebookList QScrollBar::handle:vertical { background:rgba(201, 232, 248, 115); }
QTextEdit#notebookBody { color:#f3faff; selection-background-color:#38738f; selection-color:#fff; }
QLabel#notebookHeading { color:#f3faff; }
QLabel#notebookMuted { color:#a9c8d7; }
QMenu#notebookMenu { background:#184359; border-color:#547b8d; color:#f3faff; }
QMenu#notebookMenu::item:selected { background:#34657a; color:#f3faff; }
QMenu#notebookMenu::item:disabled { color:#93afbf; }
'''

SLASH_MENU_STYLE = '''
QListView { background:#f0f9ff; border:1px solid #bedbe9; border-radius:8px; color:#173b52; outline:0; padding:4px; }
QListView::item { padding:6px 9px; border-radius:5px; }
QListView::item:selected { background:#dbedf9; color:#153951; }
'''
