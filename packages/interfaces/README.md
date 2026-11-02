# Phân hệ C - Giao diện

Package Python dự kiến: `ogk_ui`. Chứa hai phần được phát hành tới hai môi trường khác nhau.

- `src/ogk_ui/tray/`: Tray dùng pystray, chạy quyền User trên Windows; IPC client, enrollment, cảnh báo, xin thêm giờ và báo chặn nhầm.
- `src/ogk_ui/web/templates/parent/`: Dashboard phụ huynh bằng Jinja2.
- `src/ogk_ui/web/templates/child/`: Child View cùng dữ liệu lõi với phụ huynh.
- `src/ogk_ui/web/templates/shared/`: layout và thành phần dùng chung.
- `src/ogk_ui/web/static/`: CSS, JavaScript, biểu tượng và thư viện đã đóng gói cục bộ.

Tray chỉ sử dụng IPC và hợp đồng dùng chung, không import mã thực thi SYSTEM hoặc DB Server. Server nạp template bằng package resources, ví dụ Jinja2 PackageLoader; đường dẫn không phụ thuộc thư mục đang chạy. Phụ thuộc pystray/pywin32 dành cho Tray được tách khỏi phụ thuộc web.

C1: Dashboard; C2: Tray; C3: Child View; C4: giao diện yêu cầu. C5 (dữ liệu phân loại) đặt tại `data/classification/`; C6 (QA tích hợp) đặt tại `tests/`.

Hiện đây là khung thư mục phục vụ triển khai. Yêu cầu giao diện và minh bạch nằm trong [SRS](../../docs/architecture/SYSTEM_ARCHITECTURE_SRS.md).
