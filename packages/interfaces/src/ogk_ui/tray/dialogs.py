"""Non-modal tkinter dialogs. All methods must run on the Tk main thread.

No fullscreen overlay, global input grab, forced topmost window or lock action.
Closing a dialog leaves the system tray visible.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


def format_remaining(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} giờ {minutes:02d} phút {seconds:02d} giây" if hours else f"{minutes} phút {seconds:02d} giây"


class Dialogs:
    def __init__(self, root: tk.Tk, request_more_time: Callable[[], None]) -> None:
        self.root = root
        self.request_more_time = request_more_time
        self._notices: dict[str, tuple[tk.Toplevel, tk.StringVar]] = {}
        self._request_window: tk.Toplevel | None = None

    def _window(self, title: str) -> tuple[tk.Toplevel, ttk.Frame]:
        window = tk.Toplevel(self.root)
        window.title(f"OpenGuard Kids — {title}")
        window.minsize(440, 180)
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        return window, frame

    def notice(self, key: str, title: str, text: str, *, can_request: bool = False) -> None:
        existing = self._notices.get(key)
        if existing and existing[0].winfo_exists():
            existing[1].set(text)
            existing[0].title(f"OpenGuard Kids — {title}")
            existing[0].deiconify()
            existing[0].lift()
            return
        window, frame = self._window(title)
        body = tk.StringVar(master=window, value=text)
        ttk.Label(frame, textvariable=body, wraplength=480, justify="left").pack(fill="x", pady=(0, 20))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        if can_request:
            ttk.Button(buttons, text="Xin thêm giờ", command=self.request_more_time).pack(side="left")
        ttk.Button(buttons, text="Đóng", command=window.destroy).pack(side="right")
        self._notices[key] = (window, body)

    def explanation(self, kind: str, payload: dict) -> None:
        title = {
            "quota_warning": "Sắp đến giờ nghỉ",
            "blocked_app": "Ứng dụng bị chặn",
            "blocked_domain": "Trang web bị chặn",
            "locked": "Phiên bị hạn chế",
        }[kind]
        lines = []
        if kind == "quota_warning":
            seconds = payload["remaining_seconds"]
            lines.append(f"Còn {format_remaining(seconds)} trước mốc khóa do Service thông báo.")
            if seconds <= 60:
                lines.append("Đây là thời gian lưu bài cuối cùng. Hãy lưu công việc đang làm.")
        else:
            lines.append(title + (f": {payload['subject']}" if payload.get("subject") else "."))
        lines.extend((f"Người đặt luật: {payload['rule_set_by']}", f"Lý do: {payload['reason']}",
                      f"Luật liên quan: {payload['policy_id']}"))
        self.notice(kind, title, "\n\n".join(lines), can_request=True)

    def ask_more_time(self, submit: Callable[[int], bool]) -> None:
        if self._request_window and self._request_window.winfo_exists():
            self._request_window.deiconify()
            self._request_window.lift()
            return
        window, frame = self._window("Xin thêm giờ")
        self._request_window = window
        ttk.Label(frame, text="Con muốn xin thêm bao nhiêu phút?", wraplength=440).pack(anchor="w")
        minutes = tk.StringVar(master=window, value="15")
        ttk.Spinbox(frame, from_=1, to=120, textvariable=minutes, width=8).pack(anchor="w", pady=12)
        ttk.Label(frame, text="Yêu cầu sẽ được gửi cho phụ huynh. Thời gian chỉ thay đổi sau khi được duyệt.",
                  wraplength=440).pack(anchor="w")
        feedback = tk.StringVar(master=window)
        ttk.Label(frame, textvariable=feedback, wraplength=440).pack(anchor="w", pady=10)

        def send() -> None:
            try:
                value = int(minutes.get())
                if not 1 <= value <= 120:
                    raise ValueError
            except ValueError:
                feedback.set("Hãy nhập số phút từ 1 đến 120.")
                return
            if submit(value):
                window.destroy()
            else:
                feedback.set("Chưa gửi được yêu cầu. Hãy kiểm tra trạng thái kết nối rồi thử lại.")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Gửi yêu cầu", command=send).pack(side="left")
        ttk.Button(buttons, text="Hủy", command=window.destroy).pack(side="right")
