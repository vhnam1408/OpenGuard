# Phân hệ A - Agent

Package Python dự kiến: `ogk_agent`, tổ chức theo `src` layout. Phân hệ này chạy trên Windows, dùng pywin32/psutil và không chứa giao diện người dùng.

| Thư mục trong `src/ogk_agent/` | Trách nhiệm |
|---|---|
| `service/` | A1: vòng đời Windows Service, SCM, khởi tạo và dừng an toàn |
| `time_control/` | A2: quota, lịch 7x48, idle, cảnh báo và chống lùi giờ |
| `app_control/` | A3: nhận diện SHA-256 và kiểm tra SID trước khi chặn tiến trình |
| `dns/` | A4: DNS Proxy 127.0.0.1:53, SafeSearch, cache theo phạm vi |
| `storage/` | A5: SQLite, checkpoint, hàng đợi và mã hóa dữ liệu cục bộ |
| `policy/` | A6: xác thực và thực thi chính sách có phiên bản |
| `windows/` | Ánh xạ SID/phiên, quyền truy cập và thành phần hỗ trợ khóa phiên |
| `ipc/` | Named pipe server và kiểm tra danh tính phía gọi |
| `transport/` | HTTPS, heartbeat 60 giây, WSS, pinning và ACK |
| `lifecycle/` | Enrollment, xóa dữ liệu, ngắt liên kết và xóa hồ sơ |

DB chạy thật đặt tại `%ProgramData%\OpenGuardKids\state\agent.db`, không đặt trong kho nguồn. Chỉ SYSTEM/Administrators được ghi. Tray không truy cập DB hay token Agent trực tiếp.

Đã triển khai các thành phần nền tảng bên dưới. Ràng buộc nghiệp vụ và nghiệm thu nằm trong [SRS](../../docs/architecture/SYSTEM_ARCHITECTURE_SRS.md), đặc biệt Phần 3 và danh mục cấm 1.3.

## Thành phần đã có

- `service/windows_service.py`: Windows Service tên **OpenGuard Kids Agent**, tài khoản LocalSystem, tự khởi động; SCM restart sau lỗi với các khoảng 5/15/60 giây. Dừng bình thường không kích hoạt recovery. Lỗi worker được đưa về pywin32 sau cleanup để SCM nhận trạng thái lỗi.
- `ipc/server.py`: Named Pipe `\\.\pipe\OpenGuardKids.Tray.v1`, ACL SYSTEM/Administrators và quyền đọc/ghi cụ thể cho Interactive User; từ chối kết nối mạng. Xác thực SID/phiên của token Windows bằng impersonation rồi luôn revert; không nhận SID tự khai. Dùng IPC v1 từ `ogk_contracts`, giới hạn frame 64 KiB, số kết nối, tốc độ request và hàng đợi.
- `time_control/counter.py`: `time.monotonic()`, quota theo binding, lịch 7×48, loại trừ phần idle vượt 300 giây và phiên khóa/ngủ/không hoạt động. Cảnh báo ở 600/300/60 giây, không phát bù mốc không thể đạt. **60 giây lưu bài nằm trong quota**, nên quota 180 giây yêu cầu khóa ở giây 180. Khoảng lấy mẫu quá 5 giây bị bỏ để không tính thời gian ngủ hoặc trạng thái không biết.
- `storage/checkpoints.py`: SQLite WAL, giao dịch checkpoint với `synchronous=FULL` trước khi trả cảnh báo/yêu cầu khóa; giữ thời lượng, ngày ngân sách, đồng hồ tin cậy và các mốc đã báo khi restart. Lùi giờ không giảm thời lượng hoặc lùi ngày ngân sách. Trong một lần chạy, thời gian lịch tiến theo monotonic; sau restart lấy mốc lớn hơn giữa checkpoint và UTC hiện tại. Không tuyên bố phát hiện được mọi chỉnh giờ tiến hoặc chỉnh giờ xuyên reboot khi không có nguồn thời gian tin cậy bên ngoài.
- `windows/sessions.py`: lấy SID thực qua `WTSQueryUserToken`, chỉ dùng các trường số idle/lock của `WTSSessionInfoEx`. Không gọi `GetLastInputInfo` trong Session 0 để suy ra hoạt động của trẻ; không đọc phím gõ.
- `dns/proxy.py`: bind riêng loopback `127.0.0.1:53`, UDP và TCP, timeout hữu hạn, xác minh ID/question của upstream và chuyển sang TCP khi UDP bị truncated. Chặn theo nhãn tên miền bằng NXDOMAIN; ngoại lệ an toàn trước luật thường; thông báo có lý do/người đặt luật, giới hạn một lần/miền/binding/policy mỗi 60 giây. Google SafeSearch dùng CNAME `forcesafesearch.google.com` và phân giải A/AAAA từ upstream đã cấu hình; HTTPS/SVCB không trả IP hint chưa lọc. Không lưu cache câu trả lời DNS hay log truy vấn.
- `app_control/blocker.py`: dùng psutil liệt kê PID, xác minh token SID/phiên, bỏ qua tiến trình critical/thiết yếu, giữ handle tiến trình ổn định và handle tệp không cho ghi/thay thế trong lúc băm. Luật chứa tên cùng SHA-256; hash quyết định danh tính, nên đổi tên vẫn bị nhận ra. Cache có TTL và khóa theo định danh/kích thước/timestamp tệp. Kiểm tra lại handle và điều kiện thực thi trước khi kết thúc; ngoại lệ hash an toàn/allow được xét trước deny. Callback chỉ nhận basename/hash và giải thích, không nhận đường dẫn.

Không có mã chụp màn hình, ghi phím, đọc nội dung cá nhân, ẩn tiến trình/dịch vụ, tự cài đặt hoặc chống gỡ. Chỉ kiểm thử bằng tài khoản/hồ sơ giả và miền trung tính.

## Cài package và chạy kiểm thử

Từ thư mục gốc repo, với Python 3.11+ trên Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\packages\agent
.\.venv\Scripts\python.exe -m unittest discover -s packages/agent/tests -v
.\.venv\Scripts\python.exe -m unittest discover -s packages/interfaces/tests -v
```

Test dùng dữ liệu giả trong `runtime/agent-tests`, DNS ở cổng loopback ngẫu nhiên và Named Pipe có tên ngẫu nhiên. Test SCM dùng mock; không cài dịch vụ, đổi DNS card mạng, khóa phiên hoặc kết thúc tiến trình thật. Test Named Pipe cấp thêm quyền tạo pipe cho tài khoản chạy fixture; ACL production vẫn chỉ cho SYSTEM/Administrators tạo server.

## Cài dịch vụ trong môi trường lab được phép

Cài package và pywin32 vào môi trường triển khai **chỉ Administrators/SYSTEM được ghi**, theo hướng dẫn triển khai pywin32. Không chạy Service SYSTEM từ một checkout/venv mà tài khoản trẻ có thể sửa. Bộ cài cần tạo trước `%ProgramData%\OpenGuardKids\state` với ACL chỉ SYSTEM/Administrators được ghi, kể cả WAL/journal, rồi truyền đường dẫn `agent.db` cho checkpoint store. Mã nguồn không tự tạo quyền rộng cho User.

Trong PowerShell Administrator, dùng Python của môi trường triển khai:

```powershell
python -m ogk_agent.service.windows_service install
python -m ogk_agent.service.windows_service start
python -m ogk_agent.service.windows_service stop
python -m ogk_agent.service.windows_service remove
```

`install`/`update` cấu hình LocalSystem, auto-start và recovery. `start` kiểm tra token LocalSystem; không tự nâng quyền. Tray vẫn chạy riêng trong phiên User. Việc viết mã/kiểm thử không đồng nghĩa đã cài hoặc nghiệm thu Service SYSTEM.

## Điểm tích hợp bắt buộc

`AgentRuntime` hiện trả `managed=false` và từ chối xin giờ khi chưa ghép nối. Đây là khung dịch vụ chạy được, **chưa phải Agent hoàn chỉnh đã nối enrollment/policy/transport**. Chưa khởi động counter, blocker hay DNS proxy cho tài khoản chưa liên kết.

Khi tích hợp một binding đã xác minh:

1. Nạp policy có chữ ký/HMAC, kiểm tra đích và version; lấy SID/phiên thực từ Windows. Mỗi binding chỉ có một worker sở hữu counter. Chọn/cập nhật quota ngày thường/cuối tuần từ policy tại lần đổi ngày; checkpoint hiện lưu số đã dùng, không tự lưu lệnh cộng giờ hay chống replay lệnh.
2. Poll `WindowsSessionSampler` khoảng 1 giây. Khi suspend/resume, đánh dấu mẫu `suspended` và bỏ khoảng chuyển tiếp. Không dùng dữ liệu idle tự khai qua IPC. Lỗi lấy mẫu hoặc mất Tray phải dừng thu thập/thực thi cho phiên đó.
3. `PresenceRegistry.is_present(ClientIdentity(sid, session))` hết hạn sau 5 giây và bị xóa ngay khi pipe disconnect. Cổng thực thi của blocker/DNS phải kiểm tra cả binding còn hiệu lực, policy hợp lệ và lease này. Lease là tín hiệu sống từ Tray; chưa phải bằng chứng xác thực tệp Tray hoặc chứng minh Explorer đang vẽ icon. Lớp tích hợp cần kiểm tra nguồn Tray đã triển khai tin cậy khi quyết định cho phép quản lý.
4. Chuyển `CounterResult.warnings` thành message `quota_warning`, thêm `reason`, `rule_set_by`, `policy_id`, `remaining_seconds`. `lock_required` là yêu cầu khóa; gọi thành phần hỗ trợ trong **đúng phiên tương tác**, rồi đối soát trạng thái khóa. Không gọi `LockWorkStation` từ Service Session 0. Gửi cảnh báo/chặn qua `pipe.presence.publish()`; callback phải enqueue nhanh và báo thất bại nếu không có kênh nhận.
5. Cấp hash an toàn của Tray/thành phần trợ giúp và các ứng dụng khẩn cấp cho blocker. `record` phải tạo đúng event bảy trường trong contracts và lưu hàng đợi bền vững; dataclass thông báo của blocker/DNS không phải event gửi Server.
6. Chỉ truyền `DnsScope` từ lớp định tuyến/phân giải theo người dùng đã xác thực. **Không suy SID từ IP loopback, tài khoản foreground hoặc PID DNS Client.** Truy vấn chưa chứng minh nguồn chỉ chuyển tiếp, không áp luật/ghi báo cáo trẻ. Phải chứng minh cô lập cả cache DNS dùng chung trước khi bật định tuyến quản lý.
7. DNS yêu cầu upstream IP được quản trị cấu hình và danh sách an toàn bắt buộc có nguồn (trường học, trợ giúp khẩn cấp); policy chỉ được bổ sung ngoại lệ, không xóa danh sách này. `google_search_domains` là tập tên Google được phê duyệt, bao gồm các miền quốc gia cần quản lý; không dùng wildcard. Nếu kênh giải thích thất bại, truy vấn deny được chuyển tiếp thay vì chặn im lặng.
8. Proxy không thay DNS adapter/hosts nên chưa có cấu hình mạng do nó sở hữu cần hoàn nguyên. Lớp định tuyến sau này phải ghi nhận cấu hình sở hữu và tháo ngay khi mất Tray/dừng/gỡ, không ghi đè thay đổi mạng hợp lệ khác. Supervisor cần gọi `check_health()` của pipe/DNS và đưa lỗi worker về Service để SCM recovery có hiệu lực.

Đã bổ sung `storage/offline_queue.py`: queue SQLite WAL bền vững tại đường dẫn ProgramData mặc định, batch tối đa 100 event, key chống trùng giữ nguyên qua restart/retry, chỉ xóa sau ACK đúng batch, kiểm epoch/thu hồi và retention. Có `HTTPSBatchUploader`, `sync_once` và worker `sync_forever`; callback bắt buộc kiểm vòng đời/xác thực/policy trước upload. Xem [hợp đồng đồng bộ và ví dụ tích hợp](../../docs/api/REALTIME_SYNC.md).

Chưa triển khai trong phạm vi này: enrollment/DPAPI, xác minh policy, nối transport vào AgentRuntime, bộ nhận/chống replay lệnh tại Agent, khóa phiên tương tác, định tuyến DNS theo SID, bộ phân loại 200 mục, kiểm thử DoH và các tiêu chí nghiệm thu tích hợp/độ trễ. Không cam kết chống mọi DoH/VPN/proxy.

Tham chiếu kỹ thuật: [Google SafeSearch](https://support.google.com/websearch/answer/186669?hl=en), [Microsoft Named Pipe ACL](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights), [Microsoft giới hạn của GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo), [WTSINFOEX](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w), [pywin32 service framework](https://github.com/mhammond/pywin32/blob/main/win32/Lib/win32serviceutil.py).
