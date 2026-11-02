# OpenGuard Kids

Kho tài liệu thiết kế và khung thư mục cho đồ án OpenGuard Kids trên Windows 10/11 x64.

- [Tài liệu Đặc tả và Kiến trúc Hệ thống](docs/architecture/SYSTEM_ARCHITECTURE_SRS.md): sáu phần theo yêu cầu, gồm kiến trúc, chức năng, vòng đời dữ liệu, cây thư mục và bảo mật.
- [Quyền riêng tư và danh mục dữ liệu](PRIVACY.md).
- [A - Agent](packages/agent/README.md): Windows Service và các mô-đun thực thi.
- [B - Server](packages/server/README.md): FastAPI, dữ liệu và dịch vụ nghiệp vụ.
- [C - Giao diện](packages/interfaces/README.md): Tray UI, Dashboard và Child View.
- [Hợp đồng dùng chung](packages/contracts/README.md): lược đồ và giao thức tích hợp.

**Trạng thái:** đã xây dựng tài liệu và khung thư mục; chưa triển khai mã ứng dụng, bản cài `.exe` hoặc kiểm thử chức năng. Các tiêu chí trong SRS là yêu cầu nghiệm thu, không phải kết quả đã đo.

Triết lý: **Công cụ đồng hành, minh bạch, không phải phần mềm theo dõi lén.** Chỉ cài trên máy được phép quản lý với sự đồng ý của cha mẹ/người giám hộ; kiểm thử bằng tài khoản giả lập. Không thu nội dung cá nhân, không chạy ẩn, không xuất dữ liệu trẻ sang dịch vụ giám sát bên thứ ba.

Cơ sở tổng hợp là hai tài liệu người dùng cung cấp trong Downloads; các tệp nguồn được giữ nguyên tại vị trí ban đầu. Tham chiếu chi tiết nằm trong phần 1 của SRS.
