# Signal Desk / bot-trading

Khung ứng dụng quản lý bot đa sàn: React + TypeScript, FastAPI + Python worker, PostgreSQL, Docker Compose.

**Trạng thái v0.2:** đã có dữ liệu public MEXC Futures BTC_USDT perpetual (15m), theo dõi breakout long/short và backtest lịch sử. Có thể chuyển về replay tổng hợp. Chưa có khớp lệnh paper liên tục, tài khoản sàn hoặc giao dịch thật. Không yêu cầu API key.

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

Compose chờ PostgreSQL healthy, chạy migration một lần, sau đó khởi động API và worker riêng. Bot mặc định dừng. Vào **Kết nối sàn → MEXC Futures → Sử dụng nguồn này**, rồi bấm **Bật theo dõi tín hiệu** trên Tổng quan. Nguồn dữ liệu được lưu trong database; chuyển nguồn sẽ dừng bot. MEXC Futures đồng bộ REST mỗi 15 giây, chỉ lưu nến 15 phút đã đóng theo giờ server. Feed vẫn cập nhật khi tắt phân tích tín hiệu. Với Demo replay, mỗi 5 giây xử lý một nến tổng hợp, tối đa 2.000 nến. Tín hiệu lưu vào database và không tạo lệnh.

```bash
docker compose logs -f api bot
docker compose stop bot       # dừng tiến trình worker
docker compose down           # giữ volume dữ liệu
```

`docker compose down -v` xóa toàn bộ dữ liệu database, chỉ dùng khi chủ động reset.

## Docker dev với hot reload

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

Dashboard vẫn ở http://localhost:3000, nhưng được phục vụ bằng Vite dev server với React Fast Refresh. Sửa file trong `frontend/src/` sẽ cập nhật giao diện ngay, không cần build lại container. Source được bind mount; `node_modules` Linux nằm trong volume riêng, không dùng chung dependency macOS. Polling được bật để nhận thay đổi file qua Docker trên macOS.

| Service    | Chế độ dev                                                                  |
| ---------- | --------------------------------------------------------------------------- |
| `frontend` | Vite + React Fast Refresh khi sửa giao diện                                 |
| `api`      | Uvicorn tự restart khi sửa `backend/app/`                                   |
| `bot`      | Watchfiles gửi SIGINT rồi restart worker khi sửa `bot/` hoặc `backend/app/` |
| `migrate`  | Đọc migration từ source mount, chạy theo lệnh; không tự theo dõi file       |
| `postgres` | Giữ nguyên PostgreSQL và volume dữ liệu                                     |

Python source được mount chỉ đọc; polling nhận thay đổi file trên macOS. API và worker dùng image dev có pytest, Ruff và Watchfiles. Sau restart, worker đọc lại trạng thái và nến đã commit từ database; bật/tắt bot được giữ nguyên. Khi sửa chiến lược, thay đổi sẽ áp dụng từ lần xử lý kế tiếp sau restart.

Khi sửa dependency trong `package.json`/lockfile, khởi động lại frontend để chạy lại `npm ci`:

```bash
docker compose -f compose.yaml -f compose.dev.yaml restart frontend
```

Khi sửa `backend/requirements*.txt` hoặc Dockerfile Python, build lại các service Python:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d api bot migrate
```

Khi thêm/sửa migration, áp dụng chủ động trước khi dùng code phụ thuộc schema mới:

```bash
docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate
```

Nếu thay đổi schema không tương thích với code đang chạy, dừng `api bot` trước khi migrate rồi chạy lại. Không chỉnh migration đã áp dụng; tạo revision mới.

Chạy kiểm tra bên trong image dev, database test được tạo riêng và không dùng PostgreSQL đang chạy:

```bash
docker compose -f compose.yaml -f compose.dev.yaml exec api python -m pytest backend/tests -q
docker compose -f compose.yaml -f compose.dev.yaml exec api ruff check backend bot migrations
docker compose -f compose.yaml -f compose.dev.yaml exec api ruff format --check backend bot migrations
docker compose -f compose.yaml -f compose.dev.yaml logs -f api bot
```

Quay lại Nginx và image Python production không có dev dependencies/watchers:

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

`DemoAdapter` phục vụ replay; `MexcFuturesAdapter` đọc dữ liệu USDT perpetual qua public REST. Source, symbol, timeframe và thời điểm mở nến tạo định danh riêng, tránh trộn tín hiệu tổng hợp với dữ liệu sàn. Bản này chỉ triển khai BTC_USDT 15m; chưa có adapter Forex hoặc sàn khác.

## Ranh giới vận hành

- `TRADING_MODE=paper` là giá trị duy nhất được hỗ trợ. API và worker từ chối khởi động nếu cấu hình live.
- Chưa có authentication. Chỉ dùng môi trường phát triển; các cổng được bind về loopback. Không đưa bản này ra Internet hoặc chuyển bind thành `0.0.0.0` trước khi bổ sung đăng nhập, phân quyền, TLS và bảo vệ thao tác điều khiển.
- Chưa tự deploy VPS. Docker mặc định dùng frontend build tĩnh; thay source cần build lại. Docker dev có hot reload cho frontend, API và worker như hướng dẫn trên. API và worker trong Docker dev tự restart khi sửa source; dependency Python cần build lại.
- PostgreSQL không publish cổng ra host; dữ liệu nằm trong named volume.
- `.env` và API keys không được commit. Mật khẩu mặc định chỉ dành cho dev.
- Dashboard offline hiển thị preview tổng hợp và vô hiệu hóa điều khiển. Dữ liệu này không ghi vào database. UI lấy lại snapshot API mỗi 5 giây, worker stale sau 30 giây.
- TP/SL của worker là mức tham chiếu. Backtest có mô hình khớp lệnh nghiên cứu theo OHLC; chưa có paper execution liên tục hoặc bảo vệ vị thế thật.

## Dữ liệu MEXC Futures và backtest

1. Mở **Kết nối sàn**, chọn MEXC Futures để xem chart giá hợp đồng thật. Lần đầu tải 120 nến đã đóng. Chuyển nguồn sẽ dừng bot; bật lại nếu muốn ghi nhận tín hiệu mới.
2. Mở **Backtest**, chọn 7/30/90 ngày, vốn, rủi ro mỗi lệnh, phí và trượt giá; bấm **Chạy backtest**. Không cần bật bot. Báo cáo gần nhất tự tải lại khi mở trang.
3. Xem toàn bộ, 70% đầu hoặc 30% kiểm chứng. Phần **Quan sát lệnh trên chart** có nến tương tác, volume, marker entry/exit và đường SL/TP. Chọn lệnh trong dropdown, chuyển trước/sau, kéo chart hoặc zoom; bấm **Về lệnh đã chọn** để căn lại vùng nến. Đây là lệnh mô phỏng trong backtest, không phải lệnh đặt trên MEXC. JSON theo ID chứa báo cáo, nến giao dịch, nến fair price, funding và snapshot thông số hợp đồng, đủ để tái lập lần chạy.

### Quy tắc dữ liệu

- UTC seconds trong ứng dụng; Futures `start/end` dùng seconds, server ping và funding settlement dùng milliseconds.
- Chia truy vấn nến thành tối đa 2.000 điểm mỗi request; kiểm tra OHLCV hữu hạn, thứ tự và liên tục. Nến đang mở bị loại. Không tự tạo nến để lấp khoảng trống.
- Timeout và lỗi mạng/5xx retry tối đa 3 lần với backoff. 418/429 dừng request và lưu cooldown theo `Retry-After` (ít nhất 60 giây). Worker thử lại sau cooldown.
- Sau restart/mất mạng, lấy bù từ nến cuối đã lưu, tối đa 10.000 nến mỗi lượt. Cursor và tín hiệu commit cùng transaction; không tạo trùng khi chạy lại. Khi người dùng tắt bot, nến vẫn đồng bộ nhưng không tạo lại tín hiệu cho khoảng đã tắt.
- Chỉ là REST polling, chưa có WebSocket; reconnect là lần polling kế tiếp và REST backfill. Khi feed lỗi, dashboard giữ dữ liệu cũ và hiện trạng thái chậm; không đổi sang giá tổng hợp.

### Mô hình nghiên cứu

- Breakout đóng vượt đỉnh 20 nến cho LONG; phá đáy 20 nến cho SHORT. SL ở đáy/đỉnh 5 nến trước và TP 2R từ giá tín hiệu. Demo giữ quy tắc LONG ban đầu.
- Vào ở **open nến kế tiếp**, cộng trượt giá bất lợi. Bỏ qua nếu gap vượt SL/TP; chỉ giữ một vị thế. Khối lượng làm tròn xuống theo contract size/lot step hiện tại; giá trị vị thế tối đa 1× vốn. Rủi ro cấu hình là khoảng entry–SL, gap/phí có thể làm lỗ vượt mức đó.
- Nếu một nến chạm cả SL và TP, xử lý SL trước. Gap qua SL khớp tại open; TP dùng giá mục tiêu. Phí cả hai chiều, trượt giá cả vào và ra.
- Funding dùng lịch sử rate thực, signed theo LONG/SHORT, fair-price **open** tại kỳ quyết toán làm giá xấp xỉ. Giả định funding quyết toán trước exit/entry tại open. Không giả định funding bằng 0 khi API thiếu dữ liệu.
- Chia thời gian 70/30 với quy tắc cố định. Tập 30% dùng 20 nến trước mốc chia để warmup; không mang vị thế từ tập 70% sang. Mỗi khoảng dùng cùng vốn ban đầu và đóng vị thế cuối khoảng, nên tổng hai khoảng không nhất thiết bằng toàn bộ.
- Báo cáo có P&L ròng, tỷ lệ thắng, profit factor, drawdown theo mark equity cuối nến, tổng phí, funding, đường vốn và từng lệnh.
- **Giới hạn:** chưa mô phỏng liquidation, maintenance-margin tiers, ADL, order-book liquidity hoặc partial fills. Dùng contract metadata hiện tại cho cả lịch sử; chưa có lịch sử thay đổi lot size. Fair-price open chỉ là giá xấp xỉ lúc settlement. Đây là backtest nghiên cứu, không phải bản sao matching engine sàn.
- Phí mặc định 5 bps/chiều và trượt giá 2 bps/chiều là **giả định**, không phải báo giá phí tài khoản. Có thể chỉnh trên form.

### API nội bộ

- `POST /api/market/config`: `{"source":"mexc_futures"}` hoặc `synthetic`.
- `POST /api/backtests`: `{"days":30,"initial_capital":10000,"risk_percent":1,"fee_bps":5,"slippage_bps":2}`.
- `GET /api/backtests/latest`, `GET /api/backtests/{id}`: báo cáo đã lưu, kèm dữ liệu gốc.
- Migration `0002` giữ tín hiệu cũ dưới source `synthetic`, thêm market candles/state và báo cáo. Downgrade yêu cầu chuyển đổi dữ liệu có kiểm soát; không tự xóa tín hiệu để khôi phục constraint cũ.

### Sandbox của MEXC

[MEXC API FAQ](https://www.mexc.com/mexc-api) ghi rõ API hiện kết nối môi trường live và không có sandbox/test environment. Trang Futures Demo Trading là tính năng web, không phải một API sandbox được tài liệu này hỗ trợ. Spot [`POST /api/v3/order/test`](https://www.mexc.com/api-docs/spot-v3/spot-account-trade/test-new-order) chỉ kiểm tra yêu cầu, không gửi tới matching engine và không mô phỏng số dư/P&L. Ứng dụng hiện chỉ gọi các endpoint public GET.

Tài liệu đã đối chiếu ngày 12/10/2026:

- [Futures API introduction](https://www.mexc.com/api-docs/futures/integration-guide)
- [Candlestick data](https://www.mexc.com/api-docs/futures/market-endpoints/get-candlestick-data)
- [Fair-price candles](https://www.mexc.com/api-docs/futures/market-endpoints/get-fair-price-candles)
- [Funding history](https://www.mexc.com/api-docs/futures/market-endpoints/get-funding-rate-history)
- [Contract info](https://www.mexc.com/api-docs/futures/market-endpoints/get-contract-info)

## Lộ trình tiếp theo

1. Paper execution liên tục, giới hạn rủi ro, tổng exposure, thống kê tài khoản.
2. Mô hình leverage/liquidation và dữ liệu intrabar trước khi nghiên cứu đòn bẩy cao.
3. Thêm adapter thứ hai và kiểm thử tính năng từng sàn.
4. Authentication, quản lý secret và đối chiếu trạng thái lệnh trước khi xem xét giao dịch thật.

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

## Chart attribution

TradingView Lightweight Charts™ — Copyright (с) 2025 TradingView, Inc. [TradingView](https://www.tradingview.com/). Thư viện được dùng để render chart từ bộ dữ liệu MEXC đã lưu; không lấy giá từ TradingView và không kết nối tài khoản giao dịch.

### Ví mô phỏng realtime

Mở `http://localhost:3000` → **Ví mô phỏng**. Migration `0003` tạo ví
10.000 USDT ảo và sổ giao dịch riêng, giữ nguyên báo cáo backtest và tín hiệu cũ.
Không cần API key; không gửi lệnh hoặc dùng tiền thật trên MEXC.

- BTC_USDT USDT perpetual, Market Long/Short thủ công, một vị thế tại một thời điểm.
- Notional + phí mở tối đa 1× số dư; làm tròn số hợp đồng theo metadata MEXC.
- Giá bid/ask/fair từ public ticker khoảng mỗi 5 giây; UI tải ví mỗi 2 giây.
- Mở/đóng tại ask/bid theo chiều lệnh, cộng trượt giá bất lợi 2 bps; phí giả định
  5 bps mỗi chiều. Đây không phải phí thực tế của tài khoản.
- SL/TP dùng bid/ask đã quan sát; worker theo dõi ngay cả khi đóng trình duyệt.
  Khi worker/Docker dừng hoặc mất mạng, không có khớp lệnh trong thời gian đó.
  Khi kết nối lại, xử lý tại giá quan sát mới; không tái dựng tick bị bỏ lỡ.
- Khóa đặt/đóng khi quote nhận đã quá 20 giây, timestamp sàn quá 30 giây hoặc
  gặp lỗi nguồn giá. Số dư và PnL được giữ lại, UI báo dữ liệu chậm.
- Số dư trừ phí và funding, cộng PnL đã chốt. Equity cộng PnL chưa chốt theo
  fair price; chưa trừ phí đóng dự kiến. Vốn đang giữ = notional tại entry.
- Funding dùng rate quyết toán lịch sử, giá fair-price open 15m xấp xỉ, cập nhật
  trễ khi nến đã đóng (bao gồm vị thế đã đóng trước lúc dữ liệu được đăng).
  Ledger `(trade_id, settlement_time)` tránh tính trùng; lỗi funding hiển thị riêng.
- Wallet-row lock serialize mở/đóng, SL/TP và funding. Mỗi lệnh có `request_id`
  UUID để retry sau timeout không tạo thêm giao dịch hoặc trừ phí hai lần.
- Chart có entry/exit/SL/TP; nến hiện tại chỉ gồm tick đã quan sát. 120 nến đóng
  lấy từ cache MEXC đang đồng bộ trong workspace; marker lịch sử chỉ hiện nếu
  thời gian lệnh nằm trong phạm vi nến. Mức giá vẫn có thể xem qua từng dòng lệnh.
- Chưa có Limit, khớp một phần, liquidation, margin tiers hoặc ADL.
  Nút theo dõi tín hiệu và Auto Paper có điều khiển riêng.

API: `GET /api/paper/wallet`, `POST /api/paper/orders`,
`POST /api/paper/positions/{id}/close`. API docs tại `http://localhost:8000/docs`.
Ví và lịch sử lưu trong PostgreSQL qua restart container; không có chức năng tự reset.

### Auto Paper: bot tự giao dịch trong ví mô phỏng

**Ví mô phỏng → Auto Paper → lưu thông số → Bật Auto Paper.** Mặc định tắt.
Migration `0004` giữ nguyên ví/lịch sử, thêm nhãn nguồn `manual` / `auto`, thời gian
nến tín hiệu và nhật ký quyết định. Không có API key hoặc live execution.

- Structure Breakout Long/Short trên BTC_USDT 15m, cùng quy tắc với backtest.
  Đánh giá 21 nến đóng liên tục; vào bằng quote bid/ask nhận sau lúc đóng nến,
  cộng trượt giá. Không giả vờ khớp đúng giá open lịch sử như một lệnh đã xảy ra.
- Chỉ vào trong 60 giây sau đóng nến, một quyết định mỗi nến. Nến cũ/quá hạn,
  giá vượt SL/TP, thiếu dữ liệu, lỗi funding hoặc đang có vị thế đều không mở mới.
  Lý do hiện ở trạng thái và nhật ký (12 quyết định gần nhất).
- Bật/resume hoặc khởi động worker sẽ baseline tại nến đóng mới nhất và chờ nến
  kế tiếp. Không đặt bù tín hiệu trong thời gian tắt/mất kết nối/restart.
- Khối lượng = ngân sách rủi ro / khoảng cách giá khớp đến SL, giới hạn 1× vốn,
  làm tròn xuống bước hợp đồng. Ngân sách còn bị giới hạn bởi phần lỗ ngày còn lại.
  Phí/trượt giá/gap có thể làm lỗ thực tế vượt ngân sách SL.
- Mặc định rủi ro 1%, tối đa 5 lệnh auto/ngày UTC, ngưỡng lỗ equity 3%.
  Lỗ ngày so với equity được quan sát đầu tiên trong ngày UTC, gồm PnL chưa chốt
  và chi phí của toàn bộ ví; số lệnh chỉ đếm lệnh auto đã mở trong ngày đó.
  Đây là ngưỡng dừng mở mới, không phải bảo đảm lỗ tối đa hoặc lệnh đóng cưỡng chế.
- Chạm giới hạn sẽ tắt auto; không tự bật lại ngày sau. Không reset baseline khi
  sửa cấu hình hay bật lại cùng ngày. Chỉ đổi thông số khi auto đã dừng.
- Dừng auto vẫn giữ vị thế và SL/TP. Có thể đóng thủ công hoặc chọn **Dừng auto và
  đóng vị thế**. Khi giá stale, việc đóng chờ quote mới; nút dừng auto vẫn dùng được.
- Khi auto bật, khóa mở lệnh thủ công và chuyển nguồn sang synthetic. Đóng vị thế
  vẫn được phép. Nút **Bật theo dõi tín hiệu** ở Tổng quan là bộ theo dõi độc lập,
  không bật/tắt Auto Paper.
- Worker có các vòng độc lập: heartbeat, quote/SLTP (5s), tải nến (15s), funding
  (60s), auto (5s). Request lịch sử/funding chậm không chặn vòng quote/SLTP.
  Lỗi xử lý auto bất ngờ dừng vào lệnh mới và ghi trạng thái; SL/TP vẫn chạy.
- Wallet-row lock + quyết định khóa theo timestamp nến ngăn khớp lệnh trùng.
  Ghi tín hiệu dùng `ON CONFLICT DO NOTHING`, tránh đụng giữa monitor và auto.

API: `GET /api/paper/auto`, `POST /api/paper/auto/config`,
`POST /api/paper/auto/control`. Các route `/api/paper/orders` và đóng vị thế
vẫn dùng chung bộ khớp lệnh, tính phí và kế toán ví với auto.

Kiểm tra đồng thời trên PostgreSQL (tạo/xóa database test riêng, không đổi dữ liệu app):

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec -T api python -m backend.tests.check_auto_concurrency
```

### Xem lại lệnh Paper trên chart

Chart mặc định hiển thị vị thế đang mở (nếu có), hoặc giá realtime khi chưa có vị thế.
Chọn một hàng lịch sử để xem riêng lệnh đã đóng; bấm **Giá realtime** để trở về.
**Entry** là giá khớp mở, **Exit** là giá khớp đóng. Lý do thoát ghi rõ SL, TP hoặc đóng thủ công.
Bảng chi tiết hiển thị thời gian UTC, rủi ro theo khoảng cách Entry–SL, tỷ lệ mục tiêu/rủi ro thực tế, phí và funding.

Lệnh auto mới lưu bản chụp vùng 20 nến, giá đóng nến tín hiệu và ngân sách rủi ro vào quyết định cùng transaction mở lệnh.
Chart đánh dấu nến tín hiệu và hai biên vùng breakout. Lệnh cũ chưa có bản chụp được ghi rõ, không tái tạo lý do giả định.
Lệnh thủ công không có tín hiệu breakout. Nến xem lại đọc từ cache DB, tối đa 2.000 nến từ 24 nến trước tín hiệu;
không tự tải dữ liệu sàn hay thay đổi trạng thái bot khi xem lịch sử. Nến thiếu không được bù giả lập.
