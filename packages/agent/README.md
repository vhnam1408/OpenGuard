# Phân hệ A - Agent

Package Python dự kiến: `ogk_agent`, tổ chức theo `src` layout. Phân hệ này chạy trên Windows, dùng pywin32/psutil và không chứa giao diện người dùng.

| Thư mục trong `src/ogk_agent/` | Trách nhiệm |
|---|---|
| `service/` | A1: vòng đời Windows Service, SCM, khởi tạo và dừng an toàn |
| `time_control/` | A2: quota, lịch 7x48, idle, cảnh báo và chống lùi giờ |
| `app_control/` | A3: nhận diện SHA-256 và kiểm tra SID trước khi chặn tiến trình |
| `dns/` | A4: DNS Proxy 127.0.0.1:53, SafeSearch, cache theo phạm vi |
| `storage/` | A5: SQLite, checkpoint, hàng đợi và mã hóa dữ liệu cục bộ |
| `policy/` | A6: xác thực và thực thi chính sách có phiên bản |
| `windows/` | Ánh xạ SID/phiên, quyền truy cập và thành phần hỗ trợ khóa phiên |
| `ipc/` | Named pipe server và kiểm tra danh tính phía gọi |
| `transport/` | HTTPS, heartbeat 60 giây, WSS, pinning và ACK |
| `lifecycle/` | Enrollment, xóa dữ liệu, ngắt liên kết và xóa hồ sơ |

DB chạy thật đặt tại `%ProgramData%\OpenGuardKids\state\agent.db`, không đặt trong kho nguồn. Chỉ SYSTEM/Administrators được ghi. Tray không truy cập DB hay token Agent trực tiếp.

Hiện đây là khung thư mục phục vụ triển khai. Ràng buộc nghiệp vụ và nghiệm thu nằm trong [SRS](../../docs/architecture/SYSTEM_ARCHITECTURE_SRS.md).
