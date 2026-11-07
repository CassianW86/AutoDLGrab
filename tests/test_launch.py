import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import app
import launch


class LaunchTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_service_opens_without_starting_worker(self):
        with patch.object(launch, "dashboard_running", return_value=True), \
                patch.object(launch.webbrowser, "open") as opened, \
                patch.object(launch.uvicorn, "Server") as server:
            await launch.main()
        opened.assert_called_once_with(launch.URL)
        server.assert_not_called()

    async def test_concurrent_start_opens_existing_service(self):
        listener = Mock()
        listener.bind.side_effect = OSError("in use")
        sock = Mock()
        sock.__enter__ = Mock(return_value=listener)
        sock.__exit__ = Mock(return_value=False)
        with patch.object(launch, "dashboard_running", side_effect=[False, True]), \
                patch.object(launch.socket, "socket", return_value=sock), \
                patch.object(launch.webbrowser, "open") as opened, \
                patch.object(launch.uvicorn, "Server") as server:
            await launch.main()
        opened.assert_called_once_with(launch.URL)
        server.assert_not_called()

    async def test_startup_exit_is_a_normal_task_exception(self):
        server = Mock()
        server.serve = AsyncMock(side_effect=SystemExit(3))
        with self.assertRaisesRegex(RuntimeError, "Service startup failed"):
            await launch.serve(server, Mock())

    async def test_database_lock_still_prevents_second_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            guard = sqlite3.connect(data / "worker.lock.db")
            guard.execute("BEGIN EXCLUSIVE")
            try:
                with patch.object(app, "DATA", data):
                    with self.assertRaisesRegex(RuntimeError, "Another AutoDL Grab process"):
                        async with app.lifespan(app.app):
                            self.fail("A second worker must not start")
            finally:
                guard.close()


class ProbeTest(unittest.TestCase):
    def test_probe_rejects_unrelated_service(self):
        for data, expected in [({}, False), ([], False),
                               ({"phase":"idle", "attempts":0, "browser_mode":"dedicated"}, True)]:
            opener = Mock()
            opener.open.return_value = BytesIO(json.dumps(data).encode())
            with patch.object(launch, "build_opener", return_value=opener):
                self.assertEqual(launch.dashboard_running(), expected)
