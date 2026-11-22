from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call, patch

import monitor
from desktop_qt import MainWindow


class DesktopProjectSyncTests(TestCase):
    def test_open_existing_project_launches_locally_before_scheduling(self):
        actions = Mock()
        window = SimpleNamespace(
            has_local_project=lambda: True,
            _sync_project='owner/project',
            _project_directory=Path('local-project'),
            project_occupancy_message=Mock(return_value=None),
            request_project=actions.request_project,
            launch_project=actions.launch_project,
        )
        window.schedule_local_project = lambda: MainWindow.schedule_local_project(window)
        with patch.object(monitor, 'personal_sync', actions.flow):
            MainWindow.open_project(window)
        self.assertEqual(actions.mock_calls, [call.launch_project(Path('local-project')),
                                              call.flow.request_after_open('owner/project')])

    def test_missing_project_uses_the_existing_download_path(self):
        actions = Mock()
        window = SimpleNamespace(
            has_local_project=lambda: False,
            _sync_project='owner/project',
            request_project=actions.request_project,
        )
        MainWindow.open_project(window)
        self.assertEqual(actions.mock_calls, [call.request_project('owner/project')])
