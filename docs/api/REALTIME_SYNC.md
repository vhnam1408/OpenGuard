# Đồng bộ thời gian thực và hàng đợi ngoại tuyến

Hai module chính:

- Server: `packages/server/src/ogk_server/realtime/ws_handler.py`.
- Agent: `packages/agent/src/ogk_agent/storage/offline_queue.py`.

Schema dùng chung nằm trong `ogk_contracts.sync`. `create_app()` đã đăng ký các tuyến dưới đây; bảng `commands` được tạo thêm khi Server khởi động. Không thay đổi cột của các bảng hiện có.

## Lệnh Server → Agent

Agent mở **WSS `/ws/agent`**, gửi header `Authorization: Bearer <access-token>`. Server xác thực binding/device/child/parent và token_version trước accept, trước mỗi lượt giao lệnh và mỗi ACK. Token qua query string bị từ chối. Access token hết hạn hoặc binding bị thu hồi khiến kết nối đóng với mã 1008; Agent cần refresh tuần tự rồi kết nối lại nếu còn quyền.

Token cấp mới có scope `heartbeat`, `policy`, `enroll`, `realtime`, `events`. Token access từ phiên bản cũ cần được thay bằng token cấp từ `/api/v1/auth/refresh` sau khi nâng cấp; không dùng lại refresh token đã luân chuyển.

Frame đầu tiên:

```json
{"type":"ready","binding_id":"binding-1","protocol_version":1}
```

Phụ huynh tạo lệnh bằng **POST `/api/v1/commands`**, Bearer JWT phụ huynh có quyền sở hữu binding:

```json
{
  "binding_id": "binding-1",
  "idempotency_key": "unique-parent-operation-1",
  "type": "add_time",
  "seconds": 900,
  "ttl_seconds": 60
}
```

Khóa ngay dùng `type: "lock_now"`, `seconds: 0`. Cộng giờ nhận 1–86.400 giây; TTL nhận 5–300 giây, mặc định 60. Gửi lại cùng key và cùng nội dung trả cùng command_id, không gia hạn TTL; thay nội dung trả 409. HTTP 202 chỉ xác nhận lưu outbox.

Server poll outbox chung mỗi 0,5 giây, nên REST request và WebSocket có thể ở worker khác nhau, không cần broker trong triển khai SQLite cùng máy. Khi mất ACK, retry sau ít nhất 1 giây, giữ nguyên command_id và hạn lệnh; kết nối lại tiếp tục đọc outbox. Mỗi lượt lấy tối đa 32 lệnh, mỗi socket write có timeout 2 giây. Đích **<5 giây khi online** được kiểm thử trong môi trường lab; nghẽn mạng/DB và thời gian thực thi trên Windows cần đo nghiệm thu riêng.

Frame giao lệnh có dạng:

```json
{
  "type": "command",
  "command_id": "command-1",
  "binding_id": "binding-1",
  "command": "add_time",
  "seconds": 900,
  "status": "Pending",
  "created_at": "2026-11-03T08:00:00+00:00",
  "expires_at": "2026-11-03T08:01:00+00:00"
}
```

Agent kiểm đích, hạn lệnh và `command_id`. **Áp dụng quota và lưu kết quả chống lặp trong cùng giao dịch cục bộ**, rồi gửi ACK. Lệnh nhận lại chỉ trả kết quả cũ, không cộng thêm giờ. Lệnh cộng giờ không tự mở ngoài lịch tuần. Kênh WSS phải được xác minh TLS; không giao khóa ký JWT cho Agent.

```json
{"type":"ack","command_id":"command-1","status":"Applied"}
```

ACK nhận `Applied`, `Rejected`, `Expired`. Server kiểm ACK thuộc đúng binding, đã có lần giao, và còn hạn; ACK sai đích/không tồn tại/chưa giao trả `accepted: false`. ACK giống kết quả cuối trả lại thành công, ACK mâu thuẫn không đổi kết quả. Lệnh quá TTL chuyển `Expired` và không được xác nhận `Applied` bằng ACK đến muộn.

```json
{"type":"ack_received","command_id":"command-1","accepted":true}
```

**GET `/api/v1/commands/{command_id}`** cho phụ huynh đọc trạng thái; chỉ hiển thị “đã áp dụng” khi trạng thái là `Applied`. `Expired` nghĩa là không có xác nhận áp dụng đúng hạn. ACK `Rejected`/`Expired` không được diễn giải thành đã thực thi.

## Event ngoại tuyến

`OfflineQueue()` mở `%ProgramData%\OpenGuardKids\state\agent.db`. Installer phải tạo trước thư mục được bảo vệ ACL chỉ SYSTEM/Administrators ghi. Khi test có thể truyền đường dẫn SQLite rõ ràng; thư mục cha phải tồn tại. Queue dùng WAL, `synchronous=FULL`, giao dịch và khóa theo thread; dùng chung tệp với `SQLiteCheckpointStore` nhưng dùng bảng riêng.

Sau enrollment tin cậy, gọi `configure_binding(binding_id, device_id=..., child_id=..., data_epoch=...)`. Producer gọi `enqueue(event, binding_id=..., data_epoch=...)` **trước khi thử gửi mạng**, kể cả online. Hàm trả idempotency_key được lưu bền vững. Có thể truyền key ổn định nếu producer cũng cần retry việc enqueue. Payload vẫn đúng bảy trường của `ogk_contracts.events.Event`; không ghi token vào queue.

Worker do runtime quản lý có thể dùng:

```python
uploader = HTTPSBatchUploader("https://ogk.example", token_provider)
queue.sync_once(binding_id, uploader, prepare_sync=prepare_sync)
# Hoặc chạy trong worker riêng, stop là threading.Event:
queue.sync_forever(binding_id, uploader, prepare_sync=prepare_sync, stop=stop)
```

`prepare_sync` là callback bắt buộc. Callback kiểm vòng đời/thu hồi/xóa trước, làm mới xác thực, đồng bộ policy, cập nhật epoch trong queue, rồi trả `True` khi được upload; trả `False` để tạm dừng. `configure_binding` với epoch mới xóa queue cũ cùng giao dịch; epoch lùi bị từ chối. `revoke_binding` xóa event chờ và giữ tombstone để không tự kích hoạt lại binding. Khi service dừng, đặt `stop`, join worker rồi `close()` queue; network request có timeout mặc định 10 giây.

`HTTPSBatchUploader` dùng xác minh chứng thư mặc định, từ chối HTTP và redirect, lấy access token mới qua callback mỗi lần gửi. Có thể truyền opener có HTTPSHandler/SSLContext để tích hợp pinning của bản triển khai, giữ cơ chế từ chối redirect. Không giữ khóa SQLite trong lúc gọi mạng.

**POST `/api/v1/events`** yêu cầu Bearer JWT Agent:

```json
{
  "protocol_version": 1,
  "batch_id": "batch-1",
  "binding_id": "binding-1",
  "data_epoch": 1,
  "events": [{
    "idempotency_key": "event-1",
    "event": {
      "ts": "2026-11-03T08:00:00+00:00",
      "device_id": "device-1",
      "child_id": "child-1",
      "type": "app_start",
      "subject": "notepad.exe",
      "duration_sec": 0,
      "policy_id": "policy-1"
    }
  }]
}
```

Mỗi batch tối đa 100 event. Server kiểm quyền, epoch hiện tại, policy thuộc binding, retention tối đa 90 ngày tính từ lúc phát sinh, và lệch tương lai tối đa 5 phút. Batch commit toàn bộ hoặc rollback toàn bộ. Unique key `(binding_id, data_epoch, idempotency_key)` loại trùng ngay cả khi gửi đồng thời; cùng key nhưng khác event trả 409. Event sai phạm vi trả 403; epoch cũ trả 409 và phải kiểm vòng đời trước khi retry.

ACK chỉ gửi sau commit:

```json
{
  "batch_id": "batch-1",
  "binding_id": "binding-1",
  "data_epoch": 1,
  "accepted_keys": ["event-1"],
  "server_time": "2026-11-03T08:00:01+00:00"
}
```

Queue chỉ xóa các key được ACK đúng batch/binding/epoch; ACK sai hoặc timeout giữ dữ liệu. Batch ID và key không đổi khi restart/retry. Nếu Server commit nhưng phản hồi mất, lần retry được ACK mà không ghi thêm event. Queue hỗ trợ ACK từng phần dù API hiện ACK cả batch. Retry mạng dùng backoff có jitter tối đa 60 giây; batch thành công được rút liên tiếp. Gọi `prune()` định kỳ khi offline lâu; chọn batch/đếm queue cũng tự bỏ event hết retention. Xóa logic và `secure_delete` không bảo đảm ghi đè mọi bản sao trong WAL/backup/SSD.

`AgentRuntime` hiện chưa có enrollment/transport thực tế. Hai module cung cấp API tích hợp; việc nối worker với phiên Windows, kiểm policy/DPAPI/pinning, kênh vòng đời và chống áp dụng lệnh lặp tại Agent vẫn thuộc lớp runtime. Kiểm thử dùng hồ sơ giả; không khóa phiên thật hoặc cài Service.

## Kiểm thử

```powershell
python -m pytest packages/server/tests -q
python -m unittest discover -s packages/agent/tests -p test_offline_queue.py -v
```

Test Server bao gồm push online <5 giây qua TestClient và Uvicorn/WebSocket thật trên loopback, ACK/retry/reconnect/expiry/IDOR, thu hồi khi socket đang mở, batch đồng thời và tình huống Server đã commit nhưng mất phản hồi, đóng/mở lại queue rồi đồng bộ không trùng. Test Agent bao gồm ACK sai/từng phần, nhiều producer, dùng chung DB checkpoint, retention và epoch/thu hồi trước upload.
