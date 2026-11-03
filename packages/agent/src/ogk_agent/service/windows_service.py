"""Visible SCM service scaffold. Never installs/starts itself on import.

Install from a protected deployment directory, as an Administrator. The initial
runtime exposes the unpaired IPC state only. Enrollment, signed-policy loading,
the interactive lock helper and per-user DNS routing are integration boundaries.
"""

from __future__ import annotations

import os
import sys
import threading

from ogk_agent.ipc.server import ClientIdentity, NamedPipeServer
from ogk_contracts.ipc import Message

if os.name == "nt":
    import servicemanager
    import win32api
    import win32con
    import win32security
    import win32service
    import win32serviceutil
    ServiceBase = win32serviceutil.ServiceFramework
else:
    ServiceBase = object


SERVICE_NAME = "OpenGuardKidsAgent"


def require_system() -> None:
    if os.name != "nt":
        raise OSError("OpenGuard Kids Service requires Windows")
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        if win32security.ConvertSidToStringSid(sid) != "S-1-5-18":
            raise PermissionError("Service must run as LocalSystem")
    finally:
        token.Close()


def configure_recovery() -> None:
    """SCM auto-start and restart after failures, never after an ordinary stop."""
    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        service = win32service.OpenService(manager, SERVICE_NAME,
                                          win32service.SERVICE_CHANGE_CONFIG | win32service.SERVICE_START)
        try:
            win32service.ChangeServiceConfig(
                service, win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_AUTO_START,
                win32service.SERVICE_NO_CHANGE, None, None, 0, None, "LocalSystem", None, None,
            )
            win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS, {
                "ResetPeriod": 86400, "RebootMsg": None, "Command": None,
                "Actions": ((win32service.SC_ACTION_RESTART, 5000),
                            (win32service.SC_ACTION_RESTART, 15000),
                            (win32service.SC_ACTION_RESTART, 60000)),
            })
            win32service.ChangeServiceConfig2(
                service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True)
        finally:
            win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(manager)


class AgentRuntime:
    """Replace/extend server-side hooks after trusted enrollment integration.

    A Tray message cannot create a binding, set policy, approve extra time or
    invoke privileged code. Active components must recheck their SID/session
    presence gate immediately before each action, and detach owned routes when
    that gate closes. No counter/blocker/proxy is started for an unpaired device.
    """
    def __init__(self) -> None:
        self.pipe = NamedPipeServer(self.handle)

    def handle(self, identity: ClientIdentity, message: Message) -> Message | None:
        if message.type == "status.get":
            return Message("status", {"managed": False}, message.request_id)
        if message.type == "time.request":
            return Message("error", {"code": "NOT_ENROLLED"}, message.request_id)
        return None

    def start(self) -> None:
        self.pipe.start()

    def tick(self) -> None:
        self.pipe.check_health()

    def close(self) -> None:
        self.pipe.close()


class OpenGuardKidsService(ServiceBase):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "OpenGuard Kids Agent"
    _svc_description_ = "OpenGuard Kids parental controls; visible User Tray required for enforcement."
    _svc_reg_class_ = "ogk_agent.service.windows_service.OpenGuardKidsService"
    runtime_factory = AgentRuntime

    def __init__(self, args) -> None:
        super().__init__(args)
        self._stop = threading.Event()

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=15000)
        self._stop.set()

    def SvcShutdown(self) -> None:
        self.SvcStop()

    def SvcDoRun(self) -> None:
        runtime = None
        failed = False
        try:
            require_system()
            runtime = type(self).runtime_factory()
            runtime.start()
            servicemanager.LogInfoMsg("OpenGuard Kids Agent started; waiting for managed bindings.")
            while not self._stop.wait(1):
                runtime.tick()  # A shorter loop also bounds the five-second Tray lease.
        except Exception as error:
            failed = True
            servicemanager.LogErrorMsg("OpenGuard Kids Agent failed: " + type(error).__name__)
        finally:
            if runtime is not None:
                try:
                    runtime.close()
                except Exception as error:
                    failed = True
                    servicemanager.LogErrorMsg("OpenGuard Kids cleanup failed: " + type(error).__name__)
        if failed:
            # pywin32's C framework reports a nonzero stopped status when SvcRun
            # raises. A normal return would hide the failure from SCM recovery.
            raise RuntimeError("OpenGuard Kids Agent runtime failed")
        else:
            servicemanager.LogInfoMsg("OpenGuard Kids Agent stopped.")


def main() -> None:
    if os.name != "nt":
        raise OSError("Windows Service requires Windows")
    # The pywin32 helper handles install/update/start/stop/remove with SCM.
    # Installation defaults to LocalSystem; configuration enforces it explicitly.
    win32serviceutil.HandleCommandLine(
        OpenGuardKidsService, serviceClassString=OpenGuardKidsService._svc_reg_class_)
    if any(argument in {"install", "update"} for argument in sys.argv[1:]):
        configure_recovery()


if __name__ == "__main__":
    main()
