# Kiểm tra đóng gói Windows — 03/11/2026

Môi trường build: Windows 11 x64; Python 3.12.14 x64; PyInstaller 6.22.3; Inno Setup 6.7.4. Dependency build nằm trong venv riêng `runtime/packaging-build`; Inno dùng chế độ portable trong `runtime/tools`, không đăng ký bộ công cụ vào Windows.

| Kiểm tra | Kết quả |
|---|---|
| Build `ogk_agent.spec` và `ogk_ui.spec` | Thành công |
| Chạy `--smoke-test` trên cả hai EXE | Exit code 0; không tạo Service/Tray |
| Kiểm tra PE/manifest hai EXE | Windows GUI subsystem (2), `asInvoker` |
| Test entry point đóng gói | 6/6 đạt |
| Test Agent hiện có | 53/53 đạt ngoài sandbox |
| Test Interfaces hiện có | 21/21 đạt |
| Test Windows PowerShell 5.1 | Đạt: ACL, UDP/TCP, chủ sở hữu Service, phục hồi khi preflight lỗi, cấu hình SYSTEM/auto-start/recovery bằng mock |
| Compile `OpenGuardKids.iss` cuối cùng | Exit code 0, không cảnh báo |

Lần chạy test Agent trong sandbox ban đầu gặp 5 lỗi socket/Named Pipe; chạy lại cùng bộ test ngoài sandbox đạt toàn bộ 53 test. Không chỉnh mã runtime để bỏ qua các kiểm tra này.

Artifact: `dist/installer/OpenGuardKidsSetup.exe`, **32.925.000 byte**, chưa ký số.

```text
SHA-256: A7B4040D348F629580EBC8A3C3E1A2119C13590D29AA19B3A374B9ACF5DF97EB
```

Chưa chạy bộ cài OpenGuard Kids hoặc cài/gỡ Service thật. UAC với admin khác, ACL thực tế sau cài, SCM chạy SYSTEM sau reboot, Tray User trong nhiều phiên, nâng cấp/gỡ và các ngưỡng NT1 cần nghiệm thu trên VM/lab được phép theo `packaging/README.md`. Đây là bằng chứng build/kiểm tra đóng gói, không phải chứng nhận toàn bộ hệ thống đã tích hợp hoàn chỉnh.
