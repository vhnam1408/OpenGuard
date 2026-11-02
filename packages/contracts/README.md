# Hợp đồng tích hợp dùng chung

Package Python dự kiến: `ogk_contracts`. Chỉ chứa định nghĩa dữ liệu, hằng số và quy tắc kiểm tra giao thức; không chứa kết nối DB, mã Windows, giao diện hoặc bí mật.

- `events/`: event đúng bảy trường, enum chín loại và phong bì đồng bộ có idempotency key/data_epoch.
- `policy/`: cấu trúc chính sách, version và định dạng dữ liệu được ký.
- `ipc/`: thông điệp Tray-Service và mã lỗi.
- `commands/`: command_id, phạm vi liên kết, hạn lệnh, ACK và trạng thái.

A, B và C cùng sử dụng package này để tránh tự định nghĩa hợp đồng khác nhau. Chia sẻ schema không thay thế việc xác thực lại tại Server/Service.
