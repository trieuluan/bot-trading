# Signal Desk / bot-trading

Khung ứng dụng quản lý bot đa sàn: React + TypeScript, FastAPI + Python worker, PostgreSQL, Docker Compose.

**Trạng thái v0.1:** chỉ có dữ liệu tổng hợp và ghi nhận tín hiệu. Chưa tích hợp sàn, khớp lệnh mô phỏng, backtest, P&L, quản lý tài khoản, hoặc giao dịch thật. Không yêu cầu API key. Giá hiển thị không phải giá thị trường.

## Chạy bằng Docker

Cần Docker Engine / Docker Desktop / OrbStack đang hoạt động.

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

- Dashboard: http://localhost:3000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

Compose chờ PostgreSQL healthy, chạy migration một lần, sau đó khởi động API và worker riêng. Bot mặc định dừng; vào dashboard bấm **Bật phân tích mô phỏng**. Mỗi 5 giây worker xử lý một nến replay 15 phút, tối đa 2.000 nến. Đây là phát lại tăng tốc, không phải feed thời gian thực. Tín hiệu lưu vào database và không tạo lệnh.

```bash
docker compose logs -f api bot
docker compose stop bot       # dừng tiến trình worker
docker compose down           # giữ volume dữ liệu
```

`docker compose down -v` xóa toàn bộ dữ liệu database, chỉ dùng khi chủ động reset.

## Docker dev với hot reload frontend

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

Dashboard vẫn ở http://localhost:3000, nhưng được phục vụ bằng Vite dev server với React Fast Refresh. Sửa file trong `frontend/src/` sẽ cập nhật giao diện ngay, không cần build lại container. Source được bind mount; `node_modules` Linux nằm trong volume riêng, không dùng chung dependency macOS. Polling được bật để nhận thay đổi file qua Docker trên macOS.

API và bot vẫn chạy bằng image như trước. Khi sửa dependency trong `package.json`/lockfile, khởi động lại frontend để chạy lại `npm ci`:

```bash
docker compose -f compose.yaml -f compose.dev.yaml restart frontend
```

Quay lại Nginx và bản production build:

```bash
docker compose -f compose.yaml up --build -d
```

Dùng đủ hai file Compose cho các lệnh dev để giữ đúng cấu hình.

## Phát triển cục bộ không cần Docker

Python 3.13+ và Node.js 22+. Dùng SQLite cho dev; Docker dùng PostgreSQL.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
alembic upgrade head
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal thứ hai, tại thư mục gốc:

```bash
source .venv/bin/activate
python -m bot.worker
```

Terminal thứ ba:

```bash
cd frontend
npm ci
npm run dev
```

Vite proxy `/api` sang API localhost:8000. API restart không mất trạng thái vì trạng thái nằm trong database. Worker restart tiếp tục từ nến đã commit. Không chạy nhiều worker cùng lúc trong bản SQLite dev. PostgreSQL dùng row lock để tuần tự hóa bật/dừng và xử lý tín hiệu.

## Kiểm tra

```bash
source .venv/bin/activate
python -m pytest backend/tests -q
cd frontend
npm run build
```

## Cấu trúc

```text
frontend/               React dashboard; Vite dev, Nginx production image
backend/app/            API, models, dữ liệu mẫu, logic chiến lược dùng chung
bot/worker.py           Worker độc lập, heartbeat + replay + lưu tín hiệu
migrations/             Alembic schema versions
compose.yaml            frontend, api, bot, migrate, postgres
```

`MarketDataAdapter` là ranh giới để bổ sung nguồn giá thật. Chỉ `DemoAdapter` đã triển khai. Các tên sàn trên trang Kết nối là kế hoạch, không phải tích hợp đang hoạt động.

## Ranh giới vận hành

- `TRADING_MODE=paper` là giá trị duy nhất được hỗ trợ. API và worker từ chối khởi động nếu cấu hình live.
- Chưa có authentication. Chỉ dùng môi trường phát triển; các cổng được bind về loopback. Không đưa bản này ra Internet hoặc chuyển bind thành `0.0.0.0` trước khi bổ sung đăng nhập, phân quyền, TLS và bảo vệ thao tác điều khiển.
- Chưa tự deploy VPS. Docker mặc định dùng frontend build tĩnh; thay source cần build lại. Docker dev dùng Vite hot reload cho frontend như hướng dẫn trên. Backend và worker vẫn cần build lại khi sửa source.
- PostgreSQL không publish cổng ra host; dữ liệu nằm trong named volume.
- `.env` và API keys không được commit. Mật khẩu mặc định chỉ dành cho dev.
- Dashboard offline hiển thị preview tổng hợp và vô hiệu hóa điều khiển. Dữ liệu này không ghi vào database. UI lấy lại snapshot API mỗi 5 giây, worker stale sau 30 giây.
- TP/SL trong tín hiệu là mức tham chiếu; chưa có bộ máy khớp lệnh hoặc bảo vệ vị thế thật.

## Lộ trình tiếp theo

1. Chọn sàn và spot/perpetual; tích hợp dữ liệu public, chuẩn hóa nến đã đóng, xử lý gap/reconnect.
2. Backtest dùng chung logic tín hiệu, kiểm tra out-of-sample, phí và trượt giá.
3. Mô phỏng khớp lệnh, giới hạn rủi ro, thống kê P&L, tổng exposure.
4. Thêm adapter thứ hai và kiểm thử tính năng từng sàn.
5. Authentication, quản lý secret và đối chiếu trạng thái lệnh trước khi xem xét giao dịch thật.

## Tham khảo thiết kế và kiến trúc

- [Hummingbot Dashboard](https://hummingbot.org/dashboard/): quản lý instance, chiến lược, kết nối theo từng tác vụ. Trang này ghi rõ dashboard cũ không còn được duy trì tích cực; dùng để tham khảo bố cục, không làm dependency.
- [Freqtrade](https://docs.freqtrade.io/en/stable/installation/): tách dry-run trước live.
- [CCXT](https://docs.ccxt.com/docs/manual): tham khảo chuẩn hóa adapter crypto; chưa cài trong bản này.

Giao diện được viết riêng, không sao chép source hoặc tài sản của các sản phẩm trên. Higgsfield được dùng tạo một concept hình ảnh UI trong chat; ảnh không phải chart dữ liệu và không thay thế component tương tác.

## Code style và cấu trúc frontend

Source được giữ dạng dễ đọc. Chỉ output production trong `dist/` được minify bởi Vite.

```text
frontend/src/
├── components/       # Chart, bot controls, table, sidebar, dialog
├── pages/            # Tổng quan, bot, chiến lược, kết nối, nhật ký
├── hooks/            # State và vòng cập nhật dashboard
├── lib/              # API client, format giá, dữ liệu preview
└── types/            # Kiểu dữ liệu API dùng chung
```

Frontend dùng Prettier (2 spaces, dòng tối đa 100 ký tự); Python dùng Ruff (4 spaces). VS Code có cấu hình format-on-save và gợi ý extensions trong `.vscode/`. Cài hai extension được gợi ý để editor dùng cùng formatter với CI.

```bash
cd frontend
npm run format          # format frontend, JSON, YAML và Markdown
npm run format:check    # kiểm tra mà không sửa file
```

Tại thư mục gốc, sau khi cài requirements-dev:

```bash
ruff format backend bot migrations
ruff check backend bot migrations
```

CI kiểm tra format, Python lint, backend tests và frontend build.
