# Tray ↔ Windows Service, IPC v1

Hợp đồng được khai báo tại `packages/contracts/src/ogk_contracts/ipc.py`.
Đây là hợp đồng tích hợp mới; Named Pipe server trong Agent chưa được triển khai.

## Kết nối và xác thực

- Pipe cục bộ: `\\.\pipe\OpenGuardKids.Tray.v1`, duplex, byte stream.
- UTF-8 JSON, mỗi thông điệp kết thúc bằng LF (`\n`), tối đa 65.536 byte gồm LF.
- Phong bì có đúng bốn trường: `version` (số nguyên 1), `request_id` (UUID),
  `type`, `payload` (object). Không gửi token, SID tự khai hoặc đường dẫn DB.
- Request dùng UUID mới; response dùng lại `request_id`. Push dùng UUID mới.
- Service phải tạo pipe với DACL chỉ cho SYSTEM/Administrators và User được phép,
  dùng `PIPE_REJECT_REMOTE_CLIENTS`, xác minh SID/phiên thực từ Windows trước khi
  trả trạng thái hoặc xử lý yêu cầu. Client dùng `SECURITY_IDENTIFICATION` để
  Service có thể nhận diện, không được mượn quyền client để thao tác tài nguyên.
- Đối tượng quản lý lấy từ kết nối đã xác thực, không lấy từ nội dung thông điệp.
  Service chịu trách nhiệm chống pipe giả mạo/squatting (ví dụ first-instance),
  phân tuyến đúng SID/phiên và chống các phiên không được phép.

```json
{"version":1,"request_id":"be1d7ce1-3e99-4cba-bb23-88cacbf2eda0","type":"status.get","payload":{}}
```

## Thông điệp

| Chiều | `type` | `payload` |
|---|---|---|
| UI → Service | `status.get` | `{}` |
| UI → Service | `time.request` | `minutes`: nguyên, 1–120; chỉ xin, không tự cộng giờ |
| UI → Service | `tray.presence` | `visible`: `true`; không cần ACK |
| Service → UI | `status` | `managed`: boolean; nếu true, thêm `remaining_seconds`, `rule_set_by`, `reason`, `policy_id` |
| Service → UI | `quota_warning` | `remaining_seconds`, `rule_set_by`, `reason`, `policy_id` |
| Service → UI | `blocked_app`, `blocked_domain`, `locked` | `rule_set_by`, `reason`, `policy_id`; tùy chọn `subject` |
| Service → UI | `time.request.result` | `status`: `pending` / `approved` / `rejected`; hai trạng thái cuối bắt buộc `rule_set_by`, `reason` |
| Service → UI | `error` | `code`: mã kỹ thuật, không chứa dữ liệu cá nhân |

`remaining_seconds` là số nguyên 0–604800. Các chuỗi giải thích bắt buộc không
rỗng, tối đa 512 ký tự; `subject` tối đa 253 ký tự, chỉ tên app/tên miền tối thiểu,
không URL hoặc đường dẫn cá nhân. Service phải kiểm tra payload của request theo
allowlist và tự kiểm quyền; hợp đồng phong bì không thay thế xác thực.

Ví dụ cảnh báo (dữ liệu giả):

```json
{"version":1,"request_id":"0f4815bd-61cf-434b-90d1-38d2dddbd6b2","type":"quota_warning","payload":{"remaining_seconds":60,"rule_set_by":"Phụ huynh mẫu","reason":"Đã đến giờ nghỉ theo lịch","policy_id":"policy-demo"}}
```

Service phát cảnh báo ở 10/5/1 phút nếu mốc có thể đạt tới; 60 giây lưu bài nằm
trước mốc khóa. Tray chỉ hiển thị số liệu Service gửi, không tự tính quota,
không khóa máy và không cấp lại ân hạn. Kết quả duyệt dẫn đến tải lại trạng thái;
không cộng số phút ngay trên UI.

## NT1 và lỗi

Tk chạy trên main thread, pystray chạy trên thread Windows riêng. Hộp thoại là
cửa sổ thường, không chặn vòng lặp, không chiếm toàn màn hình hoặc trợ giúp OS.
[Tài liệu pystray](https://pystray.readthedocs.io/en/latest/usage.html) cho phép
chạy vòng lặp icon trên thread riêng khi chỉ nhắm Windows.
Không có menu ẩn hoặc tùy chọn chạy không icon. Cửa sổ Tk gốc chỉ làm host và
được withdraw; icon và thông báo khởi động vẫn phải hiện công khai.

UI chỉ mở IPC sau khi đặt icon visible và hiển thị thông báo khởi động. Main
loop gửi `tray.presence` mỗi 2 giây khi icon còn visible. Mất icon/lỗi UI thì
đóng pipe và dừng presence. Service phải kiểm tra mỗi 5 giây, hủy lease ngay khi
pipe đóng hoặc sau tối đa 5 giây không nhận presence mới, tạm dừng thu thập/thực
thi cho đúng phiên và tháo cấu hình mạng do mình sở hữu theo SRS. Không giữ lease
cũ qua reconnect; không tin trạng thái sẵn sàng sau khi UI đã mất.

`Icon.visible` phản ánh trạng thái backend pystray, không chứng minh người dùng
đã nhìn thấy icon (Windows có thể đưa icon vào vùng overflow). Cần QA Windows
thực tế với Explorer restart, chuyển User, crash/hang Tray và Service. Phần UI
này không tự bảo đảm việc Service dừng kiểm soát khi Agent chưa triển khai.

Kết nối lại có backoff 0,5–10 giây. Hàng đợi được giới hạn; các request chưa xác
nhận bị bỏ khi mất kết nối, không tự gửi lại thao tác xin giờ. Service nên dùng
`request_id` làm khóa idempotency và lưu trạng thái yêu cầu. UI chờ phản hồi tối
đa 10 giây và hiển thị kết quả chưa xác định nếu hết hạn. Không coi việc ghi
thành công vào pipe là phụ huynh đã nhận/đồng ý.

Log kỹ thuật có xoay vòng tại `%LOCALAPPDATA%\OpenGuardKids\logs\tray.log`
(256 KiB × tối đa 3 tệp), không ghi payload/tên phụ huynh/lý do/token. UI không
đọc/ghi DB hoặc vùng ProgramData của Agent, không gọi mã SYSTEM.
