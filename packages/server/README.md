# Phân hệ B - Server

Package Python dự kiến: `ogk_server`, dùng FastAPI, Uvicorn, SQLAlchemy và SQLite WAL trong môi trường đồ án.

| Thư mục trong `src/ogk_server/` | Trách nhiệm |
|---|---|
| `api/routes/` | REST endpoints cho auth, enroll, policy, heartbeat, events, requests, reports, audit, lifecycle |
| `api/dependencies/` | Phiên đăng nhập, xác thực Agent, RBAC và phạm vi sở hữu |
| `api/web/` | Web routes; kiểm quyền và render template từ package C |
| `db/models/` | SQLAlchemy models: parent, child, device, binding, policy, event, request, command, audit, deletion |
| `db/migrations/` | Migration có phiên bản |
| `services/` | Nghiệp vụ B1-B6; giao dịch, báo cáo, chống trùng và xóa dữ liệu |
| `security/` | Argon2id, token, CSRF, khóa, chữ ký chính sách |
| `realtime/` | WebSocket, outbox, giao lệnh và ACK |
| `jobs/` | Retention 90 ngày, hết hạn yêu cầu và xử lý việc chờ |

Server dùng `ogk_contracts` và nạp giao diện web từ `ogk_ui`; không phụ thuộc pywin32/pystray. DB Server nằm trong volume dữ liệu được cấu hình ngoài mã nguồn; không dùng chung DB với Agent.

Hiện đây là khung thư mục phục vụ triển khai. Chi tiết API và kiểm soát quyền nằm trong [SRS](../../docs/architecture/SYSTEM_ARCHITECTURE_SRS.md).
