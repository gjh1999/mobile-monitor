"""Edit local project groups and background synchronization preferences."""

from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


POLICIES = ((None, '默认'), ('high', '高优先级'), ('12h', '每 12 小时'),
            ('24h', '每 24 小时'), ('on_open', '进入后同步'))


class ProjectSyncDialog(QDialog):
    def __init__(self, parent, settings, projects):
        super().__init__(parent)
        self.settings = settings
        self.projects = projects
        self.groups = {row['id']: dict(row) for row in settings.project_groups()}
        self.assignment = {project: group['id'] for group in self.groups.values() for project in group['projects']}
        self.original_policy = {project: settings.project_policy(project) for project in projects}
        self.setObjectName('lanxiDialog')
        self.setWindowTitle('项目分组与同步')
        self.resize(700, 460)
        layout = QVBoxLayout(self)
        info = QLabel('本机项目先打开。服务器同步按项目设置在后台执行。')
        info.setWordWrap(True)
        layout.addWidget(info)
        actions = QHBoxLayout()
        for label, action in (('新增分组', self.new_group), ('重命名分组', self.rename_group), ('删除分组', self.remove_group)):
            button = QPushButton(label)
            button.clicked.connect(action)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.table = QTableWidget(len(projects), 3, self)
        self.table.setHorizontalHeaderLabels(['项目', '分组', '同步频率'])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 270)
        self.table.setColumnWidth(1, 170)
        self.group_choices = {}
        self.policy_choices = {}
        for index, (project, (title, path)) in enumerate(projects.items()):
            item = QTableWidgetItem(title)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(path)
            self.table.setItem(index, 0, item)
            group = QComboBox(self.table)
            group.currentIndexChanged.connect(lambda _, key=project, box=group: self.assignment.update({key: box.currentData()}))
            self.group_choices[project] = group
            self.table.setCellWidget(index, 1, group)
            policy = QComboBox(self.table)
            for value, label in POLICIES:
                policy.addItem(label, value)
            policy.setCurrentIndex(max(policy.findData(self.original_policy[project]), 0))
            self.policy_choices[project] = policy
            self.table.setCellWidget(index, 2, policy)
        self.refresh_groups()
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def refresh_groups(self):
        for project, box in self.group_choices.items():
            previous = self.assignment.get(project)
            box.blockSignals(True)
            box.clear()
            box.addItem('未分组', None)
            for gid, group in self.groups.items():
                box.addItem(group['name'], gid)
            box.setCurrentIndex(max(box.findData(previous), 0))
            box.blockSignals(False)

    def group_name(self, original=None):
        name, ok = QInputDialog.getText(self, '项目分组', '分组名称', text=self.groups[original]['name'] if original else '')
        if not ok:
            return None
        name = ' '.join(name.split())
        if not 1 <= len(name) <= 40 or any(row['name'].casefold() == name.casefold()
                                          for key, row in self.groups.items() if key != original):
            QMessageBox.warning(self, '分组名称不可用', '请输入长度不超过 40 字、且未使用的名称。')
            return None
        return name

    def choose_group(self, title):
        if not self.groups:
            return None
        names = {row['name']: gid for gid, row in self.groups.items()}
        choice, ok = QInputDialog.getItem(self, title, '选择分组', list(names), editable=False)
        return names.get(choice) if ok else None

    def new_group(self):
        name = self.group_name()
        if name:
            gid = str(uuid4())
            self.groups[gid] = {'id': gid, 'name': name, 'projects': []}
            self.refresh_groups()

    def rename_group(self):
        gid = self.choose_group('重命名分组')
        if gid and (name := self.group_name(gid)):
            self.groups[gid]['name'] = name
            self.refresh_groups()

    def remove_group(self):
        gid = self.choose_group('删除分组')
        if gid:
            self.groups.pop(gid)
            self.assignment = {project: value for project, value in self.assignment.items() if value != gid}
            self.refresh_groups()

    def save(self):
        try:
            groups = [{**row, 'projects': [key for key, value in self.assignment.items() if value == gid]}
                      for gid, row in self.groups.items()]
            changes = {project: current for project, box in self.policy_choices.items()
                       if (current := box.currentData()) != self.original_policy[project]}
            self.settings.save_project_settings(groups, changes)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '项目设置未保存', str(error))
            return
        self.accept()
