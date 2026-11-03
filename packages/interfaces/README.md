# Phân hệ C - Giao diện

Package Python dự kiến: `ogk_ui`. Chứa hai phần được phát hành tới hai môi trường khác nhau.

- `src/ogk_ui/tray/`: Tray dùng pystray, chạy quyền User trên Windows; IPC client, enrollment, cảnh báo, xin thêm giờ và báo chặn nhầm.
- `src/ogk_ui/web/templates/parent/`: Dashboard phụ huynh bằng Jinja2.
- `src/ogk_ui/web/templates/child/`: Child View cùng dữ liệu lõi với phụ huynh.
- `src/ogk_ui/web/templates/shared/`: layout và thành phần dùng chung.
- `src/ogk_ui/web/static/`: CSS, JavaScript, biểu tượng và thư viện đã đóng gói cục bộ.

Tray chỉ sử dụng IPC và hợp đồng dùng chung, không import mã thực thi SYSTEM hoặc DB Server. Server nạp template bằng package resources, ví dụ Jinja2 PackageLoader; đường dẫn không phụ thuộc thư mục đang chạy. Phụ thuộc pystray/pywin32 dành cho Tray được tách khỏi phụ thuộc web.

C1: Dashboard; C2: Tray; C3: Child View; C4: giao diện yêu cầu. C5 (dữ liệu phân loại) đặt tại `data/classification/`; C6 (QA tích hợp) đặt tại `tests/`.

## Chạy Tray trên Windows

Yêu cầu Python 3.11+ có tkinter (bản cài Python Windows thông thường có sẵn).
Chạy bằng tài khoản User tương tác, không dùng Run as administrator hoặc SYSTEM:

```powershell
python -m pip install -e './packages/interfaces[tray]'
ogk-tray
# Hoặc: python -m ogk_ui.tray.main
```

Phụ thuộc Tray được khai báo riêng trong extra `tray`; cài package web không
import pystray/pywin32. Entry point GUI không tự nâng quyền. Bộ cài cần dùng
manifest `asInvoker` và đăng ký khởi động trong phiên User theo SRS.

- `tray/main.py`: icon, menu “Thời gian còn lại”/“Xin thêm giờ”, điều phối Tk.
- `tray/ipc_client.py`: kết nối Named Pipe bất đồng bộ, backoff và giới hạn queue.
- `tray/dialogs.py`: hộp thoại công khai, giải thích luật và yêu cầu thêm giờ.
- Log lỗi nội bộ: `%LOCALAPPDATA%\OpenGuardKids\logs\tray.log`.

Không có chế độ ẩn. Lỗi icon/UI dừng báo sẵn sàng và đóng IPC; Service phải dừng
kiểm soát phiên khi lease hết hạn. Agent đã có Named Pipe server; khi chạy riêng
Tray mà Agent chưa chạy, Tray hiển thị trạng thái chưa kết nối và thử lại. Chi tiết thông điệp, bảo mật
và nghĩa vụ phía Service ở [hợp đồng IPC](../../docs/api/TRAY_IPC.md).

Kiểm thử tự động bằng dữ liệu giả (không cần GUI):

```powershell
python -m unittest discover -s packages/interfaces/tests -v
```

Yêu cầu giao diện và minh bạch nằm trong [SRS](../../docs/architecture/SYSTEM_ARCHITECTURE_SRS.md).
