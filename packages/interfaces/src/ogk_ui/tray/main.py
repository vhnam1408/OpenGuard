"""Interactive User-session entry point: python -m ogk_ui.tray.main."""

from __future__ import annotations

import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from queue import Empty, Full, Queue
import threading
import time

from ogk_contracts.ipc import Message, ProtocolError
from .ipc_client import DisconnectedError, NamedPipeClient

LOG = logging.getLogger(__name__)


def _text(payload: dict, name: str, limit: int = 512) -> str:
    value = payload.get(name)
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 and c != "\n" for c in value)):
        raise ProtocolError("Missing or invalid explanation")
    return value


def validate_payload(message: Message) -> None:
    """Reject malformed state before it can be presented as trusted status."""
    payload = message.payload
    if message.type == "status":
        if type(payload.get("managed")) is not bool:
            raise ProtocolError("Invalid managed state")
        if not payload["managed"]:
            return
    if message.type in {"status", "quota_warning"}:
        value = payload.get("remaining_seconds")
        if type(value) is not int or not 0 <= value <= 7 * 24 * 3600:
            raise ProtocolError("Invalid remaining time")
    if message.type in {"status", "quota_warning", "blocked_app", "blocked_domain", "locked"}:
        for name in ("rule_set_by", "reason", "policy_id"):
            _text(payload, name)
        if "subject" in payload:
            _text(payload, "subject", 253)
    elif message.type == "time.request.result":
        if payload.get("status") not in {"pending", "approved", "rejected"}:
            raise ProtocolError("Invalid request status")
        if payload["status"] in {"approved", "rejected"}:
            _text(payload, "rule_set_by")
            _text(payload, "reason")
    elif message.type == "error":
        _text(payload, "code", 64)


class TrayApplication:
    def __init__(self, root, pystray, image) -> None:
        from .dialogs import Dialogs

        self.root = root
        self._events: Queue[tuple[str, object]] = Queue(maxsize=256)
        self._overflow = threading.Event()
        self._closing = False
        self._failed = False
        self._ready = False
        self._online = False
        self._last_presence = 0.0
        self._started = time.monotonic()
        self._pending: dict[str, tuple[str, float]] = {}
        self._status: dict | None = None
        self.dialogs = Dialogs(root, self.ask_more_time)
        self.client = NamedPipeClient(
            lambda msg: self._post("message", msg),
            lambda online: self._post("connection", online),
        )
        self.icon = pystray.Icon(
            "OpenGuardKids", image, "OpenGuard Kids — đang kết nối",
            menu=pystray.Menu(
                pystray.MenuItem("Thời gian còn lại", lambda icon, item: self._post("remaining"), default=True),
                pystray.MenuItem("Xin thêm giờ", lambda icon, item: self._post("request")),
            ),
        )
        self._tray_thread = threading.Thread(target=self._run_tray, name="ogk-tray", daemon=True)
        root.report_callback_exception = self._tk_error

    def _post(self, kind: str, payload: object = None) -> None:
        try:
            self._events.put_nowait((kind, payload))
        except Full:
            self._overflow.set()

    def _setup(self, icon) -> None:
        try:
            icon.visible = True
            if not icon.visible:
                raise RuntimeError("Tray is unavailable")
            self._post("ready")
        except Exception as error:
            LOG.error("Tray setup failed: %s", type(error).__name__)
            self._post("fatal")

    def _run_tray(self) -> None:
        try:
            self.icon.run(setup=self._setup)
        except Exception as error:
            LOG.error("Tray loop failed: %s", type(error).__name__)
        finally:
            self._post("fatal")

    def run(self) -> None:
        self._tray_thread.start()
        self.root.after(100, self._tick)
        try:
            self.root.mainloop()
        finally:
            self.shutdown()

    def _tk_error(self, exception_type, value, traceback) -> None:
        LOG.error("UI callback failed: %s", exception_type.__name__)
        self._fatal()

    def _send(self, kind: str, payload: dict, *, track: bool = True) -> str | None:
        try:
            if track and len(self._pending) >= 16:
                raise DisconnectedError("Too many outstanding requests")
            message = Message.create(kind, payload)
            self.client.send(message)
            if track:
                self._pending[message.request_id] = (kind, time.monotonic() + 10)
            return message.request_id
        except (DisconnectedError, ProtocolError):
            LOG.warning("IPC request could not be queued")
            return None

    def _presence(self) -> None:
        # Only the live Tk loop renews presence, never the IPC worker. A stuck
        # or missing UI cannot keep its NT1 lease alive in the background.
        if self._ready and self._online and self.icon.visible:
            self._send("tray.presence", {"visible": True}, track=False)
            self._last_presence = time.monotonic()

    def _tick(self) -> None:
        if self._closing:
            return
        try:
            if self._overflow.is_set() or (self._ready and not self.icon.visible):
                self._fatal()
                return
            if not self._ready and time.monotonic() - self._started > 10:
                self._fatal()
                return
            for _ in range(64):
                try:
                    kind, payload = self._events.get_nowait()
                except Empty:
                    break
                if kind == "fatal":
                    self._fatal()
                    return
                if kind == "ready":
                    self._ready = True
                    self.dialogs.notice("startup", "Đã khởi động",
                                        "OpenGuard Kids đã khởi động và đang kết nối với dịch vụ.\n\n"
                                        "Biểu tượng luôn ở khay hệ thống. Con có thể xem thời gian còn lại và xin thêm giờ.")
                    self.client.start()
                elif kind == "connection":
                    self._connection(bool(payload))
                elif kind == "message":
                    self._message(payload)
                elif kind == "remaining":
                    self.show_remaining()
                elif kind == "request":
                    self.ask_more_time()
                if self._closing:
                    return
            now = time.monotonic()
            if now - self._last_presence >= 2:
                self._presence()
            expired = [key for key, (_, deadline) in self._pending.items() if deadline <= now]
            for key in expired:
                operation, _ = self._pending.pop(key)
                if operation == "status.get":
                    self._status = None
                self.dialogs.notice("connection", "Chưa nhận được phản hồi",
                                    "Dịch vụ chưa phản hồi. Kết quả yêu cầu chưa xác định; thời gian chưa được xác nhận thay đổi.")
                LOG.warning("IPC response timed out")
        except Exception as error:
            LOG.error("UI event processing failed: %s", type(error).__name__)
            self._fatal()
            return
        self.root.after(100, self._tick)

    def _connection(self, online: bool) -> None:
        self._online = online
        self._status = None
        self._pending.clear()
        self.icon.title = "OpenGuard Kids — " + ("đã kết nối" if online else "mất kết nối dịch vụ")
        if online:
            self._presence()
            self._send("status.get", {})
        else:
            self.dialogs.notice("connection", "Mất kết nối dịch vụ",
                                "Chưa lấy được thời gian và luật hiện tại. OpenGuard Kids đang thử kết nối lại.\n\n"
                                "Nếu vừa gửi yêu cầu, kết quả chưa xác định. Hãy kiểm tra lại trước khi gửi lại.")

    def _message(self, message: Message) -> None:
        try:
            validate_payload(message)
        except ProtocolError:
            LOG.error("Invalid Service payload; ending UI presence")
            self._fatal()
            return
        self._pending.pop(message.request_id, None)
        payload = message.payload
        if message.type == "status":
            first_status = self._status is None
            self._status = payload
            if first_status:
                text = (f"OpenGuard Kids đang hoạt động.\n\nLuật do {payload['rule_set_by']} thiết lập."
                        if payload["managed"] else "OpenGuard Kids đã cài đặt — chờ ghép nối.")
                self.dialogs.notice("startup", "Trạng thái quản lý", text)
            self._show_status()
        elif message.type in {"quota_warning", "blocked_app", "blocked_domain", "locked"}:
            self.dialogs.explanation(message.type, payload)
        elif message.type == "time.request.result":
            state = payload["status"]
            text = {"pending": "Yêu cầu đã được tiếp nhận. Con đang chờ phụ huynh duyệt.",
                    "approved": "Yêu cầu thêm giờ đã được duyệt.",
                    "rejected": "Yêu cầu thêm giờ chưa được duyệt."}[state]
            if state != "pending":
                text += f"\n\nNgười phản hồi: {payload['rule_set_by']}\nLý do: {payload['reason']}"
                self._status = None
                self._send("status.get", {})
            self.dialogs.notice("request_result", "Xin thêm giờ", text)
        elif message.type == "error":
            self.dialogs.notice("service_error", "Dịch vụ chưa xử lý được yêu cầu",
                                "Yêu cầu chưa hoàn tất. Hãy kiểm tra kết nối và thử lại sau.")
            LOG.warning("Service rejected an IPC request")

    def _show_status(self) -> None:
        from .dialogs import format_remaining

        payload = self._status
        if payload is None:
            return
        if not payload["managed"]:
            text = "Tài khoản này chưa được ghép nối để quản lý."
        else:
            text = (f"Thời gian còn lại: {format_remaining(payload['remaining_seconds'])}\n\n"
                    f"Người đặt luật: {payload['rule_set_by']}\nLý do: {payload['reason']}\n"
                    f"Luật liên quan: {payload['policy_id']}\n\n"
                    "Đây là số liệu tại lần cập nhật gần nhất từ dịch vụ.")
        self.dialogs.notice("remaining", "Thời gian còn lại", text, can_request=True)

    def show_remaining(self) -> None:
        if self._send("status.get", {}) is None:
            self.dialogs.notice("remaining", "Thời gian còn lại",
                                "Chưa kết nối được dịch vụ nên chưa biết thời gian còn lại.", can_request=True)
        else:
            self.dialogs.notice("remaining", "Thời gian còn lại", "Đang lấy số liệu mới nhất từ dịch vụ…", can_request=True)

    def ask_more_time(self) -> None:
        if any(kind == "time.request" for kind, _ in self._pending.values()):
            self.dialogs.notice("request_result", "Xin thêm giờ", "Yêu cầu đang được gửi. Hãy chờ phản hồi.")
            return
        self.dialogs.ask_more_time(self._submit_time)

    def _submit_time(self, minutes: int) -> bool:
        if self._send("time.request", {"minutes": minutes}) is None:
            return False
        self.dialogs.notice("request_result", "Xin thêm giờ", "Đang gửi yêu cầu qua dịch vụ, chờ xác nhận tiếp nhận.")
        return True

    def _fatal(self) -> None:
        self._failed = True
        LOG.error("Tray unavailable: stopping presence for NT1")
        self.shutdown()
        _fallback_notice("OpenGuard Kids không thể duy trì giao diện. Đã ngừng báo sẵn sàng cho dịch vụ.\n"
                         "Dịch vụ phải tạm dừng kiểm soát phiên này theo NT1.")

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing, self._ready = True, False
        # Closing the pipe immediately invalidates this connection's presence.
        self.client.close()
        try:
            self.icon.stop()
        except Exception as error:
            LOG.error("Tray cleanup failed: %s", type(error).__name__)
        try:
            self.root.destroy()
        except Exception as error:
            LOG.error("Tk cleanup failed: %s", type(error).__name__)


def _fallback_notice(text: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, text, "OpenGuard Kids", 0x10)


def _configure_logging() -> None:
    directory = Path(os.environ["LOCALAPPDATA"]) / "OpenGuardKids" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "tray.log", maxBytes=256 * 1024,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _require_user_session() -> None:
    if os.name != "nt":
        raise RuntimeError("Tray requires Windows")
    session = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise RuntimeError("Cannot identify interactive session")
    if session.value == 0 or ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Tray requires a non-elevated interactive User session")


def main() -> int:
    root = None
    app = None
    try:
        _configure_logging()
        _require_user_session()
        import tkinter as tk
        import pystray
        from PIL import Image, ImageDraw

        # Local vector primitives rendered to the tray icon; no remote assets.
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill="#176b56")
        draw.line([(18, 32), (28, 42), (47, 22)], fill="white", width=6)
        root = tk.Tk()
        root.withdraw()  # Tk host only; the pystray icon remains visible.
        app = TrayApplication(root, pystray, image)
        app.run()
        return 1 if app._failed else 0
    except Exception as error:
        LOG.error("Tray startup/runtime failed: %s", type(error).__name__)
        _fallback_notice("OpenGuard Kids chưa thể khởi động giao diện.\n"
                         "Hãy chạy bằng tài khoản User, kiểm tra thư viện và nhật ký nội bộ.")
        return 1
    finally:
        if app is not None:
            app.shutdown()
        elif root is not None:
            root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
