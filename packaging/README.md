# Đóng gói OpenGuard Kids cho Windows x64

Bộ cài máy trẻ theo SRS §2.2 và §5.1: PyInstaller tạo hai bundle `onedir`, Inno Setup nén chúng thành **một tệp `OpenGuardKidsSetup.exe`**. Máy đích không cần Python. Server FastAPI và Dashboard là đơn vị phát hành riêng theo §5.1, không được cài thành Service máy trẻ.

“Hidden service” ở đây là **không có cửa sổ console**, với `console=False`; tên/mô tả Service vẫn hiện trong SCM/Services. Tray có biểu tượng công khai, chạy quyền User; không cấu hình giao diện Service Session 0. Không dùng cơ chế giấu tiến trình, tự ghép SID của quản trị viên thành SID của trẻ hay chống gỡ.

## Sáu bước build và kiểm tra

### 1. Chuẩn bị máy build

Dùng Windows 10/11 **x64**, Python 3.11+ **x64** có Tcl/Tk và **Inno Setup 6.3+**. Máy đích thuộc phạm vi x64; cấu hình không hỗ trợ ARM64/x86. Build không yêu cầu quyền Administrator. Từ thư mục gốc repository:

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r .\packaging\requirements-build.txt .\packages\agent '.\packages\interfaces[tray]'
```

Đường dẫn repository có khoảng trắng/tiếng Việt được hỗ trợ. Chỉ build trên Windows; PyInstaller không phải trình biên dịch chéo từ Linux.

### 2. Kiểm tra logic đóng gói bằng dữ liệu giả

```powershell
.\.venv-build\Scripts\python.exe -m unittest discover -s tests/packaging -v
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tests\packaging\test_install_support.ps1
```

Các test này không đăng ký/dừng/xóa Service thật, không sửa ACL hệ thống, không đổi DNS hay mở giao diện quản lý. Kiểm thử PowerShell tạo ACL trong bộ nhớ, dùng socket loopback cổng ngẫu nhiên và mock SCM.

### 3. Biên dịch Agent và Tray

```powershell
.\.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller .\packaging\pyinstaller\ogk_agent.spec
.\.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller .\packaging\pyinstaller\ogk_ui.spec
```

Kết quả:

```text
dist/ogk_agent/ogk_agent.exe
dist/ogk_agent/_internal/...
dist/ogk_ui/ogk_ui.exe
dist/ogk_ui/_internal/...
```

Phải giữ toàn bộ `_internal`, không chỉ chép hai EXE. Chọn `onedir` để Service SYSTEM nạp DLL từ Program Files được bảo vệ, tránh giải nén executable đặc quyền vào thư mục tạm. Tài liệu [PyInstaller về chế độ đóng gói](https://pyinstaller.org/en/stable/operating-mode.html) giải thích yêu cầu quyền tệp này.

`service_entry.py` xử lý lời gọi SCM không tham số bằng `Initialize` → `PrepareToHostSingle` → `StartServiceCtrlDispatcher`. Việc đăng ký Service thuộc installer; không gọi `ogk_agent.exe install`. `tray_entry.py` giữ mutex theo phiên, ngăn hai Tray chạy đồng thời và bỏ qua `--autostart` nếu đang ở phiên elevated/Session 0. Hai EXE đều dùng manifest `asInvoker`; SCM cấp SYSTEM cho Agent.

### 4. Tạo một bộ cài duy nhất

```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 .\packaging\inno_setup\OpenGuardKids.iss
```

Hoặc tự động cài dependency vào venv đã chọn, build hai bundle, kiểm tra import trên EXE bằng `--smoke-test`, compile Inno và in SHA-256 bằng một lệnh:

```powershell
.\scripts\build-windows.ps1 -Python .\.venv-build\Scripts\python.exe -Version 0.1.0
```

Nếu ISCC ở nơi khác, truyền `-Iscc 'D:\Tools\Inno Setup 6\ISCC.exe'`. Smoke test không tạo icon, đăng ký Service hoặc thực thi luật. Kết quả cuối: **`dist\installer\OpenGuardKidsSetup.exe`**. Chỉ cần phát hành tệp này; không cần phát hành Python/venv. Khi phát hành có ký số, ký hai EXE trước khi compile Inno rồi ký Setup sau cùng.

### 5. Cài và nghiệm thu trong Windows VM/lab được phép

Chạy Setup từ phiên User bình thường và chấp thuận UAC; có thể nhập tài khoản admin khác. `PrivilegesRequired=admin` và kiểm tra token trong helper bắt buộc quyền quản trị, không tắt UAC hay tự thay chính sách UAC. Windows x64 và phiên bản OS được kiểm tra bởi Inno.

Helper chạy **Windows PowerShell 5.1 x64** với `-NoProfile -NonInteractive`; `ExecutionPolicy Bypass` chỉ áp dụng cho tiến trình đó, không thay chính sách máy. Mọi thao tác Service đều kiểm lỗi và có timeout khi chờ dừng/chạy/xóa. Lỗi được ghi vào Setup log và `%TEMP%\is-...tmp\OpenGuardKids-install-support.log` của phiên installer.

Các hành động cài đặt:

- Kiểm tra quyền; dừng Service cũ đúng đường dẫn nếu nâng cấp; thử bind độc quyền **UDP và TCP `127.0.0.1:53`**. Có xung đột thì dừng cài; không chiếm cổng bằng cách kết thúc dịch vụ khác. Đây là kiểm tra tại thời điểm cài; runtime vẫn phải xử lý lỗi bind nếu cổng bị chiếm sau đó.
- Cài vào `%ProgramFiles%\OpenGuardKids` cố định; SYSTEM/Administrators có FullControl, Users chỉ ReadAndExecute. Không cho User sửa EXE/DLL chạy SYSTEM.
- Tạo `%ProgramData%\OpenGuardKids\{state,cache,logs,config}`. DACL chỉ SYSTEM (`S-1-5-18`) và Administrators (`S-1-5-32-544`) có FullControl, bỏ quyền kế thừa và quyền cấp rõ ràng cũ, đặt owner Administrators. Không cấp `users-modify`/Everyone. DACL áp dụng cả dữ liệu đã tồn tại, gồm SQLite WAL/journal. Từ chối junction/symlink trong vùng triển khai/dữ liệu.
- Tạo/cập nhật SCM **`OpenGuardKidsAgent`**, tên hiển thị **OpenGuard Kids Agent**, `LocalSystem`, `Automatic`, đường dẫn binary có dấu ngoặc kép, recovery restart sau **5/15/60 giây**, reset sau 24 giờ. Stop bình thường không kích hoạt recovery; recovery không được cho phép runtime kiểm soát khi thiếu Tray.
- Tạo shortcut **Common Startup** và Start Menu trỏ tới `ui\ogk_ui.exe`, không ghi HKCU của admin dùng cho UAC. Shortcut được Explorer chạy bằng token User ở từng lần đăng nhập.
- Cho chạy Tray ngay bằng `runasoriginaluser`, mặc định được chọn ở trang hoàn tất. Nếu Setup đã được mở elevated/“Run as administrator”, Inno không lấy lại được token User gốc: bootstrap bỏ qua lần chạy này; mở shortcut từ phiên User hoặc đăng nhập lại. Cài silent chỉ đăng ký shortcut và không mở Tray ngay.

Giới hạn token gốc là hành vi được ghi trong [tài liệu Inno Setup](https://jrsoftware.org/ishelp/topic_runsection.htm); không thay bằng scheduled task chạy SYSTEM hoặc “highest privileges”.

Kiểm tra trong VM bằng PowerShell Administrator (chỉ đọc):

```powershell
Get-CimInstance Win32_Service -Filter "Name='OpenGuardKidsAgent'" |
    Select-Object Name, DisplayName, StartName, StartMode, State, PathName
sc.exe qfailure OpenGuardKidsAgent
icacls.exe "$env:ProgramData\OpenGuardKids"
icacls.exe "$env:ProgramData\OpenGuardKids\state"
icacls.exe "$env:ProgramFiles\OpenGuardKids"
```

Dùng tài khoản chuẩn giả lập để xác nhận không ghi được ProgramData/Program Files, Tray có integrity Medium và Agent ở SYSTEM/Session 0. Tray dùng IPC, không đọc trực tiếp DB/token. Thử UAC bằng admin khác, hai User/chuyển nhanh phiên, khởi động lại và đăng nhập: đo icon ≤30 giây, thông báo ≤10 giây sau icon. Chỉ ghép tài khoản trẻ bằng luồng enrollment được xác thực.

### 6. Kiểm tra nâng cấp, gỡ và ranh giới runtime

Giữ nguyên AppId cho các lần nâng cấp. Thử cài lại khi Agent/Tray đang chạy: Service phải dừng trước thay tệp; Restart Manager xử lý tệp UI đang dùng. Nếu chuẩn bị lỗi/hủy thì phục hồi Service cũ vốn đang chạy. Dữ liệu được giữ nguyên. Đây không phải cơ chế rollback đầy đủ giữa các phiên bản schema; backup/migration phải do tầng storage triển khai.

Gỡ bằng Windows Installed Apps: helper kiểm tra đúng chủ sở hữu Service, dừng và xóa SCM **trước** khi Inno xóa binary/shortcut. Nếu không dừng/xóa được, gỡ bị chặn để tránh bỏ lại Service trỏ vào tệp đã xóa. Giữ ProgramData có ACL bảo vệ; việc xóa dữ liệu là luồng vòng đời có xác thực trong SRS Phần 4, không phải xóa đệ quy mù của installer. Không xóa Windows account/profile hoặc dữ liệu cá nhân.

Mã Agent hiện không sửa DNS adapter/hosts, nên installer không có thay đổi mạng cần hoàn nguyên. Nếu sau này nối lớp định tuyến DNS theo SID, phải thêm và kiểm thử cleanup có xác thực/có ACK trước stop/remove; không được coi gói này đã đáp ứng cleanup cho một runtime mới có sửa mạng.

SRS yêu cầu Service ngừng thu thập/thực thi khi mất Tray và chỉ bật sau enrollment. `AgentRuntime` trong repository hiện vẫn trả `managed=false`, chưa nối đầy đủ enrollment/policy/transport và các worker thực thi. Bộ cài không tự triển khai các chức năng còn thiếu, không tạo binding, không chứng nhận NT1/độ trễ hay các tiêu chí end-to-end. Cần kiểm tra mất Tray, lỗi icon, tài khoản ngoài binding và trạng thái chờ ghép nối sau khi hoàn tất tích hợp.
