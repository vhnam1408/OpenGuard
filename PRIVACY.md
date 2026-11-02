# OpenGuard Kids - Quyền riêng tư và danh mục dữ liệu

## Phạm vi

OpenGuard Kids chỉ thu siêu dữ liệu phục vụ quản lý minh bạch. Không thu nội dung tin nhắn, email, tài liệu, URL đầy đủ, từ khóa tìm kiếm, tiêu đề cửa sổ, phím gõ hoặc ảnh màn hình. Không gửi báo cáo/hồ sơ sang dịch vụ phân tích hoặc giám sát bên thứ ba.

Đây là đặc tả dữ liệu phục vụ triển khai. Schema hoạt động được giữ đúng bảy trường.

## Dữ liệu hoạt động

| Trường | Mục đích | Thời hạn tối đa | Người được xem |
|---|---|---|---|
| `ts` | Sắp xếp và tổng hợp theo thời gian | 90 ngày từ thời điểm phát sinh | Phụ huynh sở hữu; trẻ tương ứng |
| `device_id` | Phân biệt thiết bị tạo sự kiện | 90 ngày trong event | Phụ huynh sở hữu; trẻ tương ứng |
| `child_id` | Gắn dữ liệu với đúng hồ sơ | 90 ngày trong event | Phụ huynh sở hữu; trẻ tương ứng |
| `type` | Phân loại chín hành vi kỹ thuật cho phép | 90 ngày | Phụ huynh sở hữu; trẻ tương ứng |
| `subject` | Tên ứng dụng hoặc miền đăng ký; không chứa đường dẫn/URL | 90 ngày | Phụ huynh sở hữu; trẻ tương ứng |
| `duration_sec` | Tổng hợp thời lượng hợp lệ | 90 ngày | Phụ huynh sở hữu; trẻ tương ứng |
| `policy_id` | Giải thích luật tại thời điểm xảy ra | 90 ngày trong event | Phụ huynh sở hữu; trẻ tương ứng |

Hàng đợi ngoại tuyến không gia hạn thời hạn lưu. Dữ liệu dẫn xuất, cache và bản xuất phải tuân theo cùng giới hạn. Admin không mặc nhiên có quyền xem báo cáo gia đình.

## Dữ liệu vận hành và bảo mật

| Nhóm | Mục đích | Vòng đời | Quyền truy cập |
|---|---|---|---|
| SID, binding, cấu hình thiết bị | Áp luật đúng tài khoản Windows | Khi liên kết còn hoạt động; xóa ánh xạ vận hành khi ngắt | Service; Server chỉ giữ định danh ánh xạ tối thiểu nếu cần |
| Policy, bộ đếm, checkpoint | Thực thi và chống đặt lại quota | Đến khi thay thế, hết mục đích hoặc xóa liên kết; không giữ lịch sử quá 90 ngày | Service; phụ huynh/trẻ xem phần chính sách phù hợp |
| Hash mật khẩu và phiên phụ huynh | Xác thực | Đến khi thay mật khẩu/xóa tài khoản; phiên tối đa 12 giờ | Dịch vụ xác thực; không hiển thị hash |
| Access/refresh token | Xác thực liên kết thiết bị | Access 15 phút; refresh theo cấu hình, thu hồi khi ngắt | Service và dịch vụ xác thực; không hiển thị |
| Yêu cầu thêm giờ/báo chặn nhầm | Ghi nhận tiếng nói và phản hồi | Tối đa 90 ngày | Phụ huynh sở hữu; trẻ tương ứng |
| Audit thay đổi luật | Giải thích ai thay gì | Tối đa 90 ngày; xóa cùng hồ sơ | Phụ huynh sở hữu; trẻ xem nội dung liên quan, ẩn IP |
| Biên nhận xóa và khóa dọn dẹp | Giao lệnh xóa tới máy offline, nhận ACK | Đến ACK hoặc khi công việc được đóng theo chính sách vận hành được công bố | Dịch vụ vòng đời; không cho đọc báo cáo, không chứa lịch sử hoạt động |

Access token, mã enrollment, khóa mã hóa và mật khẩu không được ghi vào log. Log kỹ thuật phải tối giản, loại dữ liệu cá nhân và có thời hạn; không sử dụng như bản sao lịch sử hoạt động.

## Ba thao tác của phụ huynh

1. **Xóa dữ liệu:** xóa lịch sử và cache hoạt động, giữ liên kết, policy, token và trạng thái quota hiện hành.
2. **Xóa ghép nối:** thu hồi quyền quản lý liên kết, dọn dữ liệu/credential cục bộ; báo cáo đã nhận còn trong thời hạn 90 ngày.
3. **Xóa hồ sơ:** xóa hồ sơ và toàn bộ dữ liệu liên quan trên Server, thu hồi mọi liên kết và yêu cầu các Agent xóa cache tương ứng.

Nếu Agent offline, Dashboard ghi rõ đang chờ xóa cục bộ. Chỉ xác nhận xóa trên thiết bị khi đã nhận ACK. Xóa/gỡ phần mềm không xóa Windows account, Windows profile hoặc tệp cá nhân.

## Bảo vệ và quyền xem

TLS 1.2+ và certificate pinning bảo vệ kênh truyền. AES-GCM bảo vệ dữ liệu nhạy cảm khi lưu; khóa Agent được bảo vệ bằng DPAPI và ACL. Mọi truy vấn kiểm tra quyền sở hữu ở Server. Trẻ được xem cùng số liệu lõi và thay đổi chính sách của mình.
