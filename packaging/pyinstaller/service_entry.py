"""Frozen SCM entry point: no arguments means dispatch, not CLI help."""
import sys


def main() -> int:
    import servicemanager
    from ogk_agent.service.windows_service import OpenGuardKidsService

    if sys.argv[1:] == ["--smoke-test"]:
        # Import bundled native dependencies without installing/starting anything.
        import win32timezone
        from ogk_agent.app_control import blocker
        from ogk_agent.dns import proxy
        from ogk_agent.storage import offline_queue
        from zoneinfo import ZoneInfo
        ZoneInfo("Asia/Ho_Chi_Minh")
        return 0
    if len(sys.argv) != 1:
        return 2  # Installation belongs to the elevated installer, not this EXE.
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(OpenGuardKidsService)
    servicemanager.StartServiceCtrlDispatcher()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
