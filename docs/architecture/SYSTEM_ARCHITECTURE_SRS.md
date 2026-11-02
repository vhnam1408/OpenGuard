# OpenGuard Kids - Tài liệu Đặc tả và Kiến trúc Hệ thống

**System Architecture & Software Requirements Specification**  
**Phiên bản:** 1.0 - **Ngày:** 02/11/2026  
**Đối tượng sử dụng:** nhóm lập trình, người kiểm thử và giảng viên đánh giá đồ án.  
**Nền tảng:** Windows 10/11 x64; Python 3.11+; FastAPI; SQLAlchemy; SQLite; pywin32; psutil; pystray; Jinja2.

## Phần 1: Tổng quan & Triết lý thiết kế

### 1.1. Mục tiêu và cơ sở đặc tả

OpenGuard Kids hỗ trợ phụ huynh thiết lập thời gian và nội dung sử dụng máy tính phù hợp với trẻ. Chính sách được cấu hình trên Dashboard, thực thi tại máy Windows, tiếp tục hoạt động khi mất mạng và được giải thích công khai cho trẻ. Hệ thống hỗ trợ quan hệ **một phụ huynh - nhiều trẻ - nhiều thiết bị**.

Triết lý cốt lõi:

> **Công cụ đồng hành, minh bạch, không phải phần mềm theo dõi lén.**

Tài liệu tổng hợp hai nguồn người dùng cung cấp:

- **[S1]** `MoTa_Doan_QLyViecSuDungPCCuaTre (1).docx`: yêu cầu NT1-NT6, F1-F5, kiến trúc, giao thức và tiêu chí nghiệm thu gốc.
- **[S2]** `Cẩm nang OpenGuard Kids.pdf`: phân hệ A/B/C; lộ trình 52 task bắt buộc và 3 task mở rộng; hướng dẫn tích hợp và kiểm thử. Các trang 8-9 tập hợp ngưỡng định lượng; trang 35-38 đối chiếu các chỗ cần chỉnh so với đề gốc; trang 42-50 mô tả luồng yêu cầu, báo cáo và xóa dữ liệu.

Các yêu cầu bổ sung của thiết kế là bộ cài `.exe` duy nhất, quản lý theo Windows SID, xóa dữ liệu ba mức và cấu trúc Monorepo. Cụm **Quyết định thiết kế** chỉ phương án do tài liệu này xác lập, không phải thông số được trích nguyên từ đề gốc. Tiêu chí bên dưới là yêu cầu phải kiểm chứng khi triển khai, không phải kết quả kiểm thử đã thực hiện.

### 1.2. Sáu nguyên tắc minh bạch

| Mã | Nguyên tắc | Biểu hiện bắt buộc |
|---|---|---|
| NT1 | Trẻ biết mình đang được hỗ trợ | Tray icon tồn tại trong phiên được quản lý, có thông báo khởi động, không có chế độ ẩn. Nếu không thể duy trì Tray, Agent dừng kiểm soát. |
| NT2 | Tối thiểu dữ liệu | Chỉ ghi siêu dữ liệu cần thiết: ứng dụng, tên miền, thời lượng và dấu thời gian; không ghi nội dung cá nhân. |
| NT3 | Trẻ có tiếng nói | Có nút “Xin thêm giờ”, “Con nghĩ trang này bị chặn nhầm”; yêu cầu có trạng thái và phản hồi. |
| NT4 | Mọi hành động giải thích được | Chặn ứng dụng, chặn web và khóa phiên phải nêu lý do, luật liên quan và người đặt luật; không giả vờ lỗi mạng. |
| NT5 | Minh bạch hai chiều | Trẻ xem cùng số liệu lõi về mình và lịch sử thay đổi chính sách; giao diện diễn đạt phù hợp lứa tuổi. |
| NT6 | An toàn có ưu tiên tuyệt đối | Miễn chặn cổng trường học, Tổng đài bảo vệ trẻ em 111 và dịch vụ cứu hộ; không vô hiệu hóa chức năng khẩn cấp sẵn có của hệ điều hành. |

Danh sách an toàn có sẵn trong policy và cache ngoại tuyến; mục mặc định không được xóa bằng luật thông thường. Miễn chặn DNS không được diễn giải thành lời hứa rằng Windows đang khóa vẫn mở được mọi website; cơ chế khóa dùng chức năng chuẩn của Windows, không tạo lớp phủ chiếm màn hình hoặc cản trở chuyển người dùng và trợ giúp của hệ điều hành.

### 1.3. Danh mục cấm BAN

1. Không đọc nội dung tin nhắn, email, tài liệu; không thu URL đầy đủ, tiêu đề cửa sổ, phím gõ hoặc ảnh chụp màn hình.
2. Không ẩn tiến trình/dịch vụ, dùng rootkit hoặc chống gỡ theo kiểu mã độc.
3. Không chuyển hồ sơ, event hay báo cáo của trẻ tới dịch vụ giám sát, phân tích hoặc phân loại đám mây của bên thứ ba. Tài nguyên frontend được đóng gói cục bộ, không gắn trình theo dõi.
4. Không thử nghiệm trên trẻ thật; không cài/kiểm thử trên máy người khác khi chưa có sự đồng ý. Dùng tài khoản và hồ sơ giả lập.
5. Không sử dụng, truy cập hoặc đính kèm tên miền nội dung người lớn trong dữ liệu kiểm thử; dùng miền công khai an toàn hoặc miền mô phỏng trung tính.

### 1.4. Phạm vi và truy vết

Các yêu cầu bắt buộc NT1-NT6, F1-F5 và bốn yêu cầu tích hợp phải có liên kết **yêu cầu -> task -> test case -> minh chứng -> người duyệt**. Lộ trình [S2] có 55 task; ngân sách theo nhóm ứng dụng T053 là mức **NÊN CÓ** theo [S1], TOTP T054 và thưởng giờ T055 là phần mở rộng. Không dùng phần mở rộng để thay thế nghiệm thu các chức năng bắt buộc.

## Phần 2: Kiến trúc Hệ thống Tổng thể - 4 Khối

### 2.1. Mô hình tổng thể và quyền hạn

```text
MAY TRE - WINDOWS 10/11                         MAY CHU LAN/VPS LAB
+-----------------------------------+          +---------------------------+
| Tray UI (User, theo SID/phien)      |          | Parent Dashboard          |
| hien thi, canh bao, gui yeu cau     |          | Jinja2 + HTML/CSS/JS      |
+----------------+------------------+          +-------------+-------------+
                 | Named pipe co ACL                         | HTTPS
+----------------v------------------+          +-------------v-------------+
| Windows Service (SYSTEM)          |<-------->| Server FastAPI + Uvicorn   |
| policy, quota, app, DNS, cache     |  HTTPS   | auth, policy, event, audit |
| SQLite trong ProgramData         |  WSS     | SQLAlchemy + SQLite WAL   |
+-----------------------------------+          +---------------------------+
```

| Khối | Quyền và công nghệ | Trách nhiệm | Ranh giới |
|---|---|---|---|
| Windows Service | LocalSystem; pywin32, psutil | A1-A6: quản lý dịch vụ, đếm giờ, chặn app, DNS Proxy, cache, thực thi policy; đồng bộ và xử lý vòng đời | Nơi quyết định thực thi; chỉ tác động đối tượng thuộc SID được liên kết |
| Tray UI | User; pystray và thành phần hộp thoại | C2/C4: icon, cảnh báo, enrollment, xin thêm giờ, báo chặn nhầm, mở Child View | Không giữ token dài hạn, không tự sửa quota, không đọc DB Agent trực tiếp |
| Server | FastAPI, Uvicorn, SQLAlchemy, SQLite WAL | B1-B6: tài khoản, thiết bị, enrollment, policy, event, báo cáo, audit, lệnh và xóa dữ liệu | Xác thực và kiểm tra sở hữu trước mọi truy vấn/thao tác |
| Parent Dashboard | Trình duyệt; Jinja2 | C1/C3: cấu hình, duyệt yêu cầu, báo cáo, nhật ký và ba mức xóa; Child View dùng cùng dịch vụ báo cáo | Mọi thay đổi đi qua Server; không kết nối trực tiếp DB hay IPC máy trẻ |

Service và Tray là hai tiến trình riêng. Không chạy Tray dưới SYSTEM và không cho Service tạo giao diện trực tiếp trong phiên tương tác; Windows tách dịch vụ khỏi desktop người dùng. [Microsoft - Interactive Services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)

### 2.2. Bộ cài và khởi tạo

**INST-01.** Phát hành một tệp `OpenGuardKidsSetup.exe`, bao gồm Agent, Tray và phụ thuộc; máy đích không phải tự cài Python. Quyết định thiết kế: dùng PyInstaller để đóng gói thành phần Python và Inno Setup làm bộ cài tổng; một tệp cài có thể triển khai nhiều tệp nội bộ.

Luồng cài đặt:

1. Quản trị viên chấp thuận UAC; installer kiểm tra Windows x64, quyền truy cập và xung đột cổng 53.
2. Cài Service với tên/mô tả rõ ràng, tự khởi động qua SCM và có Recovery Options cho crash.
3. Tạo vùng dữ liệu `%ProgramData%\OpenGuardKids`; chỉ SYSTEM/Administrators được ghi.
4. Đăng ký Tray chạy quyền User khi đăng nhập; khởi chạy ngay trong phiên phù hợp nếu phiên đã tồn tại.
5. Hiển thị trạng thái **“OpenGuard Kids đã cài đặt - chờ ghép nối”**. Service có thể chạy nền nhưng chưa thu thập hay thực thi luật với tài khoản chưa liên kết.
6. Sau enrollment và khi Tray sẵn sàng, hiển thị **“OpenGuard Kids đang hoạt động. Luật do [phụ huynh] thiết lập.”**

Tray phải xuất hiện trong **≤30 giây** sau đăng nhập; thông báo khởi động trong **≤10 giây** sau icon theo [S2]. Service kiểm tra Tray mỗi **5 giây**. Nếu mất Tray, tạm dừng thu thập/thực thi cho phiên liên quan, thử phục hồi; nếu không thể hiển thị icon thì dừng chế độ kiểm soát và tháo cấu hình mạng do mình sở hữu. Đây là lựa chọn ưu tiên NT1 của [S1], nghiêm ngặt hơn khoảng phục hồi vẫn kiểm soát được mô tả tại trang 40 của [S2]. Không dùng cơ chế recovery để lặp lại kiểm soát vô hình.

### 2.3. Enrollment và ánh xạ Windows SID

**ENR-01.** Phụ huynh đăng nhập Dashboard trên thiết bị của mình, chọn hồ sơ trẻ, xác nhận đồng ý và tạo mã ngẫu nhiên **8 ký tự**, có hạn **10 phút**, dùng một lần. Server giới hạn tần suất và số lần thử; không ghi mã vào log.

**ENR-02.** Trên máy trẻ chỉ nhập mã; không nhập mật khẩu phụ huynh. Service gửi mã cùng định danh bản cài/device fingerprint đã tối giản và tham chiếu tài khoản được chọn. Khi installer chạy bằng tài khoản quản trị khác, SID của tài khoản quản trị không được tự động trở thành SID của trẻ.

**ENR-03.** Server tiêu thụ mã bằng giao dịch nguyên tử, trả `device_id`, `binding_id`, access token **15 phút**, refresh token và policy đã bảo vệ toàn vẹn. Service lưu credential bằng DPAPI. Mã hết hạn/đã dùng phải bị từ chối; SID đã liên kết không được chuyển chủ sở hữu âm thầm.

Mô hình nhận dạng:

```text
Parent 1 ---- N ChildProfile
Device 1 ---- N ManagedBinding
ChildProfile 1 ---- N ManagedBinding

Local binding:  binding_id + device_id + child_id + windows_sid
Server binding: binding_id + device_id + child_id + sid_ref + status
```

`windows_sid` lấy từ Windows; `sid_ref` là tham chiếu tối thiểu dùng đối chiếu trên Server, không nhất thiết phải gửi SID thô. Một cặp thiết bị-SID chỉ có một liên kết đang hoạt động. Đổi tên tài khoản không thay đối tượng quản lý; xóa và tạo lại cùng tên nhưng SID khác phải enrollment lại.

**SID-01. Chặn app:** xác minh SID trong access token của tiến trình trước khi áp luật; không chặn toàn máy bằng tên tiến trình.

**SID-02. Khóa phiên:** Service tìm đúng phiên thuộc SID và yêu cầu thành phần hỗ trợ trong phiên đó khóa. `LockWorkStation` phải được gọi từ desktop tương tác; kết quả khởi phát thành công chưa chứng minh phiên đã khóa, cần đối soát thông báo phiên. Lệnh “mở khóa” của hệ thống chỉ gỡ hạn chế chính sách; không vượt qua mật khẩu/PIN Windows. [Microsoft - LockWorkStation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-lockworkstation)

**SID-03. Bảo toàn tài khoản:** ngắt ghép nối, xóa hồ sơ hoặc gỡ phần mềm không gọi xóa Windows account/profile, đổi mật khẩu, sửa nhóm quyền hoặc xóa tệp cá nhân. Trẻ phải dùng tài khoản chuẩn, không thuộc Administrators; installer không tự ý sửa quyền tài khoản khác.

**SID-04. Mạng:** DNS chuẩn không mang SID; đổi DNS card mạng toàn máy không đủ bảo đảm cô lập. Mô-đun `dns/` phải nhận ngữ cảnh liên kết từ một lớp phân giải/định tuyến theo người dùng đã xác thực, xử lý cả DNS Client và cache dùng chung. Đây là điểm phải làm PoC trên Windows trước khi chốt phương án kỹ thuật. Không gán mọi truy vấn cho tài khoản đang ở màn hình trước. Nếu chưa chứng minh được SID nguồn, không ghi truy vấn vào báo cáo của trẻ hoặc coi kiểm thử cô lập đã đạt. Điều kiện bắt buộc là tài khoản khác trên cùng máy không bị áp luật của trẻ.

### 2.4. IPC, heartbeat và WebSocket

| Kênh | Hợp đồng và xác thực | Nhịp/độ trễ |
|---|---|---|
| Tray-Service | Named pipe có ACL, giới hạn kết nối cục bộ; xác minh SID/phiên thực từ Windows; message có version, request_id, loại và payload bị giới hạn | Trạng thái/cảnh báo trong phiên; không tin SID tự khai của Tray |
| Agent-Server HTTPS | Token giới hạn theo device/binding; heartbeat gồm trạng thái, quota đã dùng, policy_version; Server trả version mới nhất và lệnh chờ | Heartbeat mỗi **60 giây** |
| Server-Agent WSS | Command có command_id, loại, binding đích, thời hạn và xác thực; Agent gửi ACK; có kết nối lại và chống áp dụng lặp | Khóa ngay, gỡ hạn chế, cộng **15 phút**: **<5 giây** khi online |
| Dashboard-Server HTTPS | Cookie bảo mật, CSRF, RBAC và quyền sở hữu | Policy thay đổi có hiệu lực **≤60 giây** khi online |

Khi lệch phiên bản, Agent gọi `GET /api/v1/policy`. Server có thể đẩy `policy.updated` qua WSS để tải ngay; heartbeat giữ vai trò đối soát. Độ trễ policy được đo đến lúc thực thi, không mặc nhiên đạt chỉ vì chu kỳ polling là 60 giây.

Các nhóm API dự kiến: `/auth`, `/enroll`, `/policy`, `/heartbeat`, `/events`, `/requests`, `/reports`, `/audit`, `/commands`, `/lifecycle`, dưới tiền tố `/api/v1`. WebSocket dùng tuyến `/ws/agent`. Lệnh chờ và outbox được lưu trước khi gửi; HTTP **202** của thao tác xóa chỉ xác nhận đã nhận xử lý, không thay ACK của Agent.

## Phần 3: Đặc tả Chức năng Cốt lõi

### 3.1. F1 - Quản lý thời gian

**TIME-01.** Lịch tuần là ma trận **7x48**: 7 ngày, mỗi ngày 48 ô **30 phút**. Policy quy định múi giờ, quota ngày thường/cuối tuần và các ô được phép. Phạm vi cơ sở là quota theo liên kết trên từng thiết bị; Dashboard phải thể hiện rõ phạm vi. Nếu thêm quota gộp nhiều thiết bị, Server phải phân bổ ngân sách để các máy offline không cùng tiêu toàn bộ một quota.

**TIME-02.** Bộ đếm chỉ tính phiên thuộc SID được quản lý. Không tính khi khóa, ngủ hoặc không có thao tác **quá 5 phút**. Dùng đồng hồ đơn điệu để đo khoảng thời gian, checkpoint có giao dịch để khởi động lại không đặt lại quota. Sai số **≤±30 giây mỗi giờ** so với đồng hồ kiểm thử.

**TIME-03.** Cảnh báo trước **10, 5 và 1 phút**, thể hiện lý do, người đặt luật và nút xin thêm giờ. Có **60 giây ân hạn** lưu bài. Quota ngắn chỉ phát mốc cảnh báo có thể đạt tới, không phát bù 10/5 phút.

**Quyết định thiết kế về ân hạn:** [S2, tr.45] mô tả thêm 60 giây sau hết quota, trong khi [S1] và bảng nghiệm thu [S2, tr.8] yêu cầu quota thử **3 phút** khóa đúng hạn, ngoài lịch khóa trong **≤5 giây**. Bản SRS này đặt ân hạn vào **60 giây cuối trước mốc khóa**, bảo toàn các ngưỡng nghiệm thu:

| Tình huống | Hành vi |
|---|---|
| Quota thử 3 phút | Bắt đầu đếm ngược lưu bài ở phút thứ 2; khóa ở mốc 3 phút sử dụng thực |
| Sắp kết thúc khung giờ | Cảnh báo/ân hạn trước ranh giới; khóa trong ≤5 giây khi sang giờ cấm |
| Đăng nhập hoặc mở lại khi vẫn ngoài lịch/hết quota | Khóa lại trong ≤5 giây; không cấp lặp ân hạn |
| Phụ huynh cộng giờ | Hủy mốc khóa do hết quota nếu các điều kiện còn lại cho phép |
| Lệnh khóa ngay | Thực thi <5 giây; không cộng thêm ân hạn |

**TIME-04.** Lùi đồng hồ hệ thống **3 giờ** không làm tăng quota, giảm thời gian đã dùng hoặc đặt lại ngày ngân sách. Agent đối chiếu đồng hồ đơn điệu, checkpoint và mốc thời gian tin cậy gần nhất. Phát hiện chỉnh giờ ghi nhật ký kỹ thuật tối giản; không tự thêm loại event ngoài schema phần 6.

### 3.2. F2 - Kiểm soát ứng dụng

Luồng bắt buộc: **phát hiện tiến trình -> xác minh SID -> xác định tệp -> tính SHA-256 -> đối chiếu policy -> chặn đúng PID -> giải thích qua Tray -> ghi event**.

Nhận diện dùng tên tiến trình kết hợp **SHA-256 của tệp thực thi**, không dựa riêng vào tên. Đổi tên `notepad.exe` thành `game.exe` vẫn phải nhận ra cùng nội dung tệp. Cache hash phải mất hiệu lực khi tệp thay đổi; xác minh lại tiến trình mục tiêu trước khi kết thúc để tránh nhầm PID tái sử dụng.

Khi chặn, Tray hiển thị tên ứng dụng, lý do, người đặt luật và nút báo chặn nhầm. Không kết thúc tiến trình của tài khoản ngoài liên kết hoặc tiến trình hệ thống thiết yếu. Ngoại lệ an toàn được đánh giá trước luật chặn thông thường.

### 3.3. F2 - DNS Proxy, phân loại và SafeSearch

**DNS-01.** Resolver cục bộ tại **`127.0.0.1:53`** hỗ trợ chuyển tiếp, timeout, A/AAAA, UDP và TCP khi cần; không mở thành DNS resolver công khai. Có kiểm tra xung đột cổng và khôi phục cấu hình do Agent sở hữu khi dừng/gỡ.

**DNS-02.** Chuẩn hóa tên miền, so khớp theo nhãn; chặn miền cha bao gồm miền con. `example.com` khớp `a.example.com`, không khớp `notexample.com`. Danh sách an toàn bắt buộc được kiểm tra trước luật chặn; cache tách theo phạm vi chính sách/liên kết.

**DNS-03.** Khi chặn, có thể trả NXDOMAIN nhưng phải gửi lý do tới Tray trong **≤5 giây**. Một miền chỉ lặp thông báo một lần mỗi **60 giây**; không dùng thông báo trình duyệt lỗi làm lời giải thích duy nhất.

**DNS-04.** Bảng phân loại tối thiểu **200 mục**, gồm sáu nhóm: giáo dục, giải trí, mạng xã hội, trò chơi, không phù hợp lứa tuổi, chưa phân loại. Chính sách mặc định cho trẻ **6-11 tuổi** phải nêu rõ quyết định cho từng nhóm và nguồn dữ liệu.

**DNS-05.** Cưỡng chế SafeSearch bằng ánh xạ DNS chính thức, ví dụ miền tìm kiếm Google sang `forcesafesearch.google.com`. Kiểm bằng `nslookup` và trình duyệt; chỉ áp dụng trong phạm vi quản lý đã xác định. [Google - Lock SafeSearch](https://support.google.com/websearch/answer/186669?hl=en)

**Nghiệm thu DNS:** ít nhất **48/50** miền cần chặn, tương đương **≥95%**; đồng thời **0/20 dương tính giả** trên miền giáo dục. Bộ 50 miền cần chặn và bộ 20 giáo dục phải có nhãn kỳ vọng rõ, không dùng nội dung người lớn. Bổ sung **5 điểm cuối DoH** theo phụ lục [S1] để ghi nhận khả năng xử lý. Không tuyên bố chống được mọi VPN, DoH, proxy hay truy cập IP; không giải mã HTTPS để đọc nội dung.

### 3.4. F3 - Báo cáo minh bạch

Dashboard có thời lượng theo ngày/tuần, top **10 ứng dụng**, top **10 tên miền**, số lần chặn và danh sách yêu cầu. Child View “Hôm nay em đã dùng gì” đọc cùng API báo cáo lõi, chỉ trong hồ sơ của trẻ và dùng ngôn ngữ đơn giản, biểu tượng phù hợp.

Không cộng chồng thời lượng nhiều ứng dụng để suy ra tổng thời gian sử dụng máy. Truy vấn DNS không chứng minh trẻ đang đọc website hoặc thời lượng xem; báo cáo tên miền phải diễn giải đúng là thống kê truy vấn/miền được ghi nhận, không suy ra nội dung đã xem.

### 3.5. Offline Cache

Agent lưu policy hợp lệ gần nhất, bộ đếm/checkpoint, hàng đợi event, yêu cầu chờ và kết quả lệnh cần chống lặp. Policy có version và HMAC/chữ ký; bản sửa trái phép bị từ chối, giữ bản hợp lệ trước đó.

Khi mất mạng, tiếp tục đếm giờ, áp luật cache và xếp event vào SQLite; Tray báo offline và lần đồng bộ gần nhất. Token hết hạn khi offline không làm đặt lại quota hoặc làm mất policy hợp lệ.

Khi có mạng, thứ tự xử lý là **kiểm tra vòng đời/thu hồi/xóa -> làm mới xác thực nếu còn quyền -> đồng bộ policy -> gửi dữ liệu hợp lệ theo lô -> nhận ACK**. Mỗi event có khóa chống trùng trong phong bì đồng bộ; Server nhận lại vẫn chỉ tính một lần. Bản cục bộ chỉ rời hàng đợi khi đã có ACK hoặc bị xóa theo yêu cầu/retention.

**Quyết định thiết kế OFF-01:** chọn fail-open sau **7 ngày** mất liên lạc liên tục, dừng thu thập và hạn chế thay vì áp vô hạn một policy cũ; Tray giải thích rõ. Trước mốc này vẫn thực thi cache, luôn giữ ngoại lệ an toàn. Đây là giá trị N do thiết kế lựa chọn theo mục 2.3 của [S1], không phải thông số bắt buộc từ đề. Khi kết nối lại phải kiểm tra liên kết trước khi tiếp tục quản lý.

## Phần 4: Luồng Dữ liệu & Quản trị Vòng đời

### 4.1. Luồng trẻ xin thêm giờ

```text
Tray -> IPC -> Service -> HTTPS -> Server -> Dashboard
                                  ^             |
                                  |     Duyet/Tu choi + audit
                                  |             v
Tray <- IPC <- Service <- WebSocket <- Server
                 |
                 +---- ACK Applied ------------>
```

1. Trẻ chọn “Xin thêm giờ”; Tray gửi số phút và request_id qua IPC. Service tự xác minh SID/binding của bên gọi.
2. Service gửi HTTPS; Server kiểm token và liên kết, lưu yêu cầu `Pending`, trả mã yêu cầu. Khi offline, Tray báo “Chưa gửi được” trong **≤5 giây**, giữ yêu cầu chờ và gửi lại khi có mạng trong **≤60 giây** nếu chưa hết hạn.
3. Dashboard chỉ hiện yêu cầu cho phụ huynh sở hữu hồ sơ. Phụ huynh duyệt, ví dụ thêm **15 phút**, hoặc từ chối kèm giải thích.
4. Server lưu quyết định, audit và lệnh trong giao dịch/outbox, sau đó đẩy WSS tới đúng binding.
5. Service kiểm command_id, chữ ký/xác thực, thời hạn và đích. Cập nhật quota cùng kết quả chống lặp trong giao dịch cục bộ; nhận lặp không cộng thêm lần nữa.
6. Service gửi ACK `Applied`; Tray hiển thị “Đã được thêm 15 phút” cùng thời gian còn lại. Dashboard chỉ báo đã áp dụng sau ACK.

Trạng thái: `QueuedOffline -> Pending -> Approved -> Applied`, hoặc `Rejected/Expired`. Yêu cầu thêm giờ hết hạn sau **30 phút** không có quyết định; gửi lại không đặt lại thời hạn. Báo chặn nhầm dùng luồng tương tự, hết hạn sau **7 ngày** nếu chưa xử lý theo [S2].

Cộng quota không mặc nhiên mở ngoài lịch tuần; ngoại lệ lịch phải có phạm vi và hạn riêng. Yêu cầu được gửi qua Tray trong phiên người dùng, đặc biệt trước khi khóa; không giả định Tray tương tác được trên secure desktop của Windows khi phiên đã khóa.

### 4.2. Mô hình xóa ba mức

Ba thao tác là ba ý định nghiệp vụ độc lập. Server kiểm quyền sở hữu, CSRF và yêu cầu xác nhận hai bước; xóa hồ sơ yêu cầu xác thực lại. Mỗi việc có operation_id, phạm vi và trạng thái thực thi riêng trên Server/Agent.

| Mức | Phạm vi | Phía Server | Phía Agent | Kết quả |
|---|---|---|---|---|
| **1 - Xóa dữ liệu** | Lịch sử của hồ sơ; cache tương ứng trên các liên kết | Xóa event, tổng hợp, bản xuất và cache báo cáo; tăng data_epoch; gửi lệnh dọn cache | Xóa lịch sử, hàng đợi cũ, cache báo cáo/DNS có liên quan; cập nhật epoch; ACK | Giữ ghép nối, policy và token; tiếp tục quản lý và ghi dữ liệu mới |
| **2 - Xóa ghép nối** | Binding trẻ-SID-thiết bị được chọn | Đánh dấu Revoked, thu hồi access/refresh token và phiên WSS; hủy yêu cầu/lệnh chờ; giữ báo cáo đã nhận | Ngừng thu thập/thực thi đúng SID; xóa credential, policy và dữ liệu chưa gửi của binding; tháo quy tắc liên quan; ACK | Thiết bị/SID ngắt quản lý; báo cáo cũ còn đến hết thời hạn 90 ngày |
| **3 - Xóa hồ sơ** | Toàn hồ sơ trẻ và mọi binding trên các thiết bị | Chặn ghi mới; thu hồi mọi token; xóa hồ sơ, policy riêng, event, báo cáo, yêu cầu, dữ liệu dẫn xuất và audit có dữ liệu cá nhân; tạo lệnh dọn từng Agent | Ngừng quản lý; xóa toàn bộ cache, policy, bộ đếm, token và khóa dữ liệu riêng của hồ sơ; tháo quy tắc; ACK | Hồ sơ và báo cáo bị loại bỏ; các thiết bị hoàn tất xóa khi có ACK |

Ở Mức 1, **cache hoạt động** được xóa nhưng cấu hình vận hành tối thiểu gồm policy, credential, quota hiện hành và checkpoint chống lùi giờ được giữ. Không biến thao tác xóa lịch sử thành cách nhận lại quota. Muốn kết thúc cả cấu hình quản lý phải dùng Mức 2/3.

Ở Mức 2, báo cáo được giữ không có nghĩa lưu vĩnh viễn: tác vụ retention 90 ngày vẫn chạy. Ghép lại tạo binding/token mới; không tải hàng đợi cũ vào liên kết mới.

Mức 3 chỉ giữ biên nhận kỹ thuật tối thiểu cần giao lệnh, chống tái tạo dữ liệu và nhận ACK; biên nhận không chứa tên, SID thô hay lịch sử hoạt động. Không giữ audit cũ như một bản sao báo cáo sau khi tuyên bố xóa hồ sơ.

### 4.3. Máy ngoại tuyến, thu hồi token và chống dữ liệu sống lại

**LIFE-01.** Không trì hoãn thu hồi token quản lý để chờ máy offline. Tại enrollment, cấp một khóa/định danh dọn dẹp riêng, bảo vệ bằng DPAPI. Kênh vòng đời này chỉ nhận lệnh xóa/thu hồi đã ký và gửi ACK; không đọc báo cáo, gửi event, gia hạn token hoặc khôi phục liên kết.

**LIFE-02.** Agent kiểm tra kênh vòng đời trước upload khi kết nối lại. Server từ chối event của binding đã thu hồi hoặc data_epoch cũ; metadata epoch nằm ngoài bảy trường event. Với Mức 1, bắt đầu phiên dữ liệu mới; với Mức 2/3, dừng upload hoàn toàn.

**LIFE-03.** Trạng thái hiển thị tối thiểu: `ServerApplied`, `AgentPending`, `AgentApplied`, `Completed`. Máy offline ghi “Đã xử lý trên Server - chờ xóa trên thiết bị”. Chỉ chuyển Completed khi mọi Agent trong phạm vi đã ACK. Mục tiêu xóa cục bộ là **≤5 giây khi online**; offline xử lý ngay khi kết nối lại, không tuyên bố xóa tức thời trên thiết bị chưa nhận lệnh.

**LIFE-04.** Xóa có tính idempotent; lệnh phát lại chỉ trả kết quả cũ. Dọn SQLite phải xét WAL/journal, tệp tạm, cache bộ nhớ và khóa mã hóa riêng của hồ sơ. Không tuyên bố bảo đảm ghi đè vật lý mọi bản sao trên SSD. Nếu khôi phục backup, phải áp lại quyết định xóa trước khi mở nhận dữ liệu.

### 4.4. Gỡ cài đặt và bảo toàn Windows

Uninstaller dừng Service/Tray, gỡ đăng ký khởi động và chỉ tháo quy tắc/cấu hình do OpenGuard Kids sở hữu. Khôi phục cấu hình mạng phải xét thay đổi hợp lệ phát sinh sau cài đặt, không ghi đè mù quáng một bản sao cũ.

Xóa liên kết chỉ ảnh hưởng binding đích; các liên kết khác tiếp tục chạy. Gỡ toàn Agent là thao tác cấp máy, phải thông báo sẽ dừng quản lý mọi liên kết trên máy, nhưng không thay đổi Windows account/profile, mật khẩu, nhóm quyền hoặc tệp của bất kỳ người dùng nào. Gỡ phần mềm không tự động xóa báo cáo Server; việc đó dùng thao tác dữ liệu tương ứng trên Dashboard.

## Phần 5: Cấu trúc Thư mục Dự án

### 5.1. Lựa chọn Monorepo

Chọn Monorepo với bốn package nguồn độc lập: A-Agent, B-Server, C-Giao diện và hợp đồng dùng chung. Bố trí theo `src` layout, tách phụ thuộc Windows khỏi môi trường Server; không import pywin32/pystray khi chỉ chạy web. Server và bộ cài Agent là hai đơn vị phát hành riêng.

Cây thư mục được thiết kế và khởi tạo trong thư mục làm việc như sau; các thư mục mô-đun sẽ nhận mã nguồn ở giai đoạn triển khai:

```text
OpenGuardKids/                         # ten logic cua kho nguon
|-- README.md
|-- PRIVACY.md
|-- .gitignore
|-- docs/
|   |-- architecture/
|   |   `-- SYSTEM_ARCHITECTURE_SRS.md
|   |-- adr/                           # quyet dinh ky thuat
|   `-- api/                           # OpenAPI, IPC, WS contracts
|-- packages/
|   |-- agent/                         # A - Windows Service
|   |   |-- README.md
|   |   `-- src/ogk_agent/
|   |       |-- service/               # A1: SCM, startup/shutdown
|   |       |-- time_control/          # A2: quota, lich, idle
|   |       |-- app_control/           # A3: SID + SHA-256
|   |       |-- dns/                   # A4: proxy, SafeSearch
|   |       |-- storage/               # A5: SQLite, queue, checkpoint
|   |       |-- policy/                # A6: xac minh va thuc thi
|   |       |-- windows/               # SID, WTS, session helper
|   |       |-- ipc/                   # named pipe server
|   |       |-- transport/             # HTTPS, WSS, pinning
|   |       `-- lifecycle/             # enroll, purge, revoke
|   |-- server/                        # B - FastAPI
|   |   |-- README.md
|   |   `-- src/ogk_server/
|   |       |-- api/
|   |       |   |-- routes/            # auth, enroll, policy, events...
|   |       |   |-- dependencies/      # auth context, RBAC, ownership
|   |       |   `-- web/               # route render Jinja2 cua C
|   |       |-- db/
|   |       |   |-- models/            # SQLAlchemy models
|   |       |   `-- migrations/        # phien ban schema
|   |       |-- services/              # nghiep vu B1-B6, transactions
|   |       |-- security/              # Argon2id, token, CSRF, signing
|   |       |-- realtime/              # WebSocket, outbox, ACK
|   |       `-- jobs/                  # retention va het han
|   |-- interfaces/                    # C - UI
|   |   |-- README.md
|   |   `-- src/ogk_ui/
|   |       |-- tray/                  # C2/C4: pystray, IPC client
|   |       |   |-- dialogs/           # enroll, xin gio, ly do chan
|   |       |   `-- assets/            # icon dong goi cuc bo
|   |       `-- web/
|   |           |-- templates/
|   |           |   |-- parent/        # C1: Dashboard phu huynh
|   |           |   |-- child/         # C3: Child View
|   |           |   `-- shared/        # layout, components
|   |           `-- static/            # CSS, JS, images, vendor
|   `-- contracts/                     # schema dung chung A/B/C
|       |-- README.md
|       `-- src/ogk_contracts/
|           |-- events/                # 7 truong + envelope
|           |-- policy/
|           |-- ipc/
|           `-- commands/
|-- data/
|   |-- classification/                # C5: >=200 muc, 6 nhom
|   `-- test_domains/                  # 50 chan, 20 giao duc, DoH
|-- tests/                             # C6: QA chung ca nhom
|   |-- unit/
|   |-- integration/
|   |-- e2e/
|   |-- security/
|   `-- evidence/                      # ket qua do, khong event that
|-- packaging/
|   |-- pyinstaller/                   # build Service/Tray
|   `-- inno_setup/                    # mot setup .exe
`-- scripts/                           # build va ho tro dev/test
```

Tên `OpenGuardKids/` ở sơ đồ là tên logic của kho; thư mục thực tế là **`E:\Mạng Máy Tính`**. Các README chỉ rõ ranh giới phân hệ; cây khởi tạo chưa bao gồm mã chạy hay bộ cài.

### 5.2. Nguyên tắc phụ thuộc và sở hữu

| Vùng | Phụ trách theo [S2] | Quy tắc |
|---|---|---|
| `packages/agent` | A1-A6, Nam | Chỉ mã SYSTEM và adapter Windows; dùng contracts, không dùng DB Server |
| `packages/server` | B1-B6, Ân | Routes mỏng, nghiệp vụ trong services, model trong db; kiểm quyền ở Server |
| `packages/interfaces` | C1-C4, Thịnh | Tray chạy User; Jinja2 được Server render qua PackageLoader/package resources |
| `data/classification`, `data/test_domains` | C5, Thịnh | Danh sách an toàn có nguồn và nhãn kỳ vọng; không dữ liệu trẻ thật |
| `tests` | C6, Thịnh điều phối; cả nhóm kiểm thử | REQ -> Task -> TC -> minh chứng; không khẳng định đạt trước khi đo |
| `packages/contracts` | Cả nhóm chốt | Schema/hằng số; không DB, mạng, GUI hoặc secret |

Luồng phụ thuộc: **A -> contracts; B -> contracts; C -> contracts; B nạp tài nguyên web của C**. Tray không import package Agent để gọi chức năng đặc quyền; chỉ đi qua IPC đã xác thực. Khai báo phụ thuộc web và tray tách riêng khi bổ sung cấu hình đóng gói; mỗi đơn vị phát hành khóa phiên bản và ghi giấy phép thư viện.

Các mô-đun triển khai dự kiến gồm `service/windows_service.py`, `api/routes/auth.py`, `api/routes/enroll.py`, `api/routes/lifecycle.py`, `db/models/binding.py`, `db/models/event.py` và `tray/main.py`. Tên tệp là hợp đồng tổ chức mã nguồn, không thay thế kiểm thử hành vi.

### 5.3. Dữ liệu chạy thật tại ProgramData

```text
%ProgramData%\OpenGuardKids\
|-- state\
|   `-- agent.db                       # policy, binding, quota, queue
|-- keys\                             # khoa duoc bao ve boi DPAPI
`-- logs\                             # log ky thuat toi gian
```

Vị trí mặc định tương ứng `C:\ProgramData\OpenGuardKids\state\agent.db`; dùng biến môi trường/Windows API thay vì hardcode ổ C. DB chứa nhiều binding phải tách phạm vi bằng khóa liên kết; không xóa toàn tệp DB khi chỉ xóa một trẻ. Tray đọc trạng thái qua IPC, không cấp quyền ghi vùng ProgramData cho User.

DB Server dùng volume dữ liệu riêng ngoài kho nguồn, được cấu hình bởi biến môi trường. Không đặt DB, token, khóa riêng, log thật trong thư mục `data/`, `packages/` hay Git. Khi phát triển có thể dùng dữ liệu giả ở vùng runtime bị `.gitignore` loại trừ.

## Phần 6: Yêu cầu Bảo mật & Quyền riêng tư - F5

### 6.1. Lược đồ event đúng bảy trường

| Trường | Ý nghĩa | Ràng buộc |
|---|---|---|
| `ts` | Thời điểm phát sinh | Chuẩn hóa UTC; không đổi khi gửi lại |
| `device_id` | Thiết bị nguồn | Khớp credential đã xác thực |
| `child_id` | Hồ sơ trẻ | Khớp binding; không tin payload tự khai |
| `type` | Loại sự kiện | Chỉ chín giá trị liệt kê dưới đây |
| `subject` | Tên app hoặc tên miền đăng ký/cấp hai | Không URL, path tệp, query, tên cửa sổ hoặc nội dung |
| `duration_sec` | Thời lượng | Số không âm; 0 nếu sự kiện không đo khoảng thời gian |
| `policy_id` | Chính sách tại thời điểm phát sinh | Tham chiếu hợp lệ trong phạm vi được quản lý |

```text
app_start, app_stop, domain_query, blocked_app, blocked_domain,
quota_warning, locked, unlock_request, override_granted
```

Server từ chối trường thừa, loại sai và dữ liệu ngoài phạm vi. Idempotency key, binding_id, data_epoch và phiên bản giao thức thuộc **phong bì batch/header**, không thêm thành trường thứ tám của event. Khi queue lưu ID nội bộ phục vụ giao dịch, đó là metadata lưu trữ, không mở rộng payload hoạt động.

Thông tin request/command/audit có schema riêng vì mục đích vận hành; không dùng chúng để lưu dữ liệu bị cấm. Domain query không chứa từ khóa tìm kiếm; đường dẫn tệp chỉ dùng tạm cục bộ để tính SHA-256, không đưa vào subject.

### 6.2. Retention và quyền xem

Event, báo cáo, yêu cầu và audit cá nhân lưu tối đa **90 ngày** tính từ thời điểm phát sinh, kể cả thời gian ở cache ngoại tuyến. Tác vụ retention chạy hằng ngày **02:00** theo múi giờ vận hành và khi Server khởi động; không phục vụ bản ghi đã quá hạn trong lúc chờ job dọn. Cache, bản tổng hợp và bản xuất tuân theo cùng thời hạn.

Thao tác xóa áp dụng theo phần 4; Mức 2 không kéo dài retention. Trường dữ liệu không giải thích được mục đích phải loại bỏ. [PRIVACY.md](../../PRIVACY.md) ánh xạ **trường -> mục đích -> thời hạn -> người được xem**; dữ liệu vận hành đang hoạt động và biên nhận dọn dẹp có vòng đời riêng được công bố, không dùng để giữ lịch sử quá 90 ngày.

### 6.3. Mã hóa và quản lý khóa

**SEC-01.** Kênh HTTPS/WSS dùng **TLS 1.2+**; Agent thực hiện **certificate pinning**, từ chối khi chứng chỉ/pin không khớp. CA tự ký chỉ dùng trong lab với trust/pin cấu hình rõ; không tắt xác minh. Có kế hoạch xoay chứng chỉ với pin dự phòng được phân phối tin cậy.

**SEC-02.** Mã hóa trường nhạy cảm bằng **AES-GCM**; nonce không trùng dưới cùng khóa, kiểm tra authentication tag trước khi đọc. Khóa Agent được bảo vệ bằng **DPAPI** và ACL; khóa Server lấy từ cấu hình bí mật/biến môi trường, không hardcode hay commit. AES-GCM bảo vệ dữ liệu; DPAPI bảo vệ khóa.

**SEC-03.** Token dài hạn/bí mật thiết bị phía Server được băm khi chỉ cần kiểm tra đối chiếu; không lưu log mật khẩu, mã enrollment, token hoặc khóa. Chữ ký/HMAC policy phải bao phủ nội dung, version và đối tượng áp dụng. Agent từ chối policy sửa trái phép, sai đích hoặc hạ version ngoài quy trình khôi phục hợp lệ.

### 6.4. Xác thực phụ huynh và Agent

- Dùng **Argon2id** với salt riêng và tham số được hiệu chỉnh cho môi trường; đây là lựa chọn cụ thể từ hai phương án Argon2id/scrypt của [S1].
- Mật khẩu tối thiểu **10 ký tự**; sau **5 lần sai**, khóa tạm **15 phút**; rate limit theo IP và tài khoản. TOTP là phần mở rộng.
- Cookie Dashboard: **HttpOnly, Secure, SameSite=Lax**; phiên tối đa **12 giờ**, đăng xuất hủy phía Server; mọi thao tác ghi có CSRF.
- Access token Agent hạn **15 phút**, gắn device/binding và phạm vi; refresh token có hạn cấu hình, luân chuyển khi dùng và bị thu hồi khi phát hiện dùng lại.
- Server kiểm trạng thái liên kết/thu hồi trên mọi đường API/WSS; không chỉ kiểm chữ ký JWT rồi chờ hết 15 phút. Không đưa token vào URL hoặc Tray.
- Cơ chế dọn dẹp phần 4 không phải refresh token dự phòng và không được phục hồi quyền truy cập đã thu hồi.

### 6.5. RBAC, IDOR và đặc quyền cục bộ

| Vai trò | Quyền |
|---|---|
| `parent` | Quản lý hồ sơ, binding, policy và dữ liệu thuộc quyền sở hữu |
| `child-view` | Chỉ xem số liệu/luật của mình và gửi yêu cầu; không tự duyệt hoặc sửa policy |
| `admin` | Quản trị kỹ thuật theo quyền tối thiểu; không mặc nhiên đọc báo cáo gia đình |

Mọi truy vấn dữ liệu kiểm tra **vai trò + quyền sở hữu + trạng thái liên kết** ở tầng Server. Thay child_id/device_id/binding_id trên URL hoặc JSON không được truy cập gia đình khác; kiểm tra này áp dụng cả báo cáo xuất, WebSocket, lệnh và ba mức xóa. Chỉ ẩn nút giao diện không phải biện pháp chống IDOR.

Service kiểm tra SID người gọi IPC từ Windows, không cấp API chạy lệnh shell, đọc tệp tùy ý hoặc sửa policy theo payload của Tray. Các hành động đặc quyền được giới hạn thành tập thao tác cụ thể. Cấu hình/khóa/DB Agent chỉ SYSTEM và Administrators được ghi; tài khoản trẻ là tài khoản chuẩn.

### 6.6. Kiểm toán và điều kiện nghiệm thu cuối

Audit thay đổi luật/cộng giờ ghi **ai, thời điểm, IP, giá trị cũ và mới**. Child View hiển thị thay đổi liên quan đến trẻ và ẩn IP. Audit cá nhân vẫn chịu retention/xóa; biên nhận kỹ thuật tối thiểu của việc xóa không chứa lịch sử hoạt động.

| Nhóm | Bằng chứng cần có |
|---|---|
| Cài đặt và minh bạch | Một setup .exe; Service SYSTEM, Tray User; thông báo rõ; mất Tray thì dừng kiểm soát |
| Enrollment | Mã 8 ký tự/10 phút, một lần; không đăng nhập phụ huynh trên máy trẻ |
| Thời gian | Quota 3 phút; lịch 7x48; ngoài lịch ≤5 giây; ân hạn 60 giây; idle >5 phút; sai số ±30 giây/giờ; lùi 3 giờ không tăng quota |
| Nội dung | SHA-256 chống đổi tên; DNS ≥48/50; giáo dục 0/20; SafeSearch; ≥200 mục/6 nhóm; thử 5 điểm cuối DoH |
| Đồng bộ | Heartbeat 60 giây; policy ≤60 giây; lệnh khẩn <5 giây; gửi lại không đếm/cộng hai lần |
| SID | Hai tài khoản trên cùng máy: app/phiên đúng trẻ bị tác động, tài khoản khác không; thử cả chuyển nhanh người dùng |
| Vòng đời | Ba mức xóa, thu hồi token ngay, offline nhận xóa trước upload; không sống lại dữ liệu; không xóa Windows account/profile |
| F5 | Bảy trường/chín loại; retention 90 ngày; pin sai bị từ chối; IDOR bị chặn; AES-GCM/DPAPI và Argon2id đúng cấu hình |

Các giới hạn độ trễ được đo trong điều kiện trực tuyến xác định, từ lúc Server tiếp nhận thao tác hợp lệ đến lúc Agent thực thi/xác nhận; kết quả phải ghi điều kiện mạng và số đo. Đối với quota, so sánh mốc khóa với thời gian sử dụng thực. Không còn lỗi Critical/High chưa xử lý trước nghiệm thu.

Năm quyết định kiến trúc cần ghi riêng tại `docs/adr/` khi triển khai gồm: thời hạn offline/fail-open, mốc ân hạn, IPC, DNS theo SID/DoH/cache và toàn vẹn/chống replay. Nguồn kỹ thuật bổ sung chỉ làm rõ cơ chế Windows/SafeSearch; không thay thế yêu cầu và ngưỡng của [S1], [S2].
