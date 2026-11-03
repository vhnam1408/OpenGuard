# Phân hệ B - Server

Package Python `ogk_server`, dùng FastAPI, Uvicorn, SQLAlchemy 2 và SQLite WAL. Đã triển khai model, xác thực API, enrollment và heartbeat theo [SRS](../../docs/architecture/SYSTEM_ARCHITECTURE_SRS.md).

| Thư mục trong `src/ogk_server/` | Trách nhiệm |
|---|---|
| `api/routes/` | REST endpoints cho auth, enroll, policy, heartbeat, events, requests, reports, audit, lifecycle |
| `api/dependencies/` | Phiên đăng nhập, xác thực Agent, RBAC và phạm vi sở hữu |
| `api/web/` | Web routes; kiểm quyền và render template từ package C |
| `db/models.py` | Parent, Child, Device, Binding, Policy, Event và trạng thái mã enrollment, refresh token, phiên phụ huynh, rate limit |
| `db/migrations/` | Migration có phiên bản |
| `services/` | Nghiệp vụ B1-B6; giao dịch, báo cáo, chống trùng và xóa dữ liệu |
| `security/` | Argon2id, token, CSRF, khóa, chữ ký chính sách |
| `realtime/` | WebSocket, outbox, giao lệnh và ACK |
| `jobs/` | Retention 90 ngày, hết hạn yêu cầu và xử lý việc chờ |

Server dùng `ogk_contracts` và nạp giao diện web từ `ogk_ui`; không phụ thuộc pywin32/pystray. DB Server nằm trong volume dữ liệu được cấu hình ngoài mã nguồn; không dùng chung DB với Agent.

## Chạy trong môi trường phát triển

Chạy PowerShell tại thư mục gốc kho mã. Cần Python 3.11+ và `uv`:

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r packages/server/requirements-dev.lock
uv pip install --python .venv\Scripts\python.exe --no-deps -e packages/server

New-Item -ItemType Directory -Path runtime -Force | Out-Null
$env:OGK_DATABASE_URL = 'sqlite:///runtime/server.db'
$env:OGK_JWT_SECRET = & .venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(48))'
$env:OGK_POLICY_SECRET = & .venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(48))'
$env:OGK_REFRESH_DAYS = '30'

& .venv\Scripts\python.exe -m ogk_server.cli init-db
& .venv\Scripts\python.exe -m ogk_server.cli create-parent parent@example.test
& .venv\Scripts\python.exe -m ogk_server.cli create-child parent@example.test 'Demo child'
& .venv\Scripts\python.exe -m uvicorn ogk_server.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

CLI hỏi mật khẩu bằng `getpass`, yêu cầu tối thiểu 10 ký tự và in `parent_id`/`child_id`. Không có endpoint đăng ký công khai. Mật khẩu dùng Argon2id, salt ngẫu nhiên riêng; tham số hiện tại là 64 MiB, 3 vòng, parallelism 4, cần hiệu chỉnh khi triển khai trên máy khác.

Các lệnh tạo secret ở trên dành cho lần chạy lab đầu tiên. Khi khởi động lại phải nạp lại **cùng secret** từ cấu hình bí mật; đổi secret JWT sẽ làm mất hiệu lực JWT và hash credential hiện có, đổi secret policy thay khóa kiểm tra policy. Không commit hoặc ghi log giá trị secret. Đường dẫn DB tương đối được tính từ working directory; thư mục chứa DB phải tồn tại. Bản triển khai thật đặt DB trên volume cục bộ ngoài kho mã và cấu hình TLS 1.2+ tại reverse proxy/Uvicorn; Agent thực hiện pinning. WAL yêu cầu các kết nối cùng máy, không đặt DB trên network share. [SQLite WAL](https://sqlite.org/wal.html)

Server dùng application factory, không đọc secret/kết nối DB ngay lúc import. Startup tạo schema cho DB mới bằng `create_all`; lệnh này không nâng cấp schema đã tồn tại. Thư mục `db/migrations/` dành cho migration ở giai đoạn tiếp theo.

## Hợp đồng API

Tất cả đường dẫn dưới `/api/v1`. Các response có `Cache-Control: no-store`. Xem OpenAPI tại `/docs` khi chạy lab.

| Method / route | Xác thực | Request / kết quả chính |
|---|---|---|
| `POST /auth/login` | Email + mật khẩu | `{email, password}` → access JWT phụ huynh 900 giây |
| `POST /auth/logout` | Bearer JWT phụ huynh | Xóa phiên phía Server, trả 204 |
| `POST /enroll` | Bearer JWT phụ huynh + quyền sở hữu trẻ | `{child_id, consent: true}` → mã 8 ký tự, `expires_in: 600` |
| `POST /enroll/complete` | Mã dùng một lần; thêm binding vào thiết bị hiện có cần Bearer JWT Agent của thiết bị | `{code, installation_ref, sid_ref}` → device/binding ID, access JWT 900 giây, refresh token, policy và khóa kiểm tra |
| `POST /auth/refresh` | Refresh token trong JSON body | `{refresh_token}` → access + refresh token mới |
| `POST /heartbeat` | Bearer JWT Agent | `{policy_version, quota_used_sec, status?}` → policy_version mới nhất, server_time, data_epoch, heartbeat_interval_sec 60 |
| `GET /policy` | Bearer JWT Agent | Policy mới nhất của binding trong token và HMAC-SHA256 |

`installation_ref` là SHA-256 dạng 64 ký tự hex thường của định danh bản cài **ngẫu nhiên**, không phải kho thông tin phần cứng. `sid_ref` là tham chiếu SID đã được Service tối giản/băm cục bộ, cũng 64 ký tự hex thường; không gửi username, hostname hoặc SID thô. Service phải lấy đúng SID của tài khoản trẻ được chọn, không mặc định lấy tài khoản quản trị đang chạy installer. Không dùng fingerprint thay bằng chứng xác thực để lấy lại thiết bị. Nếu mất mọi credential, cần quy trình khôi phục/ngắt ghép nối rõ ràng ở giai đoạn tiếp theo.

Mã phân biệt hoa/thường, alphabet `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, không chứa ký tự dễ nhầm. Tạo mã mới hủy mã chưa dùng trước đó của trẻ. Mỗi phụ huynh được tạo tối đa 5 mã/10 phút; giới hạn theo IP là 20/10 phút. Đổi mã bị giới hạn 20 lần/IP/10 phút và 5 lần/mã/10 phút. Counter được lưu trong SQLite và dùng chung giữa worker. Login giới hạn theo IP và email, sau 5 lần sai khóa tài khoản 15 phút. Rate limit dùng cửa sổ cố định, trả 429 và `Retry-After`. Đặt trusted proxy rõ ràng để ASGI peer IP chính xác; không tin header `X-Forwarded-For` từ nguồn tùy ý.

Ví dụ heartbeat:

```http
POST /api/v1/heartbeat
Authorization: Bearer <agent-access-token>
Content-Type: application/json

{"policy_version":1,"quota_used_sec":60,"status":"online"}
```

Agent lên lịch gửi mỗi 60 giây. Server tiếp nhận cả ping sớm do reconnect; nhịp 60 giây không phải hạn mức cứng từ chối ping. `status` nhận `online`, `idle`, `locked`, `paused`. `device_id` và `binding_id` là tùy chọn để đối soát; nếu gửi phải khớp token, sai trả 403. Server không dùng quota báo lên để cấp lại ngân sách thực thi. Khi `policy_update_available=true`, Agent gọi `GET /api/v1/policy`; `policy_version` luôn là version Server mới nhất.

## Bảo mật và giao dịch

Các route bảo mật dùng dependency `CurrentParent`/`CurrentAgent` để kiểm JWT, vai trò, phiên, phạm vi device/binding/child, trạng thái phụ huynh/trẻ/thiết bị và thu hồi binding. Chữ ký chỉ cho phép HS256, có kiểm issuer, audience, expiration và các claim bắt buộc. [FastAPI JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/), [PyJWT](https://pyjwt.readthedocs.io/en/latest/api.html)

API phụ huynh hiện dùng Bearer token và phiên 15 phút lưu phía Server. Khi triển khai Dashboard dùng cookie phải bổ sung HttpOnly/Secure/SameSite, CSRF và giới hạn phiên 12 giờ theo SRS; API này chưa phát cookie.

Refresh token là secret ngẫu nhiên, lưu HMAC hash phía Server, hạn mặc định 30 ngày và cấu hình từ 1–90 ngày. Mỗi lần dùng tạo token mới, giữ hạn tuyệt đối của family. Nếu phát hiện dùng lại token đã luân chuyển, Server thu hồi mọi refresh token của binding và tăng `token_version`, khiến cả access token cũ mất quyền ngay. Agent phải gửi refresh tuần tự và lưu credential mới bằng DPAPI.

Mã enrollment cũng chỉ lưu HMAC hash. Giao dịch ghi bắt đầu bằng `BEGIN IMMEDIATE` trước lần đọc đầu, tiêu thụ mã có điều kiện; tạo device/binding/policy/token cùng commit hoặc cùng rollback. SQLite bật WAL, foreign keys, busy timeout 10 giây; lỗi busy trả 503 để retry. Unique index bảo đảm một device–SID chỉ có một binding active; composite foreign key chặn policy/event sai phạm vi. [SQLAlchemy SQLite transactions](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)

Policy khởi tạo version 1, chưa chặn ứng dụng/tên miền, lịch cho phép toàn tuần và quota 86.400 giây/ngày. Giao diện cấu hình luật chưa thuộc phần triển khai này; khi bổ sung phải bảo vệ danh sách ngoại lệ an toàn theo SRS. Policy dùng schema và canonical `signing_payload()` của `ogk_contracts`; HMAC bao phủ nội dung, version, device/child/binding. Enrollment trả khóa HMAC riêng theo binding dạng base64url; Agent lưu bằng DPAPI và kiểm chữ ký/đích/chống hạ version trước áp dụng. Khóa master không rời Server.

Đã bổ sung WSS `/ws/agent`, outbox bền vững, ACK lệnh, `POST/GET /api/v1/commands` và `POST /api/v1/events` có chống trùng/kiểm epoch. Xem [hợp đồng đồng bộ và hướng dẫn tích hợp Agent](../../docs/api/REALTIME_SYNC.md). `Event` giữ bảy trường hoạt động; `id`, `binding_id`, `data_epoch`, `idempotency_key` là metadata lưu trữ/phong bì.

Enrollment cấp `cleanup_token` độc lập và chỉ lưu hash để chuẩn bị kênh vòng đời theo LIFE-01. Kênh nhận lệnh xóa, tác vụ retention Server định kỳ, AES-GCM cho trường nhạy cảm và Dashboard vẫn là các phần tiếp theo. API upload đã từ chối event quá 90 ngày; chưa có API phục vụ báo cáo/event.

## Kiểm thử

```powershell
& .venv\Scripts\python.exe -m pytest packages/server/tests -q
```

Kiểm thử dùng hồ sơ giả và SQLite file tạm, bao phủ WAL/foreign keys, Argon2id, TTL JWT/mã, HMAC policy, IDOR, token sai/hết hạn, thu hồi tức thời, refresh rotation/reuse, khóa login, giới hạn thử mã, nhiều SID trên cùng thiết bị và request enroll/refresh đồng thời. Đây là kiểm thử thành phần Server; chưa chứng minh độ trễ thực thi policy hoặc hành vi Agent Windows.
