# ViDental Imaging Agent 2

F2: bộ kết nối TWAIN chạy trên máy Windows cắm thiết bị; thiết lập ngay trong web ViDental.
Camera USB/webcam vẫn chạy trực tiếp trong trình duyệt, không cần agent.

## Cài từ Git trên máy phòng khám (CMD)

Kho public: https://github.com/Thanh097698/agent — Agent **2.1.0**, giao thức **v2**.

Đọc [hướng dẫn từng bước cho người không chuyên](INSTALL.md), gồm cách mở CMD,
cài Git/Python, tải mã, cài thư viện, cài driver và ghép nối với web.

Cài Git và Python 3.13+ có Tkinter trước. Python phải cùng loại 32-bit hoặc 64-bit
với driver TWAIN; chưa biết hãng/model thì xác nhận với nhà cung cấp trước khi chạy
thiết bị thật. Sau đó nhấn Windows + R, nhập `cmd`, Enter và chạy từng dòng:

```bat
cd /d "%USERPROFILE%"
if not exist ViDental mkdir ViDental
cd ViDental
git clone https://github.com/Thanh097698/agent.git clinic-imaging-agent
cd clinic-imaging-agent
python -m venv .venv-clinic
.venv-clinic\Scripts\python.exe -m pip install -r requirements.txt
.venv-clinic\Scripts\python.exe agent.py
```

Giữ CMD và Agent chạy. Web mặc định được cho phép: `https://app.vidental.vn`.
Nếu dùng origin khác, xem `--allowed-origins` trong [INSTALL.md](INSTALL.md).
Chưa có sensor có thể chạy với `--mock` để kiểm tra cài đặt; đây là ảnh giả lập.

## Kết nối với web sau khi cài

Nếu cài từ Git ở trên, bỏ qua bước bộ cài bên dưới. Bộ cài chỉ dùng khi quản trị
đã phát hành riêng; người dùng kho này có thể chạy trực tiếp từ mã nguồn.

1. Cài driver của hãng. Chọn bộ cài agent cùng loại **32-bit hoặc 64-bit với driver**, không suy từ loại Windows.
2. Thoát bản agent cũ nếu có. Chạy bộ cài ViDental do người quản trị phát hành. Không cần cài Python, không cần quyền quản trị Windows cho chính agent; driver của hãng có thể cần quyền quản trị.
3. Web → Cấu hình → Tích hợp → Thiết bị hình ảnh → thêm/lưu thiết bị TWAIN → **Thiết lập máy này**.
4. Mở ViDental Imaging Agent từ menu Start hoặc biểu tượng cạnh đồng hồ; bấm **Tạo mã kết nối**, nhập 8 chữ số trên web trong 2 phút.
5. Bấm **Tìm thiết bị**, chọn thiết bị của phòng khám và nguồn TWAIN tương ứng, bấm **Lưu nguồn chụp**.
6. Tùy chọn **Chọn thư mục trên máy**: chọn trong cửa sổ Windows của agent. Không nhập đường dẫn từ web.
7. Hồ sơ khách hàng → Hình ảnh → Chụp từ máy → Mở máy chụp. Chụp trong cửa sổ của driver; xem trước trên web rồi bấm **Lưu vào hồ sơ**.

Cấu hình nguồn chụp/thư mục lưu là **theo máy và phòng khám**, không ghi đè máy khác.
Kết nối web theo tài khoản/phòng khám trên trình duyệt. Sau 30 ngày cần kết nối lại.
Agent không nhận hoặc lưu mật khẩu/JWT đăng nhập phòng khám.

Khi trình duyệt hỏi quyền truy cập mạng cục bộ, chọn Cho phép. Nếu đã từ chối, bật lại trong cài đặt quyền của trang.
Không tắt bảo mật trình duyệt/SmartScreen để xử lý sự cố. Bộ cài thử chưa ký mã không phải bộ cài phát hành.

Bản mới không gọi giao thức v1. Cần cập nhật **web và agent cùng đợt**. Không có fallback không xác thực.
Các trường agentUrl/sourceName/localSaveDir cũ trong cấu hình BE không còn điều khiển agent; chọn lại nguồn/thư mục trên từng máy.
Camera USB và API tải ảnh hiện có vẫn được giữ nguyên.

## Máy điểm chỉ (ZKTeco ZK9500)

Từ agent **2.1.0**. Dùng ở ô ký "Người bệnh / Đại diện" của phiếu bệnh án: chỉ máy tính đang cắm
máy điểm chỉ (và đang chạy agent) mới hiện nút **Điểm chỉ**.

1. Cài driver **ZKFinger** của ZKTeco (zkteco.com → ZKFinger SDK / driver ZK9500) cùng loại 32-bit
   hoặc 64-bit với Python/bộ cài agent. Cắm máy, chờ Windows nhận thiết bị.
2. Chạy agent 2.1.0 trở lên. Cửa sổ agent hiện "Đã cắm máy điểm chỉ" khi thấy máy.
3. Ghép nối web với agent một lần trên máy này: Cấu hình → Tích hợp → Thiết bị hình ảnh →
   **Thiết lập máy này** (bước 1–2). Máy điểm chỉ không cần khai báo thiết bị hay chọn nguồn TWAIN.
   Tài khoản khác trên cùng trình duyệt nhập mã kết nối ngay trong hộp thoại Điểm chỉ.
4. Phiếu → ô ký người bệnh → **Điểm chỉ** → đặt ngón tay → kiểm tra ảnh, tick "Người nhà ký thay"
   nếu cần → **Áp dụng**.

- Agent chỉ lấy ẢNH (`ZKFPM_AcquireFingerprintImage`), không trích hay lưu mẫu vân tay; ảnh tạm
  bị xoá khi web nhận xong. Chờ đặt tay tối đa 30 giây.
- Nhận máy qua danh sách USB đang cắm của Windows (VID `1B55` = ZKTeco), không nạp driver vào tiến
  trình chính; lấy ảnh chạy trong tiến trình con như TWAIN.
- Web chỉ hỏi agent trên trình duyệt đã từng ghép nối với phòng khám, nên máy không cài agent
  không bị trình duyệt hỏi quyền mạng cục bộ.
- Chưa thử với máy ZK9500 thật: phần gọi `libzkfp.dll` viết theo SDK, đã kiểm bằng thư viện giả lập
  và `--mock` (vân tay giả).

## Giới hạn và cách hủy

- Một tác vụ thiết bị tại một thời điểm, không xếp hàng chụp. Web yêu cầu lưu/bỏ lượt ảnh TWAIN hiện tại trước khi mở lượt mới, tránh chụp thêm rồi mất ảnh vì vượt bộ nhớ/API upload.
- Tìm nguồn: 20 giây; chọn thư mục: 120 giây; chụp: tối đa 10 phút.
- Cửa sổ chọn thư mục cũng nằm trong tiến trình con, không chặn vòng lặp của agent chính.
- Hủy/timeout dừng tiến trình con riêng của tác vụ. Không mở lượt khác trước khi tiến trình cũ đã dừng.
- Đóng dialog, chuyển bệnh nhân hoặc mất quyền truy cập khiến web hủy lượt đang chờ, bỏ qua ảnh trả về muộn.
- Không tự thử chụp lại sau lỗi mạng; điều này tránh phát sinh lượt chụp ngoài ý muốn.
- Tối đa 50 ảnh / 50 MiB JPEG mỗi tác vụ, 20 MiB / 40 triệu pixel mỗi ảnh. Vượt giới hạn báo lỗi, không tự giảm kích thước ảnh.
- Ảnh vẫn xuất JPEG theo chất lượng đã cấu hình, như luồng cũ. Không thay đây bằng ảnh chẩn đoán DICOM/16-bit gốc.
- File tạm được xóa khi web nhận xong/hủy/thất bại; ảnh chưa nhận hết tối đa 15 phút.
- Tối đa 8 lượt ảnh chưa giải phóng, 256 bản ghi tác vụ, 256 mã yêu cầu đã hủy, không tăng RAM vô hạn.
- Ngân sách nhận lượt mới 128 MiB dựa trên file hiện có và dự trữ 50 MiB JPEG; trong lúc chuyển đổi còn có một BMP tối đa khoảng 160 MiB và bộ đệm driver.
- Tiến trình driver bị giới hạn bộ nhớ 768 MiB trên Windows. Đây không phải số RAM toàn agent hoặc toàn driver của hãng.
- Bản sao cục bộ dùng tên ngẫu nhiên không ghi đè ảnh cũ. Nếu không lưu được bản sao, web vẫn nhận ảnh và hiển thị cảnh báo.
- Nếu cưỡng bức tắt lúc sao chép, thư mục đã chọn có thể còn file .vidental-*.pending chưa hoàn chỉnh; không coi file đó là ảnh đã lưu. Agent không quét/xóa hàng loạt thư mục người dùng.

Nguồn TWAIN bị treo do driver có thể cần rút/cắm lại thiết bị hoặc khởi động lại phần mềm của hãng sau khi hủy. Kết thúc tiến trình không bảo đảm phần cứng đã trở về trạng thái sẵn sàng.

## Bảo mật

- Chỉ lắng nghe 127.0.0.1:27100, không mở LAN, không thêm firewall rule.
- Origin mặc định duy nhất https://app.vidental.vn; kiểm tra Host và Origin, không cho wildcard/Origin null.
- Chỉ health cho phép thiếu Origin để chẩn đoán cục bộ. Mọi thao tác còn lại cần Origin hợp lệ; trừ ghép nối, cần Bearer token riêng của agent.
- Mã ghép nối: 8 số, 2 phút, dùng một lần, tối đa 5 lần sai. Chỉ tạo mã bằng cửa sổ agent.
- Token kết nối 30 ngày, chỉ lưu hash trong cấu hình agent; session tối đa 1 giờ và không vượt hạn kết nối, giữ trong RAM. Không truyền token trên URL.
- Web giữ token kết nối trong localStorage tách theo origin/tenant/account. XSS hoặc chiếm tài khoản Windows vẫn là rủi ro; đây không phải kho khóa chống mã độc.
- Mỗi tác vụ gắn grant sở hữu; grant khác không được xem, tải, hủy hoặc xóa ảnh của tác vụ đó.
- Nút **Ngắt tất cả kết nối trên máy này** ở agent thu hồi token và hủy tác vụ, không khóa tài khoản BE/phòng khám khác.
- Ghép nối là sự đồng ý sử dụng thiết bị cục bộ, **không thay thế phân quyền BE**. Mã phòng khám gửi khi ghép nối không phải định danh đã được BE ký xác nhận. API tải ảnh BE vẫn kiểm tra tài khoản/phòng khám/bệnh nhân/quyền như cũ.
- HTTP: tối đa 16 handler, body JSON 64 KiB, deadline tổng header/body 8 giây. Từ chối Content-Length âm/trùng/quá lớn, chunked và JSON không phải object **trước khi đặt busy**.
- Truyền JPEG nhị phân từng ảnh, không gom base64 vào một response.
- Worker có Windows Job Object (kill-on-close), dừng cả cây tiến trình thuộc tác vụ khi bị hủy. Đây là cách quản lý vòng đời, không phải sandbox chống driver độc hại cùng tài khoản Windows.
- Log cục bộ chỉ trạng thái kỹ thuật, không ghi token/mã ghép nối/đường dẫn ảnh/thông tin bệnh nhân. Xoay tối đa khoảng 3 MiB trong %LOCALAPPDATA%\ViDental\ImagingAgent.
- Không thay đổi DB, jOOQ, luồng ký số/PDF, S3 hay nhận diện IP đăng nhập/điểm danh. Nginx chỉ thêm đúng loopback vào connect-src và không để /downloads rơi về index.html.

## Phát triển và kiểm thử

Python 3.13+ còn được bảo trì, có Tkinter. Agent thực tế phải cùng bitness với driver.

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m unittest discover -s tests -v

Chạy giả lập với origin dev **khai báo rõ**, không tự cho phép mọi localhost:

    .\.venv\Scripts\python.exe agent.py --mock --allowed-origins http://127.0.0.1:5173

Không chạy --mock tại máy đang chụp bệnh nhân thật. Giao diện hiển thị GIẢ LẬP; ảnh giả lập có watermark.

Kiểm thử FE:

    cd ..\clinic-management-web
    node --test tests/imaging-agent.test.mjs
    npm run build

Kiểm thử trình duyệt (Edge cài trên máy + Playwright, agent fixture giả lập dùng cổng 27100, Vite 5179):

    node tests/imaging-browser.mjs

Mặc định dùng Python tại ../clinic-imaging-agent/.venv/Scripts/python.exe và Playwright tại
../clinic-imaging-agent/.build-tools/browser/node_modules/playwright/index.mjs.
Có thể chỉ định IMAGING_TEST_PYTHON và PLAYWRIGHT_MODULE (file URL/module path).
Test tự mở/đóng agent và Vite. Không chạy nếu một agent thật đang dùng cổng 27100.
Harness chỉ nằm trong tests/, không đưa vào bundle production. Upload trong harness là callback giả lập, không chạm BE/DB/S3.

Kiểm thử EXE sau đóng gói:

    python tests/smoke_packaged.py --exe dist\32\ViDentalImagingAgent\ViDentalImagingAgent.exe

## Đóng gói và phát hành

Dùng PyInstaller 6.22.2 và Inno Setup 6. Build script tạo venv riêng theo kiến trúc, kiểm tra bitness, chạy unit test và smoke test EXE trước khi tạo installer.
Không thay đổi môi trường Python của dự án khác.

    .\build-exe.ps1 -PythonExe C:\Python314-32\python.exe -Architecture 32 -ISCCPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" -AllowUnsignedForTesting

Cho driver 64-bit: dùng Python 64-bit và -Architecture 64. Không thể suy chọn bản từ Windows 64-bit.
Cùng AppId và cổng: mỗi tài khoản Windows chỉ chạy một bản agent. Installer cập nhật không xóa cấu hình/kết nối/bản sao cục bộ.

**Phát hành cho phòng khám**: cung cấp chứng thư ký mã trong Cert:\CurrentUser\My và private key, rồi:

    .\build-exe.ps1 -PythonExe C:\Python314-32\python.exe -Architecture 32 -SigningThumbprint THUMBPRINT -PublishWeb

Script ký EXE và installer, kiểm tra chữ ký; chỉ -PublishWeb mới chép vào
../clinic-management-web/public/downloads và tạo manifest SHA-256.
Build web sau bước này, deploy web và cài agent trên máy trạm.
Không tự deploy từ script. Web chỉ hiển thị link trong manifest hợp lệ đã đánh dấu signed=true từ pipeline phát hành.
Manifest không tự chứng minh chữ ký trong trình duyệt; bảo vệ pipeline/HTTPS và xác thực chữ ký Windows vẫn cần thiết.

Bản chưa ký chỉ để kiểm thử cục bộ, không tự đưa lên web. SmartScreen có thể cảnh báo cả chứng thư mới chưa có uy tín.
Chưa có chứng thư ký mã trong đợt sửa này, nên chưa phát hành installer cho người dùng production.

## Phạm vi đã kiểm chứng của F2

- **57 ca** unit/integration HTTP/ảnh agent: bộ ca kiểm tra token/Origin/Host, body/deadline, busy, hủy/timeout/crash, quyền sở hữu tác vụ, ảnh/thư mục tạm.
- Tiến trình Windows thật: mock capture, Windows Job Object, hủy tiến trình, chạy lại.
- **17 ca FE agent** (và 9 ca hồi quy ma trận quyền): kiểm tra schema, giới hạn response cả trong lúc đọc body, ảnh thiếu, mất response khởi tạo, không retry chụp, hủy và giải phóng file tạm.
- Edge thật + agent mock thật: ghép nối, tìm nguồn, lưu cấu hình, tải ảnh nhị phân/xem trước, callback lưu, đổi bệnh nhân khi đang chụp, camera USB giả lập, layout desktop/mobile.
- Đã build và smoke-test EXE/installer thử 32-bit với Python 3.14.7. Installer 64-bit chưa được build trong môi trường này.
- Chưa kiểm với máy/driver TWAIN thực tế; chưa pentest; chưa đo memory với ảnh thật 40 MP; chưa chạy hệ thống 1.000 người dùng; chưa kiểm prompt Local Network Access trên domain HTTPS production.
- Chưa cài/uninstall installer trên máy phòng khám thật; smoke test chạy EXE đã đóng gói trong thư mục build.

## Tham chiếu kỹ thuật

- TWAIN API đang sử dụng: https://pytwain.readthedocs.io/en/latest/twain.html
- Quản lý tiến trình Windows: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- Quyền mạng cục bộ Chrome: https://developer.chrome.com/blog/local-network-access
- Installer không nâng quyền: https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm
