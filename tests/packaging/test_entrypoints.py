"""Frozen dispatch/lifetime tests; no SCM registration or real Tray is launched."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def load_entry(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "packaging/pyinstaller" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServiceEntryTests(unittest.TestCase):
    def setUp(self):
        self.entry = load_entry("service_entry")
        self.manager = Mock()
        self.service = types.ModuleType("ogk_agent.service.windows_service")
        self.service.OpenGuardKidsService = object()
        self.modules = patch.dict(sys.modules, {
            "servicemanager": self.manager,
            "ogk_agent.service.windows_service": self.service,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_scm_launch_without_arguments_dispatches_service(self):
        with patch.object(sys, "argv", ["ogk_agent.exe"]):
            self.assertEqual(self.entry.main(), 0)
        self.assertEqual(self.manager.method_calls, [
            unittest.mock.call.Initialize(),
            unittest.mock.call.PrepareToHostSingle(self.service.OpenGuardKidsService),
            unittest.mock.call.StartServiceCtrlDispatcher(),
        ])

    def test_arbitrary_cli_argument_cannot_install_service(self):
        with patch.object(sys, "argv", ["ogk_agent.exe", "install"]):
            self.assertEqual(self.entry.main(), 2)
        self.assertFalse(self.manager.method_calls)


class TrayEntryTests(unittest.TestCase):
    def setUp(self):
        self.entry = load_entry("tray_entry")
        self.tray = types.ModuleType("ogk_ui.tray.main")
        self.tray._require_user_session = Mock()
        self.tray.main = Mock(return_value=0)
        modules = patch.dict(sys.modules, {"ogk_ui.tray.main": self.tray})
        modules.start()
        self.addCleanup(modules.stop)
        self.kernel = Mock()
        self.kernel.CreateMutexW.return_value = 123

    def run_entry(self, error, *args):
        with patch.object(sys, "argv", ["ogk_ui.exe", *args]), \
                patch.object(self.entry.ctypes, "WinDLL", return_value=self.kernel, create=True), \
                patch.object(self.entry.ctypes, "get_last_error", return_value=error, create=True):
            return self.entry.main()

    def test_existing_tray_does_not_create_second_ui(self):
        self.assertEqual(self.run_entry(183), 0)
        self.tray.main.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(123)

    def test_first_tray_keeps_mutex_until_ui_returns(self):
        def check_lifetime():
            self.kernel.CloseHandle.assert_not_called()
            return 7
        self.tray.main.side_effect = check_lifetime
        self.assertEqual(self.run_entry(0), 7)
        self.kernel.CloseHandle.assert_called_once_with(123)

    def test_mutex_closed_if_ui_fails(self):
        self.tray.main.side_effect = RuntimeError("synthetic failure")
        with self.assertRaises(RuntimeError):
            self.run_entry(0)
        self.kernel.CloseHandle.assert_called_once_with(123)

    def test_elevated_autostart_is_skipped(self):
        self.tray._require_user_session.side_effect = RuntimeError("elevated")
        self.assertEqual(self.run_entry(0, "--autostart"), 0)
        self.kernel.CreateMutexW.assert_not_called()
        self.tray.main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
