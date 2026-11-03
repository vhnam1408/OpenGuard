# OpenGuard Kids

OpenGuard Kids là đồ án hỗ trợ phụ huynh quản lý thời gian và nội dung sử dụng máy tính của trẻ trên Windows 10/11 x64. Kho mã gồm Windows Agent, Server FastAPI, giao diện Tray, hợp đồng dữ liệu, kiểm thử và công cụ đóng gói.

**Trạng thái:** đang phát triển. Các thành phần đã có mã nguồn và kiểm thử riêng; Agent chưa nối đầy đủ enrollment/policy/transport, Dashboard và Child View còn là khung thư mục. Bộ cài đã được build và smoke test; nghiệm thu cài/gỡ và luồng toàn hệ thống còn cần thực hiện trong VM/lab. Xem [bằng chứng kiểm tra đóng gói](tests/evidence/PACKAGING_VALIDATION.md).

## Cấu trúc dự án

- [Tài liệu Đặc tả và Kiến trúc Hệ thống](docs/architecture/SYSTEM_ARCHITECTURE_SRS.md): sáu phần theo yêu cầu, gồm kiến trúc, chức năng, vòng đời dữ liệu, cây thư mục và bảo mật.
- [Quyền riêng tư và danh mục dữ liệu](PRIVACY.md).
- [A - Agent](packages/agent/README.md): Windows Service và các mô-đun thực thi.
- [B - Server](packages/server/README.md): FastAPI, dữ liệu và dịch vụ nghiệp vụ.
- [C - Giao diện](packages/interfaces/README.md): Tray UI, Dashboard và Child View.
- [Hợp đồng dùng chung](packages/contracts/README.md): lược đồ và giao thức tích hợp.
- [Đóng gói Windows](packaging/README.md): hai cấu hình PyInstaller, bộ cài Inno Setup và sáu bước build/kiểm tra.

## Lấy mã nguồn

```powershell
git clone https://github.com/vhnam1408/OpenGuard.git
cd OpenGuard
```

Yêu cầu Python 3.11+. Agent và Tray chạy trên Windows. Chạy các lệnh bên dưới từ thư mục gốc repository.

## Chạy và kiểm thử

- **Server:** làm theo [hướng dẫn Server](packages/server/README.md) để tạo môi trường Python, cấu hình secret, khởi tạo SQLite và chạy API. Secret phải được tạo riêng cho môi trường triển khai.
- **Agent và Tray:** xem [Agent](packages/agent/README.md), [Tray](packages/interfaces/README.md) và [hợp đồng IPC](docs/api/TRAY_IPC.md). README từng package nêu chức năng hiện có và phần tích hợp còn thiếu.
- **Bộ cài Windows:** làm theo [sáu bước build và kiểm tra](packaging/README.md). Cần Python x64 và Inno Setup; kết quả là `dist/installer/OpenGuardKidsSetup.exe`.

Sau khi cài dependency theo hướng dẫn từng package, dùng Python của môi trường tương ứng:

```powershell
python -m pytest packages/server/tests -q
python -m unittest discover -s packages/agent/tests -v
python -m unittest discover -s packages/interfaces/tests -v
python -m unittest discover -s tests/packaging -v
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tests\packaging\test_install_support.ps1
```

Kiểm thử dùng tài khoản và dữ liệu giả. Việc build được bộ cài không thay thế nghiệm thu chức năng; các tiêu chí trong SRS vẫn là yêu cầu phải kiểm chứng.

## Nội dung lưu trên GitHub

Repository chứa mã nguồn, cấu hình dependency, tài liệu, kiểm thử và script build. `.gitignore` loại môi trường Python, cache, `runtime/`, cơ sở dữ liệu, log, secret và thư mục `build/`, `dist/`. Bộ cài `.exe` được tạo bằng script build, không đưa vào lịch sử mã nguồn.

## Quyền riêng tư

Triết lý: **Công cụ đồng hành, minh bạch, không phải phần mềm theo dõi lén.** Chỉ cài trên máy được phép quản lý với sự đồng ý của cha mẹ/người giám hộ; kiểm thử bằng tài khoản giả lập. Không thu nội dung cá nhân, không chạy ẩn, không xuất dữ liệu trẻ sang dịch vụ giám sát bên thứ ba.

Nguồn yêu cầu và tham chiếu thiết kế được mô tả trong phần 1 của SRS.
