# Cài ViDental Imaging Agent từ Git bằng CMD

Agent 2.1.0 / giao thức v2. Cài trên chính máy tính Windows của phòng khám sẽ cắm sensor và mở web ViDental. Không cần bộ cài Agent `.exe`.

Chưa biết hãng/model vẫn có thể cài Git, tải mã và thử giả lập. Để dùng sensor thật, cần driver TWAIN đúng model và Python cùng loại 32-bit hoặc 64-bit với driver. Không suy loại driver từ Windows 64-bit.

## 1. Mở CMD

Nhấn **Windows + R**, gõ `cmd`, nhấn Enter. Dán từng dòng lệnh rồi nhấn Enter; chờ chạy xong mới dán dòng tiếp. Không dán vào trình duyệt hoặc cửa sổ Python có dấu `>>>`.

Nếu đang mở PowerShell, gõ `cmd` rồi Enter để dùng các lệnh bên dưới.

## 2. Cài Git và Python

Cài Git bằng lệnh CMD:

```bat
winget install --id Git.Git -e --source winget
```

Nếu không có `winget`, tải bộ Git for Windows từ https://git-scm.com/install/windows và cài với lựa chọn mặc định. Đóng CMD và mở lại, kiểm tra:

```bat
git --version
```

Tải Python **3.13 trở lên** từ https://www.python.org/downloads/windows/. Chọn **Windows installer**, không chọn embeddable. Chọn 32-bit hoặc 64-bit phù hợp driver. Tích **Add python.exe to PATH** nếu có, rồi Install Now. Đóng CMD và mở lại:

```bat
python --version
python -c "import struct; print(struct.calcsize('P') * 8)"
```

Dòng đầu hiện Python 3.13.x hoặc mới hơn; dòng sau hiện 32 hoặc 64. Nếu Python không được nhận hoặc mở Microsoft Store, kiểm tra lại cài đặt/PATH trước khi tiếp tục. Khi chưa biết driver, chỉ thử giả lập; xác nhận kiến trúc trước khi nối thiết bị thật.

## 3. Tải Agent từ kho public

Giữ nguyên `%USERPROFILE%` trong lệnh: CMD tự thay bằng thư mục tài khoản Windows.

```bat
cd /d "%USERPROFILE%"
if not exist ViDental mkdir ViDental
cd ViDental
git clone https://github.com/Thanh097698/agent.git clinic-imaging-agent
cd clinic-imaging-agent
```

Kho public không yêu cầu tài khoản GitHub để tải. Thư mục `clinic-imaging-agent` phải có `agent.py` và `requirements.txt`.

Nếu thư mục đã tồn tại, không clone chồng hoặc xóa. Kiểm tra `git remote -v` trong thư mục đó có đúng `Thanh097698/agent` không. Nếu là kho khác, nhờ quản trị dùng thư mục mới; không tự thay remote của kho private.

## 4. Cài thư viện

Trong CMD tại thư mục `clinic-imaging-agent`:

```bat
python -m venv .venv-clinic
.venv-clinic\Scripts\python.exe -m pip install -r requirements.txt
```

Lệnh đầu tạo môi trường Python riêng; lệnh sau cần Internet. Chờ CMD trở lại dấu nhắc. Nếu có `ERROR`, dừng và gửi nguyên thông báo cho quản trị. Không cần chạy lệnh kích hoạt môi trường hoặc đổi ExecutionPolicy.

## 5. Chạy thử khi chưa có sensor

```bat
.venv-clinic\Scripts\python.exe agent.py --mock
```

Cửa sổ Agent xuất hiện, có nút **Tạo mã kết nối** và trạng thái giả lập. Giữ CMD mở. Mở trình duyệt tại `http://127.0.0.1:27100/health`; phản hồi có `version` là `2.1.0` xác nhận Agent đang chạy.

Chế độ này dùng ảnh giả, chưa kiểm tra driver/sensor. Thoát Agent hoặc nhấn Ctrl+C trong CMD trước khi chạy chế độ thật. Chỉ chạy một Agent trên máy.

## 6. Cài driver hãng và chạy sensor thật

Sau khi biết hãng/model, lấy driver TWAIN từ hãng hoặc nhà cung cấp. Chạy bộ cài, cắm sensor và khởi động lại theo hướng dẫn hãng. Không có lệnh CMD chung cho mọi driver.

Kiểm tra thiết bị được nhận trong phần mềm hãng, rồi đóng phần mềm đó. Python của Agent phải cùng 32/64-bit với driver. Đổi Python không tự đổi môi trường `.venv-clinic` đã tạo; nhờ quản trị tạo lại môi trường nếu cần đổi kiến trúc.

Mở CMD:

```bat
cd /d "%USERPROFILE%\ViDental\clinic-imaging-agent"
.venv-clinic\Scripts\python.exe agent.py
```

Giữ CMD và Agent mở. Mặc định Agent cho phép web `https://app.vidental.vn`. Nếu web có địa chỉ khác, thoát Agent trước rồi dùng đúng origin. Ví dụ web dev đang mở `http://localhost:5173`:

```bat
.venv-clinic\Scripts\python.exe agent.py --allowed-origins http://localhost:5173
```

`localhost` và `127.0.0.1` khác nhau. Origin gồm `http(s)://`, tên miền và cổng nếu có, không có đường dẫn phía sau. Nhờ quản trị cung cấp giá trị đúng khi chưa rõ.

## 7. Khai báo và ghép với web

1. Đăng nhập đúng phòng khám, mở **Cấu hình → Tích hợp → Thiết bị hình ảnh → Thêm thiết bị**. Tài khoản cần quyền sửa Tích hợp.
2. Đặt tên như **Sensor ghế 1**, chọn **Sensor X-quang trong miệng**, kiểu **TWAIN (qua agent trên máy Windows)**, bật dùng và lưu.
3. Bấm **Thiết lập máy này**. Trong cửa sổ Agent, bấm **Tạo mã kết nối**; nhập 8 số trên web trong 2 phút rồi bấm **Kết nối**.
4. Nếu trình duyệt hỏi quyền mạng cục bộ, chọn Cho phép cho ViDental.
5. Bấm **Tìm thiết bị**, chọn thiết bị phòng khám và **Nguồn TWAIN trên máy này**, rồi **Lưu nguồn chụp**. Danh sách rỗng: kiểm tra driver và 32/64-bit.
6. Vào đúng hồ sơ → **Hình ảnh** → tạo thư mục nếu chưa có → **Chụp từ máy → Mở máy chụp**. Thu nhận trong driver, kết thúc cửa sổ thu nhận để ảnh về web, rồi **Lưu vào hồ sơ**. Tải lại tab để xác nhận ảnh đã lưu.

Web phải hỗ trợ giao thức v2. Việc `/health` trả kết quả chỉ chứng minh Agent đang chạy, chưa chứng minh sensor và lưu hồ sơ đã hoạt động.

## 8. Máy điểm chỉ ZKTeco ZK9500 (nếu phòng khám dùng)

Cần Agent **2.1.0** trở lên. Máy đã cài bản cũ thì làm bước 9 (cập nhật) trước.

1. Tải driver **ZKFinger** (ZKFinger SDK cho ZK9500) từ zkteco.com hoặc nhà cung cấp máy, chạy bộ cài. Chọn cùng loại 32-bit hoặc 64-bit với Python đã kiểm ở bước 2.
2. Cắm máy điểm chỉ vào cổng USB, chờ Windows nhận thiết bị.
3. Chạy Agent như bước 6. Cửa sổ Agent hiện **Đã cắm máy điểm chỉ** là Agent đã thấy máy. Mở `http://127.0.0.1:27100/health` sẽ thấy `"fingerprint": true`.
4. Nếu máy này chưa ghép với web: làm bước 7 mục 3–4 (**Thiết lập máy này** → nhập mã kết nối). Không cần thêm thiết bị hay chọn nguồn TWAIN cho máy điểm chỉ.
5. Mở phiếu có ô ký **Người bệnh / Đại diện** (ví dụ Phiếu đồng ý điều trị) → bấm **Điểm chỉ** ở góc trái ô ký → đặt ngón tay lên máy → kiểm tra ảnh, tick **Người nhà ký thay** nếu người nhà ký → **Áp dụng** → **Lưu** phiếu.

Nút **Điểm chỉ** chỉ hiện trên máy đang chạy Agent và đang cắm máy điểm chỉ; rút máy hoặc tắt Agent thì nút tự ẩn sau vài giây. Tài khoản khác trên cùng trình duyệt chưa ghép nối sẽ được hỏi mã kết nối ngay trong hộp thoại Điểm chỉ.

## 9. Mở lại mỗi ngày và cập nhật

Mỗi ngày chỉ mở CMD và chạy:

```bat
cd /d "%USERPROFILE%\ViDental\clinic-imaging-agent"
.venv-clinic\Scripts\python.exe agent.py
```

Nếu web dùng origin riêng, thêm lại tùy chọn đã dùng ở bước 6. Không cần clone hoặc cài thư viện mỗi ngày.

Khi quản trị thông báo cập nhật, thoát Agent rồi chạy:

```bat
cd /d "%USERPROFILE%\ViDental\clinic-imaging-agent"
git pull --ff-only
.venv-clinic\Scripts\python.exe -m pip install -r requirements.txt
.venv-clinic\Scripts\python.exe agent.py
```

Nếu bất kỳ lệnh nào báo lỗi, dừng và gửi thông báo cho quản trị trước khi chạy tiếp.

## Lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| `git` / `python` không được nhận | Đóng CMD, mở lại; kiểm tra đã cài và Python đã thêm PATH. |
| Không thấy `agent.py` / `requirements.txt` | Kiểm tra tải Git đã xong và đang ở đúng thư mục; chạy lại dòng `cd /d`. |
| `pip` báo lỗi | Kiểm tra Internet và Python 3.13+; gửi nguyên lỗi, không tự thay requirements. |
| Cổng 27100 đang dùng | Kiểm tra Agent đã mở; chỉ chạy một bản trên máy. |
| Không thấy nguồn TWAIN | Kiểm tra driver, thiết bị đã nhận và Python cùng 32/64-bit. |
| Không có nút Chụp từ máy | Thiết bị phải đang bật; tài khoản cần quyền Sửa tab Hình ảnh. |
| Không có nút Điểm chỉ | Agent 2.1.0+ phải đang chạy, cửa sổ Agent báo **Đã cắm máy điểm chỉ**, và trình duyệt đã ghép nối (bước 7). |
| Điểm chỉ báo chưa cài driver | Cài driver ZKFinger cùng 32/64-bit với Python của Agent, rút máy ra cắm lại. |

Chưa xác nhận tương thích với model sensor cụ thể. Hãng/model và driver sẽ bổ sung khi có thiết bị. Camera USB nhận như webcam dùng nhánh Camera USB của web, không cần Agent.
