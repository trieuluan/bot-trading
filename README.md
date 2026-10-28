# Signal Desk / bot-trading

Khung ứng dụng quản lý bot đa sàn: React + TypeScript, FastAPI + Python worker, PostgreSQL, Docker Compose.

**Trạng thái v0.2:** đã có dữ liệu public MEXC Futures BTC_USDT perpetual (15m), theo dõi breakout long/short và backtest lịch sử. Có thể chuyển về replay tổng hợp. Có ví Paper (khớp theo ticker, funding, SL/TP) và Auto Paper. Workspace MEXC có 3 mục: Giao dịch & Bot, Nghiên cứu, Cài đặt & Kết nối. Chart nhận nến đang chạy qua WebSocket public của MEXC (trình duyệt kết nối trực tiếp); khớp Paper, SL/TP và nến đóng vẫn dùng REST của worker. Chưa có tài khoản sàn hoặc giao dịch thật; live bị chặn. Không yêu cầu API key.

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

Bật dev một lần bằng cách thêm vào `.env` (file không commit, production không có dòng này):

```bash
# Set COMPOSE_FILE=compose.yaml:compose.dev.yaml in .env once.
docker compose up --build -d
```

Từ đó `docker compose up -d`, `exec`, `logs` tự dùng cấu hình dev. Dashboard vẫn ở http://localhost:3000, nhưng được phục vụ bằng Vite dev server với React Fast Refresh. Sửa file trong `frontend/src/` sẽ cập nhật giao diện ngay, không cần build lại container. Source được bind mount; `node_modules` Linux nằm trong volume riêng, không dùng chung dependency macOS. Polling được bật để nhận thay đổi file qua Docker trên macOS.

| Service    | Chế độ dev                                                                  |
| ---------- | --------------------------------------------------------------------------- |
| `frontend` | Vite + React Fast Refresh khi sửa giao diện                                 |
| `api`      | Uvicorn tự restart khi sửa `backend/app/`                                   |
| `bot`      | Watchfiles gửi SIGINT rồi restart worker khi sửa `bot/` hoặc `backend/app/` |
| `futures-learning` | Worker learner riêng; Watchfiles restart khi sửa Python |
| `exness-feed` | Watchfiles restart feed khi sửa `bot/` hoặc `backend/app/`                |
| `migrate`  | Đọc migration từ source mount, chạy theo lệnh; không tự theo dõi file       |
| `postgres` | Giữ nguyên PostgreSQL và volume dữ liệu                                     |

Python source, gồm `scripts/`, được mount chỉ đọc; polling nhận thay đổi file trên macOS. `research-output/` được mount đọc/ghi để API và worker dùng chung báo cáo replay. API và worker dùng image dev có pytest, Ruff và Watchfiles. Sau restart, worker đọc lại trạng thái và nến đã commit từ database; bật/tắt bot được giữ nguyên. Khi sửa chiến lược, thay đổi sẽ áp dụng từ lần xử lý kế tiếp sau restart. Script dùng source mới ở lần gọi tiếp theo, không tự chạy lại.

Khi sửa dependency trong `package.json`/lockfile, khởi động lại frontend để chạy lại `npm ci`:

```bash
docker compose restart frontend
```

Khi sửa `backend/requirements*.txt` hoặc Dockerfile Python, build lại các service Python:

```bash
docker compose up --build -d api bot exness-feed migrate
```

Khi thêm/sửa migration, áp dụng chủ động trước khi dùng code phụ thuộc schema mới:

```bash
docker compose run --rm migrate
```

Nếu thay đổi schema không tương thích với code đang chạy, dừng `api bot` trước khi migrate rồi chạy lại. Không chỉnh migration đã áp dụng; tạo revision mới.

Chạy kiểm tra bên trong image dev, database test được tạo riêng và không dùng PostgreSQL đang chạy:

```bash
docker compose exec api python -m pytest backend/tests -q
docker compose exec api ruff check backend bot migrations
docker compose exec api ruff format --check backend bot migrations
docker compose logs -f api bot
```

Quay lại Nginx và image Python production không có dev dependencies/watchers:

```bash
docker compose -f compose.yaml up --build -d
```

Trên máy production không đặt `COMPOSE_FILE`, hoặc đặt `COMPOSE_FILE=compose.yaml`, rồi dùng cùng lệnh `docker compose up -d`. `-f compose.yaml` ghi đè lựa chọn trong `.env` nếu cần chạy bản production trên máy dev. Không sao chép `.env` dev lên production. Compose chọn cấu hình từ môi trường, không tự đoán máy dev/production.

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

Futures learner chạy riêng: `python -m bot.learning_worker` (Compose tự chạy service
`futures-learning`, giới hạn 1 CPU/1 GiB). Bot realtime không chạy `learning_tick`.
Learner chỉ tính tín hiệu/mẫu; lớp vẽ được tải theo các cây đang xem qua
`GET /api/workspaces/mexc_futures/learning/chart`, không ghi chart vào JSON mỗi nến.
H1/H4 dùng chung kết quả gộp trong một lượt đánh giá, tách theo luồng và đầu vào;
cache được xoá sau mỗi lượt, không dùng lại giữa symbol hoặc mốc replay.
Khi nâng cấp cần thay cả container `bot` cũ và khởi động `futures-learning`, tránh
chạy learner hai nơi hoặc thiếu learner. Quote được commit trước transaction
SL/TP/thanh lý; transaction tiền vẫn khoá ví chung. Ghi quote và learner chỉ khoá
dòng theo symbol; riêng BTC vẫn dùng chung dòng 1 với số dư. Không đổi ngưỡng
vào lệnh 60 giây, độ tươi quote, tín hiệu hoặc Forex.

Nến đóng vẫn lấy qua REST, nhịp dự phòng 15 giây; worker canh thêm lượt đồng bộ
ở giây 2–3,8 sau mốc M15, lệch nhẹ giữa các cặp. PostgreSQL `LISTEN/NOTIFY`
đánh thức learner đúng symbol sau khi transaction nến mới commit; learner commit
quyết định mới thì đánh thức Auto Paper. Listener chỉ báo việc, không tính tín hiệu
hoặc đặt lệnh. Mỗi vòng xử lý vẫn tuần tự, đọc lại DB và giữ kiểm tra chống trùng.
Mất listener, restart hoặc dùng SQLite thì tiếp tục polling learner 30 giây và Auto
5 giây; không tăng tần suất REST liên tục, không đổi WebSocket giá hoặc SL/TP.
Độ trễ còn phụ thuộc dữ liệu sàn, mạng và kiểm định lịch sử; không bảo đảm 10–15 giây.

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

Tree có thêm hai feature H1/H4 (không cho dùng M15): `pdh_pdl` đóng cắt qua
đỉnh/đáy ngày UTC trước, cần đủ nến cả ngày trước và thân nến cùng chiều;
`adx_regime` dùng Wilder ADX(14) > 25, cộng DI thuận chiều lệnh, tối thiểu 42 nến
đã đóng. ADX đo sức mạnh; DI xác nhận hướng. Mốc ngày lấy từ nến H1/H4 đã gom
từ M15, không cần feed D1 riêng. Ba G0 mới: H4 trend + H1 ADX/PDH-PDL + M15 volume;
H4 trend + H1 ADX/EMA50 pullback + M15 pin bar; H4 trend + H1 (quét PDL/PDH + OI surge)
và M15 pin bar (`pdh_pdl_sweep`). Seed đồng bộ vào learner hiện có;
replay `--signal-seeds` chỉ xuất báo cáo nghiên cứu, không thay lịch sử learner,
không cộng mẫu replay thành forward và không tự thăng hạng.

`pdh_pdl_sweep` là leaf riêng H1/H4: long quét dưới PDL rồi đóng lại giữa PDL/PDH,
short quét trên PDH rồi đóng lại trong vùng; giá mở phải ở phía trong mốc quét,
thân nến cùng chiều lệnh. Không ghép `pdh_pdl` breakout với sweep vì khác điều kiện.
OI tăng không xác nhận lệnh thanh lý: seed chỉ lọc `oi_surge` tăng >=1% trong 1h,
dùng mẫu OI đã biết và còn mới; thiếu dữ liệu thì không phát tín hiệu. Cache replay
340 ngày không có OI, nên không dùng replay đó để kết luận hiệu quả seed này.

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

Mở `http://localhost:3000` → **Giao dịch & Bot**. Migration `0003` tạo ví
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
docker compose exec -T api python -m backend.tests.check_auto_concurrency
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

### Nghiên cứu price action đa khung

Chạy `PYTHONPATH=. python scripts/walkforward.py 12 30` để so sánh trên cache MEXC
`/tmp/walkforward-cache.json` (tải dữ liệu public nếu chưa có cache cùng số tháng).
"Tháng" ở script là 30 ngày, không phải tháng lịch. Cache có thể bị hệ điều hành dọn.

Biến thể `mtf(4h,1h,15m)` dùng giá đóng 4h so với EMA50 làm bộ lọc xu hướng;
1h đóng vượt vùng 20 nến trước tạo setup; sau đó 15m chạm vùng và một nến sau
đóng cùng chiều, giữ vùng mới xác nhận. SL theo cấu trúc retest, TP 2R.
Setup hết hạn sau 12 giờ hoặc hủy khi 15m đóng phá ngược vùng. Chỉ ghép các nến
1h/4h đầy đủ theo UTC; vào ở open 15m kế tiếp, không dùng nến khung lớn chưa đóng.

Các chiến lược cùng dùng 1.600 nến khởi tạo và cùng khoảng đánh giá; mỗi cửa sổ
30 ngày bắt đầu lại với vốn ảo. Đây là so sánh quy tắc cố định theo các cửa sổ
liên tiếp, không huấn luyện/tối ưu walk-forward. Dữ liệu đã được dùng nghiên cứu
các biến thể trước nên không phải bộ kiểm chứng độc lập chưa từng xem.
Giữ ngưỡng sàng lọc cũ: ít nhất 100 lệnh, PF gộp >1,3, ít nhất 60% cửa sổ có lãi,
drawdown từng cửa sổ <10%. Kết quả đa khung chưa đạt; Auto Paper vẫn giữ chiến lược cũ.

### Quan sát MTF trực tiếp (không đặt lệnh)

Worker có vòng `mtf-observation` (mỗi 15 giây) chỉ chạy khi nguồn là MEXC Futures. Sau mỗi
nến 15m đóng, nó đọc 1.600 nến đã đóng, tính xu hướng 4h/EMA50, breakout 1h, retest 15m và
nến xác nhận; ghi trạng thái cùng lý do vào `mtf_observation_state/events`. Bảng này tách
khỏi `signals`, Auto Paper và ví: kể cả khi `confirmed`, bot không tạo lệnh.

Lần chạy đầu đặt mốc quan sát từ nến kế tiếp. Sau gián đoạn, worker chạy bù tuần tự
từ cursor đã lưu; thiếu nến thật thì dừng chờ tải đủ, không hủy setup chỉ vì máy ngủ.
Trạng thái: chờ breakout, chờ
retest, chờ xác nhận, đã xác nhận, đã hủy, hết hạn (12 giờ). Trang **Chiến lược** hiện
vùng 1h, mức breakout, SL/TP tham chiếu, marker breakout/retest/xác nhận và nhật ký.
Kết quả backtest của biến thể này chưa đạt ngưỡng; dùng dữ liệu quan sát để kiểm chứng.

API: `GET /api/observations/mtf`.

### Dữ liệu chuẩn bị cho AI filter (chưa huấn luyện AI)

Migration `0006` thêm `mtf_research_samples`. Từ lúc triển khai, mỗi setup MTF **đã xác nhận**
ghi đúng một mẫu, kể cả khi được phát hiện qua catch-up. Setup hủy/hết hạn nằm trong nhật ký,
không bị gán nhãn thua. Feature chốt tại nến xác nhận: ATR14 15m (trung bình true range),
khoảng cách EMA50 4h theo ATR/chiều setup, độ rộng vùng breakout đã lưu, volume so với
20 nến trước, thân nến, khoảng SL, giờ UTC và số nến chờ. Chỉ dùng nến đã đóng;
`available_at` là thời điểm feature thực sự sẵn có. Funding feature hiện `null`, không giả là 0.

Đánh giá giả lập độc lập vào open nến 15m kế tiếp, dùng SL/TP đã chốt. Phí cố định 5 bps
và trượt giá 2 bps mỗi chiều; tối đa 96 nến (24h) rồi đóng theo close. Chạm cả SL/TP trong
một nến tính SL trước; gap qua SL thoát tại open, entry ngoài SL/TP đánh dấu invalid.
Mất nến đánh dấu censored, không đoán diễn biến đã bỏ lỡ. Mẫu pending/open chưa có nhãn.
`label=1` nghĩa PnL giả lập dương **sau phí/trượt giá, chưa tính funding**;
`net_r_ex_funding` không phải PnL giao dịch thật. Không mô phỏng khối lượng/lot, spread hay
thanh khoản; funding chưa đủ nên bộ dữ liệu chưa dùng để khẳng định lợi nhuận hoặc bật bot.

Xuất JSON phân trang: `GET /api/observations/mtf/dataset?after=0&limit=100` (tối đa 1000).
Dùng `next_after` cho trang tiếp theo. Feature và evaluation tách riêng để tránh dùng nhãn
tương lai làm đầu vào. Khi huấn luyện cần split theo thời gian và loại các mẫu train có
`label_available_at` chồng lên kỳ kiểm tra; không split ngẫu nhiên. Mẫu cùng thời điểm hoặc
chồng lấn vị thế không phải các phép thử độc lập. Không đổi chiến lược, Auto Paper hoặc ví.

### Đối soát SL/TP ví mô phỏng sau gián đoạn

Migration `0007` lưu tiến độ đối soát trong `paper_trades.monitoring`, tồn tại qua restart.
Vòng quote kiểm tra nến MEXC 15m đã đóng từ sau nến entry, theo thứ tự thời gian;
không giới hạn 96 nến. Nến entry bị bỏ qua vì high/low có thể xuất hiện trước lúc mở lệnh.
Thiếu nến thì chờ đồng bộ, không nhảy qua khoảng trống; UI hiện cảnh báo và Auto Paper chờ.
SL/TP theo bid/ask hiện tại vẫn chạy khi lịch sử chưa đủ. Khi có đủ lịch sử, giao cắt
đầu tiên kích hoạt khớp **giả lập tại SL/TP kèm trượt giá bất lợi**. Nếu mở nến vượt SL,
dùng giá mở nến; TP vẫn khớp tại mức TP, không hưởng giá gap tốt hơn. Cả hai chạm thì ưu tiên SL.
Thời điểm đóng là đầu nến nếu gap SL, hoặc giây cuối nến làm mốc ước lượng;
không biết chính xác tick chạm. Audit lưu `execution_model=historical_levels_v1`,
`time_basis`, thời điểm đối soát và funding đã hoàn tác sau thời điểm đóng giả lập.
Funding sau exit được đưa về 0 và lưu giá trị cũ trong audit, tránh tính hai lần khi retry.
Không tự sửa các lệnh đã đóng theo bản đối soát cũ.

Lý do `recovered_stop_loss` / `recovered_take_profit` phân biệt đóng sau đối soát với
đóng theo quote trực tiếp; lưu nến kích hoạt và trường hợp chạm cả hai trong audit.
Auto Paper tự tắt sau đóng đối soát, cần kiểm tra rồi bật lại thủ công.
Đây là quy tắc mô phỏng dựa trên nến last-price, không chứng minh bid/ask đã chạm mức đó.
Không tái tạo tick đã mất hoặc các giao cắt chỉ xảy ra trong nến entry. Máy ngủ, Docker
dừng hoặc mất mạng vẫn làm mất giám sát; không có stop order gửi lên sàn.

### MTF Replay và Catch-up

- **Catch-up tự động:** Worker xử lý tối đa 96 nến mỗi vòng, tiếp tục cursor sau restart.
  Không gửi lệnh. Thiếu nến thì tải bù rồi kiểm tra liên tục trước khi xử lý cả lô.
  Snapshot và mẫu xác nhận mang nhãn `catch_up` hoặc `live`; provenance nằm trong
  evaluation/audit, không đưa vào feature dự báo. Mẫu cũ chưa có nhãn nguồn được xem là live.
  UI hiện nhãn và số nến còn chờ; API dataset hỗ trợ `?provenance=live` hoặc `catch_up`.
- **Historical Replay ngoại tuyến:** Script dùng cùng observer, feature và bộ gán nhãn,
  duyệt cửa sổ đúng 1.600 nến đến từng thời điểm; chỉ tổng hợp 1h/4h đã đóng.
  Xuất JSON riêng (`provenance=historical`), không ghi vào DB trực tiếp hoặc ví.
  Lưu các chuyển trạng thái gồm hủy/hết hạn, feature xác nhận, kết quả giả lập và hash cache.
  Mẫu cuối chưa có tương lai vẫn pending/open; không bịa nhãn hoặc bù nến thiếu.

Chạy từ thư mục repo, với cache MEXC do `scripts/walkforward.py` tạo:

```sh
PYTHONPATH=. python scripts/mtf_historical_replay.py --cache /tmp/walkforward-cache.json
# Replay 30 ngày cuối, cộng warmup:
PYTHONPATH=. python scripts/mtf_historical_replay.py --days 30 --output research-output/mtf-30d.json
```

Mặc định xuất `research-output/mtf-historical-samples.json` (gitignored).
Historical và catch-up không phải quan sát thời gian thực độc lập. Phí giả định vẫn 5 bps,
slippage 2 bps mỗi chiều, chưa funding/spread; không coi đây là phí API thật hoặc bằng chứng
lợi nhuận, không tự chọn chiến lược hay bật Auto Paper. VPS chỉ cần nếu muốn xử lý thời gian thực.

Tổng hợp theo tháng/chiều và kiểm tra ATR: 60% mẫu đầu chọn trung vị ATR%,
40% sau đối chiếu; loại mẫu đầu có nhãn chưa biết tại mốc chia. Dataset đã xem
trước nên đây chỉ là phân tích thăm dò, không phải holdout độc lập.

```sh
python scripts/mtf_dataset_report.py --self-check
python scripts/mtf_dataset_report.py
PYTHONPATH=. python scripts/mtf_forward_check.py --self-check
PYTHONPATH=. python scripts/mtf_forward_check.py
```

`mtf_forward_check.py` kiểm tra hash cache/dataset/quy tắc, giữ nguyên ngưỡng trong
`research-output/mtf-report.json`, tải nến MEXC đã đóng sau dataset gốc rồi replay.
Chỉ setup xác nhận mới, đã có kết quả mới vào thống kê; pending/open được lưu riêng.
Báo cáo nằm ở `research-output/mtf-new-data-check.json`; không sửa cache gốc, DB hay
cấu hình giao dịch. Không tạo lại báo cáo chọn ngưỡng khi đang đối chiếu dữ liệu mới.
Đây vẫn là mở rộng lịch sử, không thay thế paper prospective. Tổng R mẫu có thể
chồng thời gian, không phải lợi nhuận danh mục.

### Workspace MEXC và Exness

- Bộ chọn workspace ở sidebar tách MEXC Crypto Futures và Exness Forex Demo (`XAUUSD`).
  Chuyển workspace chỉ đổi màn hình; không bật/tắt worker MEXC đang chạy.
- Migration `0008` tạo `workspaces` và kho nến `workspace_candles` có khóa gồm
  workspace, symbol, timeframe, price_type, time. Bid/ask không ghép thành last price;
  không tự tạo volume forex. Giữ nguyên toàn bộ bảng, ví, lệnh và cursor MEXC cũ.
- `GET /api/workspaces` liệt kê workspace; `GET /api/workspaces/{id}/dashboard`
  đọc trạng thái; `/api/workspaces/{id}/candles` đọc đúng kho nến với `timeframe`,
  `price_type`, `limit`. MEXC mặc định `last`, Exness mặc định `bid`; chưa có nến trả `[]`.
- API legacy mặc định thuộc MEXC. Truyền `workspace_id` hoặc header `X-Workspace-ID`
  của Exness tới API legacy bị từ chối HTTP 409; không chuyển lệnh sang ví MEXC.
- Exness hiện chỉ có nền workspace và nơi lưu nến. Chưa đồng bộ giá, chưa chạy MTF,
  chưa tạo ví forex hoặc gửi lệnh Demo. Các tab chưa hỗ trợ bị vô hiệu hóa, không
  hiển thị số dư, tín hiệu hoặc giá MEXC làm dữ liệu Exness.
- Đây là phân tách thị trường trong ứng dụng local, không phải tenant bảo mật đa người dùng.
  Không expose API ra Internet khi chưa có authentication/authorization.

Chạy migration trước khi dùng API workspace: `alembic upgrade head`.

### Đồng bộ nến Exness XAUUSD

- `exness-feed` là process Docker riêng với worker MEXC. Chỉ GET dữ liệu Exness;
  không đọc/sửa ví, tín hiệu, cursor hoặc lệnh MEXC. Khóa Exness chỉ truyền vào service này,
  không truyền vào API, frontend hoặc bot MEXC. Dùng cấu hình `EXNESS_ENVIRONMENT=demo`.
- Bật `EXNESS_DATA_ENABLED=true` trong `.env` để process đồng bộ tự động mỗi 15 giây.
  Để `false` nếu chưa cấu hình đủ bốn biến Exness. Sửa `.env` cần recreate service.
- Đồng bộ trực tiếp M15, H1, H4 từ Exness, không tổng hợp H1/H4 bằng nến MEXC.
  Chỉ lưu nến đã đóng, xác thực OHLC và bid/ask; thiếu volume giữ null, không thay bằng 0.
  `tick_volume` giữ trường volume của nguồn, không phải volume hợp đồng futures.
- Giới hạn số nến và nhịp gọi đọc từ API limits của tài khoản. HTTP 429 có cooldown;
  lỗi ghi trạng thái rồi retry, không tiến cursor. Một page bid/ask và cursor commit cùng nhau.
- Cursor theo khoảng thời gian đã kiểm tra, không theo nến cuối. Page rỗng trong phiên nghỉ
  vẫn tiến qua khoảng đã yêu cầu; không bịa nến để lấp chỗ trống. Khi đã bắt kịp, kiểm tra
  lại hai nến gần nhất tối đa mỗi phút để cập nhật nến nguồn sửa lại.
- Backfill mặc định 90 ngày lịch; `--days` chỉ dùng khi khởi tạo frame chưa có cursor.
  Chạy lại tiếp tục cursor, không tải lại từ đầu hoặc tự mở rộng về quá khứ.
  Không chạy backfill song song với worker để tránh tranh lượt giới hạn API của tài khoản.

```sh
docker compose build api migrate exness-feed frontend
docker compose run --rm --no-deps migrate
# Tải ban đầu trước khi bật process chạy nền:
docker compose run --rm --no-deps exness-feed python -m backend.app.exness_feed --days 90
docker compose up -d --no-deps exness-feed api frontend
```

Dashboard Exness hiển thị số nến mỗi phía, khoảng đầu/cuối, lần đồng bộ gần nhất,
heartbeat và lỗi/cooldown của từng frame; UI cập nhật mỗi 15 giây.
Backlog là số khoảng nến cần kiểm tra, không phải số nến thị trường bị thiếu.
Kho nến có thật không đồng nghĩa đã kiểm tra lịch phiên hoặc chứng minh lịch sử đầy đủ:
cần phân biệt giờ nghỉ, cuối tuần và khoảng thiếu từ nhà cung cấp trước khi bật MTF forex.
Gập máy vẫn dừng Docker local; worker chạy bù sau khi máy thức, không thay thế VPS 24/7.

### MTF Exness chỉ quan sát

- Migration `0010` tạo `forex_observations`, tách khỏi trạng thái MTF và ví MEXC.
  `exness-feed` chạy quan sát sau đồng bộ, không tạo signal giao dịch hoặc gọi endpoint lệnh.
- Kiểm tra OHLC, timestamp đúng khung, cặp bid/ask, giá ask không thấp hơn bid;
  lỗi hoặc backlog bất kỳ khung nào sẽ chặn xử lý. Dashboard hiển thị spread close và khoảng trống.
  Chưa đối chiếu lịch phiên broker, nên không khẳng định khoảng trống là phiên nghỉ hay mất nến.
- Dùng nến bid gốc Exness: EMA50 trên 4h đã đóng xác định hướng, 1h đóng vượt
  đỉnh/đáy 20 nến trước tạo setup; M15 kế tiếp chạm vùng rồi một nến sau xác nhận giữ vùng.
  Không tổng hợp H1/H4 từ M15, không dùng nến chưa đóng hoặc volume để lọc tín hiệu.
- Vùng setup giữ nguyên; hủy nếu M15 đóng phá ngược, 4h đổi hướng, hoặc có khoảng trống M15;
  hết hạn sau 12 giờ tính từ đóng nến breakout. SL theo cực trị từ retest đến xác nhận.
  TP 2R tính từ close bid xác nhận chỉ là tham chiếu, chưa tính spread hoặc giá khớp.
- Lần đầu quan sát từ nến M15 mới nhất; không tự replay 90 ngày thành kết quả nghiên cứu.
  Sau restart tiếp tục từng nến chưa xử lý, tối đa 96 nến mỗi vòng; gắn nhãn `catch_up`/`live`.
  Mỗi bước chỉ dùng H1/H4 đã đóng tại thời điểm bước đó. Nhật ký lưu cả trạng thái chờ và hủy.
  UI tách tiến độ tải bù từng khung với số nến M15 đã tải còn chờ phân tích và mốc nến đã tính.
  Trạng thái worker ngoại tuyến hoặc lỗi dữ liệu luôn được ưu tiên, không báo đang chạy bù khi worker đã dừng.
  Test mô phỏng nghỉ 6/30 giờ đối chiếu từng snapshot với chạy liên tục, gồm nhiều trang tải bù,
  giới hạn 96 nến mỗi lượt, nhãn nguồn và xử lý lại không trùng bản ghi; không chạm DB thực.
- Quan sát không gửi lệnh Exness. Ví paper forex cục bộ mô tả bên dưới; chưa khớp trên tài khoản Demo sàn.
  Replay nghiên cứu bid/ask mô tả ở mục kế tiếp; lịch phiên vẫn cần xác minh.

### Replay nghiên cứu MTF Exness

Trong workspace **Exness · Forex Demo**:

- **Chiến lược:** chart XAUUSD chọn M15/H1/H4 và bid/ask. Vùng H1, mức breakout,
  SL/TP và dấu retest/xác nhận/hủy nằm trên chart Bid M15. Dữ liệu đều lấy từ Exness.
- **Nhật ký:** 20 nến quan sát gần nhất, lý do chờ/hủy và nhãn trực tiếp/chạy bù.
- **Backtest:** đọc báo cáo replay đã tạo, không tự chạy replay hoặc đặt lệnh từ UI.
  Chỉ so sánh độ nhạy trượt giá khi SHA256 bộ nến giống nhau.
- API mount thư mục `research-output` ở chế độ chỉ đọc. Không thấy file thì UI báo chưa có
  kết quả; không lấy kết quả MEXC thay thế. Sau sửa mount cần recreate container API.

`scripts/exness_mtf_replay.py` chỉ đọc kho nến, xuất JSON riêng; không sửa nhật ký quan sát,
ví paper hoặc lệnh. Lịch sử 90 ngày bao gồm warmup EMA50 H4; thời gian đánh giá ngắn hơn 90 ngày.

```sh
mkdir -p research-output
docker compose run --rm --no-deps \
  -v "$PWD/scripts:/app/scripts:ro" \
  -v "$PWD/research-output:/app/research-output" \
  exness-feed python -m scripts.exness_mtf_replay

# So sánh danh mục 1 ví ($10,000, 1% risk, max 1 vị thế) cho 4 chiến lược (gồm regime_router):
docker compose exec api python -m scripts.exness_mtf_replay --compare
```

Đo spread XAUUSD từ kho nến và lấy mẫu stream đang chạy trong 60 giây (chỉ đọc):

```sh
docker compose exec -T api python - --seconds 60 < scripts/exness_cost_audit.py
```

Báo cáo: `research-output/exness-cost-audit.json`. Spread lịch sử dùng open/close M15,
không phải tick đồng bộ. Mẫu live lấy khoảng 1 Hz, bỏ quote cũ và trùng.
Replay đã tính spread bid/ask; không cộng spread báo cáo lần nữa. Slippage thật cần
giá khớp từ broker và bid/ask tại thời điểm gửi lệnh; giá khớp paper không đủ.
Thiếu commission không đồng nghĩa phí bằng 0. Script không gửi lệnh và không sửa cấu hình chi phí.

- Tín hiệu chỉ từ nến đã đóng; vào giả định ở open M15 kế tiếp: long mua ask, short bán bid.
  Long thoát theo bid OHLC, short thoát theo ask OHLC; spread đã nằm trong hai phía giá.
  SL giữ mức bid của cấu trúc retest; TP tính lại 2R từ entry giả định, không từ close xác nhận.
- Nếu open vượt SL: thoát ở open, không bịa giá SL. Open vượt TP: lấy TP bảo thủ.
  Nếu cùng nến chạm SL và TP: ưu tiên SL; báo số nến mơ hồ. Timeout sau 96 nến M15.
- Khoảng trống chưa xác minh lịch phiên: mẫu đang chờ/mở chuyển `censored`, không tính vào
  thống kê lãi/lỗ. Mẫu còn mở cuối dữ liệu cũng bị censored, không ép đóng.
- `--slippage-bps` là trượt giá mỗi phía, `--commission-per-unit` là hoa hồng khứ hồi
  theo đơn vị giá cho một đơn vị XAU, không phải theo lot. Mặc định cả hai bằng 0,
  chưa hiệu chỉnh theo tài khoản; chưa tính swap. Không coi kết quả là lợi nhuận ròng thực tế.
- `--as-of <UTC epoch seconds>` cố định thời điểm cắt dữ liệu; báo cáo lưu SHA256 dữ liệu,
  giả định, chuyển trạng thái, từng mẫu, thống kê theo chiều và tháng UTC.
  Mẫu có thể chồng lấn: tổng R/drawdown chỉ là thống kê mẫu đóng, không phải đường vốn tài khoản.
- Đây là replay mô tả trên dữ liệu đã có, chưa out-of-sample hoặc chứng minh hiệu quả.

### Giá trực tiếp Exness

### So sánh chiến lược và ví thử nghiệm Exness

`--compare` xuất `research-output/exness-strategy-compare.json`, hiển thị ở **Backtest**.
Ba chiến lược dùng cùng bộ nến Bid/Ask, cùng mô hình khớp với paper và ba mức trượt giá
0/1/2 bps mỗi chiều. Mỗi lượt có một ví $10,000 độc lập, risk tối đa 1%, notional tối đa
1 lần vốn, chỉ một vị thế, không dùng leverage. Vào ở open M15 kế tiếp, TP 2R, không timeout.
Chỉ nến đóng được dùng; `--as-of` và SHA256 ghi lại thời điểm/bộ dữ liệu. Chưa kiểm định ngoài mẫu.

- `breakout_retest`: giữ luật H4/H1/M15 hiện có, SL theo cấu trúc retest.
- `trend_pullback`: H1 EMA20 trên/dưới EMA50, close cùng phía EMA20; M15 chạm EMA20
  của 60 nến M15 trước đó rồi đóng cùng chiều. SL lấy cực trị nến xác nhận và nến trước.
- `range_rejection`: range 20 H1 đã đóng trước nến tín hiệu; độ dịch chuyển close đầu/cuối
  không quá 50% chiều rộng range; M15 chạm vùng 10% sát biên rồi đóng trở lại phía trong.
  SL tại cực trị nến từ chối. Đây là luật thử nghiệm, không khẳng định có lợi thế.

Số dư, return và drawdown chỉ tính **lệnh đã đóng**, không phải equity cuối kỳ. Vị thế gặp
gap chưa xác minh giữ nhãn `censored` và **chặn ví**, không xóa lệnh để tiếp tục với vốn
giả định còn nguyên. UI hiện thời điểm chặn và số setup bị từ chối. Báo cáo bị chặn chỉ là
kết quả từng phần, không dùng để xếp hạng chiến lược. Commission chưa xác minh, swap,
lot và margin thực chưa được hiệu chỉnh; không coi P&L mô hình là lợi nhuận ròng thực tế.

**Ví mô phỏng** giữ nguyên `live`/`replay` cho Breakout + Retest và thêm `trend`/`range`.
Hai ví mới khởi tạo tại nến mới nhất khi worker chạy, không nhập lịch sử vào số dư forward.
Máy mở lại thì chạy bù tối đa 96 nến mỗi lượt, theo đúng thứ tự; gọi lại không tạo lệnh trùng.
Hai ví thử nghiệm dùng nến đóng, không dùng tick để thoát; UI có trạng thái chờ, lý do,
số setup xác nhận/được duyệt, lý do bỏ qua, drawdown và CSV riêng từng ví.
Không thay đổi ví MEXC, không gửi lệnh Demo hay tiền thật tới Exness.

### Giá trực tiếp Exness

- API giữ một WebSocket XAUUSD Bid/Ask mỗi process khi `EXNESS_DATA_ENABLED=true`, ký GET
  theo access point tài khoản, đọc limits trước khi subscribe; không gửi lệnh broker.
- Frontend nhận SSE `/api/workspaces/exness_forex/quotes/stream` tối đa mỗi giây. Nhiều tab
  dùng chung upstream. Chạy một uvicorn worker; nhiều process/replica chưa chia sẻ quota.
- Chart ghép nến Bid/Ask M15/H1/H4 từ mọi tick backend nhận; SSE gửi OHLC mỗi giây,
  giữ tối đa 32 nến mỗi khung trong RAM. Tick cực trị giữa hai lần SSE vẫn được tính.
  Nến đầu sau kết nối hoặc gián đoạn quá 15 giây mang nhãn OHLC tạm thời/thiếu tick.
  OHLC tick có thể khác nến sàn; nến REST đã đóng luôn được ưu tiên thay thế.
  Nến live chỉ để xem; MTF và giá vào paper vẫn dùng nến đóng.
  Mọi tick hợp lệ được xử lý SL/TP ví Live trong DB, không phụ thuộc tab trình duyệt hoặc nhịp SSE.
- Tick quá 15 giây hoặc mất stream: ẩn nến/đường live, báo dữ liệu cũ/chưa có tick.
  Gập máy vẫn ngắt kết nối; nối lại với backoff, REST worker tiếp tục bù nến.
- Lỗi quyền, subscribe hoặc quota dừng stream, không lặp retry. Kiểm tra cấu hình/quota
  trước khi restart API. Key chỉ ở backend, không chuyển ra frontend.

### Ví paper Exness cục bộ

- Worker Exness tự chạy paper sau bước quan sát khi `EXNESS_DATA_ENABLED=true` và cả ba khung
  đã đồng bộ, hợp lệ. Migration `0011` lưu tài khoản, checkpoint và lệnh trong bảng forex riêng.
  Không gửi lệnh tới Exness, không đọc hoặc đổi ví MEXC. Không mở lệnh từ lịch sử trước lúc khởi tạo.
- Tab **Ví mô phỏng** có hai ví khởi tạo 10,000 USD: **Live** cho tín hiệu mới xử lý kịp lúc;
  **Replay** chỉ cho tín hiệu bỏ lỡ/chạy bù. Lệnh Live đang chờ mà bỏ lỡ nến vào chuyển sang Replay.
  Kết quả hai ví không gộp; UI hiển thị vị thế, số dư, equity, P&L đã/chưa chốt, lịch sử và lý do bỏ qua.
- Mỗi ví chỉ một vị thế. Rủi ro mục tiêu 1% số dư, notional tối đa 1 lần số dư, khối lượng theo oz,
  chưa hiệu chuẩn lot/minimum/margin sàn. Long vào Ask open M15 kế tiếp, thoát Bid; Short ngược lại.
  Chỉ ghi nhận khớp sau khi nến đóng; TP 2R theo giá vào, SL cấu trúc retest. Không timeout vị thế.
- Spread dùng bid/ask thực; trượt giá giả định 2 bps mỗi chiều, commission khứ hồi 0 USD/oz,
  chưa tính swap, chưa hiệu chuẩn chi phí tài khoản. Giả định lưu trên từng lệnh; không coi là P&L thực tế.
- Bật lại máy: xử lý tối đa 96 nến mỗi tick, lưu checkpoint và tiền trong cùng transaction;
  SL/TP tính tại nến lịch sử đầu tiên chạm, không lấy giá hiện tại. Gap open vượt SL dùng open bất lợi;
  vượt TP dùng TP bảo thủ; cùng nến chạm SL/TP ưu tiên SL và ghi nhãn mơ hồ.
- Ví Live đang mở: tick mới (tối đa 15 giây tuổi) kiểm tra SL/TP theo Bid cho Long, Ask cho Short.
  Chỉ xử lý khi checkpoint đã đến hết M15 trước; cùng khóa workspace với worker để tránh chốt hai lần.
  SL dùng giá tick quan sát cộng trượt giá; TP dùng mức TP bảo thủ cộng trượt giá.
  Khi bắt đầu theo tick, reconnect, lỗi ghi DB hoặc thiếu tick quá 15 giây: nến M15 hiện tại
  phải đóng và được đối soát trước khi tiếp tục live, tránh TP mới che mất SL cũ (hoặc ngược lại).
  Mất tick không có nghĩa chắc chắn mất dữ liệu; chính sách này cố ý bảo thủ.
  Ví Replay, tín hiệu MTF, giá vào và trạng thái ví MEXC không đổi; không gửi lệnh broker.
- Khoảng trống M15 chưa xác minh khi có vị thế: ghi `censored`, khóa mở lệnh mới, equity chưa xác định.
- Nghỉ phiên XAUUSD thường lệ được phân loại theo giờ New York (DST Mỹ), chỉ chấp nhận khi toàn bộ nến thiếu nằm trong giờ đóng cửa. Giữ vị thế mở qua phiên, xét SL/TP bằng giá mở cửa Bid/Ask kế tiếp; hủy tín hiệu pending, không vào muộn. Báo cáo ghi thời điểm/số nến thiếu nếu còn gap bất thường. Chưa có lịch lễ/ngoại lệ đầy đủ; không tự lấp nến hoặc mở khóa ví đã bị chặn từ lần chạy cũ.
  Không bịa P&L hoặc giả định đã thoát. Chưa tự mở khóa; cần đối soát dữ liệu trước khi bổ sung xử lý phiên.
- Kiểm thử `backend/tests/test_forex_paper.py` dùng SQLite riêng: SL/TP Long/Short, spread,
  gap, ngủ máy, rollback, idempotency, chạy bù nhiều batch và không thay đổi ví MEXC.

### Nhật ký lệnh và báo cáo ví paper Exness

- **Nhật ký chi tiết (Journal v2):** Mỗi lệnh lưu đầy đủ nguồn gốc tín hiệu (xu hướng 4H, EMA 4H,
  mốc breakout 1H, nến retest 15M, lý do xác nhận), giá mở Bid/Ask, spread thực tế tại thời điểm vào/thoát,
  khối lượng theo oz và quy đổi lot, mức trượt giá mô phỏng (USD) và P&L thô vs P&L ròng.
- **Xuất CSV:** Endpoint `/api/workspaces/exness_forex/paper/trades.csv?channel=live|replay`
  cho phép tải toàn bộ nhật ký chi tiết với timestamp UTC giây để phân tích độc lập.
- **Báo cáo hiệu suất:** Tách biệt hoàn toàn hai mẫu Live và Replay. Thống kê kỳ vọng R/lệnh (Expectancy R),
  tổng R, Profit Factor, Max Drawdown đỉnh-đáy trên số dư đã chốt, tỷ lệ thắng và phân nhóm theo chiều,
  nguồn thoát (tick vs nến đóng) và tháng giao dịch. Không gộp hoặc so sánh trực tiếp hai tập dữ liệu khác thời điểm.
- **Hiệu chuẩn thông số Exness Demo:** Migration `0012` lưu thông số công cụ từ API Exness (`/conditions`).
  Áp dụng quy tắc làm tròn lot xuống (`volume_step` 0.01 lot = 1 oz, tối thiểu 0.01 lot).
  Nếu khối lượng theo rủi ro nhỏ hơn lot tối thiểu, lệnh bị từ chối thay vì tăng quy mô ép vào lệnh.
  Ghi rõ các khoản chưa thể xác minh tự động (như đơn vị swap, giờ rollover, HMR động) thay vì mặc định chi phí bằng 0.

### Exness Demo: lệnh trên sàn

**Runtime hiện tại: HTF AST V2 đa cặp.** Các mô tả family/learner/paper bên dưới là tài liệu
lịch sử, không phải đường sinh tín hiệu đang hỗ trợ. V2 dùng H4 context, H1 setup/SL/TP,
M15 trigger; không có cản hợp lệ hoặc runner R/R sau chi phí dưới 1.5 thì bỏ setup.

- `EXNESS_SYMBOLS` nhận danh sách 11 cặp, bỏ trùng; khi để trống dùng `EXNESS_SYMBOL`
  (mặc định XAUUSD). Compose/example cấu hình đủ 11 cặp, 66 setup toàn tài khoản và 6 mỗi cặp.
- Một worker cấp nến M15/H1/H4; mỗi cặp có 56 cây, ledger, mẫu và fitness riêng.
  Migration DB `0024` gán lịch sử cũ cho XAUUSD. Worker chuyển ledger XAUUSD dưới lock,
  lưu archive/manifest và giữ nguyên mẫu/dòng dõi; 10 cặp còn lại tạo G0 sạch, không replay forward.
- TP1/Runner và các leg cùng setup chỉ chiếm một suất. Intent chờ/đang gửi cũng giữ một suất;
  đóng một phần chưa giải phóng suất khi còn runner hoặc leg chưa đối soát. Ticket ngoài bot hoặc
  chưa xác minh được cùng setup tính riêng từng suất, không bị bot quản lý. Lệnh chưa rõ kết quả phải reconcile.
- Giữ tên biến `EXNESS_DEMO_MAX_POSITIONS` và `EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL`, nhưng đơn vị là setup:
  cấu hình triển khai 66 toàn tài khoản, 6 mỗi cặp; không chia đôi khi setup tách ticket.
- Specs/commission và tỷ giá bid/ask đã đóng được chụp theo thời điểm; thiếu, stale hoặc sai
  tài khoản/cặp thì chặn entry. Hiện chỉ hỗ trợ tài khoản demo USD; không đoán phí hoặc tỷ giá.
- URL/API Forex nhận `symbol`, mặc định XAUUSD; ngoài danh sách cấu hình trả 422.
  `/api/workspaces/exness_forex/markets` trả giá/timestamp, feed, specs, vị thế và lý do chặn từng cặp.
  Ví/equity vẫn chung tài khoản; trình duyệt chỉ render chart đang chọn, không điều khiển lịch feed.
- Entry cần đồng thời `FOREX_HTF_V2_EXECUTION_ENABLED`, `EXNESS_DEMO_EXECUTION_ENABLED`,
  `EXNESS_DEMO_ORDERS_ENABLED` và control tài khoản. Thay code/example không bật các cờ local.
  Kiểm thử dùng broker giả, không cần chạy worker. Triển khai/migration dữ liệu thật là bước riêng.

#### Sizing Auto Demo: 1% equity mỗi setup

- Ngân sách SL = 1% equity từ snapshot broker. Tổng lot = ngân sách / (khoảng cách entry–SL × contract size × tỷ giá lỗ về USD), làm tròn xuống theo bước lot và giới hạn broker. Không còn trần 0.10 lot hay chặn notional $50,000 cho Auto.
- TP1/Runner dùng chung ngân sách setup, không phải 1% mỗi ticket. Lưu equity/ngân sách lúc lập kế hoạch; trước mỗi lần gửi, đối chiếu equity mới và không vượt ngân sách gốc. Thiếu/không hợp lệ equity thì không mở lệnh; không fallback sang $25. Các biến cũ `EXNESS_DEMO_MAX_LOT` và `EXNESS_DEMO_RISK_PER_TRADE_USD` không còn áp dụng.
- 1% là rủi ro giá tới SL, chưa gồm phí/swap/trượt giá, không phải margin hay bảo đảm lỗ tối đa. Giới hạn tính theo setup và broker kiểm tra margin. Không thay lot/SL/TP của lệnh đã mở; lệnh kiểm tra thủ công vẫn 0.01 lot.
- Thanh tài khoản hiện ngân sách setup và đòn bẩy cấu hình của cặp đang xem từ specs cache, không thêm API broker/polling. Theo Exness API: `forex`/`cfd_leverage` dùng account leverage, `forex_no_leverage`/`cfd` dùng nghịch đảo `margin_percent` (tỷ lệ, không chia thêm 100). Đây chưa phải mức HMR realtime; specs thiếu/stale hiện `—`.
- Replay SL/TP hiện có vẫn dùng tài khoản tham chiếu $2,500 (ngân sách $25), không lấy equity broker hiện tại để sửa kết quả lịch sử; không cộng kết quả replay vào rủi ro giao dịch.

#### Tài liệu execution cũ (chỉ tham khảo lịch sử)

Đặt `EXNESS_DEMO_EXECUTION_ENABLED=true` trong `.env`, chạy `docker compose up -d`
sau migration `0014`. Executor chạy trong `exness-feed`, tách khỏi ví paper/replay và MEXC.
Tài khoản phải được API xác nhận `is_demo=true`, USD, XAUUSD; không hỗ trợ tài khoản real.

- `regime_router` chỉ gửi tín hiệu M15 đóng mới, trong 120 giây; bỏ qua tín hiệu trước lúc bật bot.
- Mỗi lệnh 0.01 lot, tối đa `EXNESS_DEMO_MAX_POSITIONS` vị thế toàn tài khoản (mặc định 1, trần 66, khuyến nghị triển khai đa cặp 66) và tối đa `EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL` vị thế mỗi cặp (mặc định 6, trần 6), không giới hạn số lệnh/ngày. Hỗ trợ 11 cặp: XAUUSD, EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCAD, USDCHF, EURJPY, GBPJPY, EURGBP qua `EXNESS_SYMBOLS`.
- SL theo cấu trúc, lùi thêm `max(0.25 × ATR14 M15, spread)` (chiến lược tự sinh đã có ATR nên không lùi thêm). TP tại cản H1/H4 gần nhất trừ đệm, không có cản thì 2R; cản dưới 1R thì bỏ tín hiệu. R thực tế thay đổi theo giá khớp.
  Bỏ qua nếu mức lỗ dự kiến tới SL vượt $25; đây không phải bảo đảm mức lỗ tối đa khi gap/slippage.
- SL/TP đặt tại Exness, tiếp tục có hiệu lực khi Mac ngủ. Đóng trước kỳ nghỉ theo lịch chuẩn
  cần worker đang hoạt động; không bảo đảm đóng trước ngày lễ bất thường hoặc lúc máy offline.
- Learner thử 16 biến thể SL/TP: đệm SL `k ∈ {0, 0.25, 0.5, 1.0}` × TP cấu trúc / 1.5R / 2R / 3R.
  Chiến lược tự sinh giữ ATR stop sẵn có và chỉ học TP. Mẫu thoát lệnh tách khỏi mẫu chọn chiến lược.
  Mỗi bối cảnh (chiến lược, chiều lệnh, xu hướng H1, phiên UTC) chỉ mở một nhóm mô phỏng tại một thời điểm.
  Dùng open nến kế tiếp, Bid/Ask, commission, slippage, lot theo ngân sách, TP1, hòa vốn, trailing và thoát sớm.
  Cùng nến chạm SL/TP ưu tiên SL; gap và mẫu chưa đóng sau 96 nến bị loại, không coi là lợi nhuận đã chốt.
  Chọn ứng viên trên train, yêu cầu ít nhất 30 cặp mẫu train và 30 cặp validation đã đóng, lợi nhuận và chênh lệch
  so với mặc định đều có cận dưới dương. Chưa xác minh commission/slippage thì chỉ khuyến nghị, không tự đổi lệnh demo.
  Mặc định `k0.25_structural` giữ nguyên khi thiếu bằng chứng; lệnh đang mở không thay chính sách.
  Đây là mô phỏng OHLC M15, không tái dựng tick/độ trễ gửi hai phần lệnh; chưa tính swap hoặc xác nhận lợi nhuận thật.
- Quần thể tự sinh mở rộng từ 10 họ chiến lược: `channel`, `rsi`, `fvg`, `session`, `squeeze`, `sweep`,
  `pullback`, `range`, `retest`, `router` (tối đa 40 cá thể, 2–4 cá thể/họ để tránh một họ lấn át).
  Mỗi cá thể có bộ gen tham số, bộ lọc xu hướng H1, thế hệ (`generation`) và danh sách cha mẹ (`parents`).
  Tiến hóa chỉ dùng nhãn đóng trước cutoff huấn luyện; cá thể có R âm sau 40 mẫu bị loại, đột biến và lai chéo
  sinh biến thể con cho thế hệ sau. Gen tự sinh (`rule_tree`) được phép vào Auto Paper ở chế độ học thử;
  để được thăng hạng và chạy lệnh thật vẫn cần vượt qua cổng forward (100 lệnh, PF > 1.3, DD < 10%).
  Để được duyệt vào lệnh demo, gen phải đạt ít nhất 30 mẫu train và 30 mẫu validation, cận dưới R dương
  ở cả hai tập, chi phí sàn đã được đo; không chuyển đổi tự động khi thiếu bằng chứng.
- Ghi intent trước HTTP, idempotency key theo tín hiệu; ACK không đồng nghĩa đã khớp.
  Đối soát operation/order/deal/position ID. Kết quả chưa rõ chặn lệnh mới, không tự gửi lại.
- Dùng khóa theo tài khoản để chặn executor trùng và không chạy đồng thời probe calibration.
- Workspace Exness → **Bot của bạn**: balance/equity sàn, lệnh đang mở/đã đóng, deal/order ID,
  chart Bid/Ask live, dấu vào/thoát thực tế, SL/TP, tạm dừng mở lệnh và yêu cầu đóng vị thế của bot.
  Lệnh probe/thủ công được ghi nguồn riêng, không tự đóng lệnh ngoài bot.
- Lịch sử nhập ban đầu 24 giờ rồi lưu tăng dần trong DB, đọc theo trang; profit lấy từ deal sàn,
  không tự coi là net sau commission/swap. Khi mở máy lại, đối soát lịch sử sàn thay vì giả lập fill.

API trạng thái: `GET /api/workspaces/exness_forex/execution` (có `offset`, `limit`).
`PATCH` cùng URL với `{"enabled": false}` chỉ dừng mở mới, không hủy SL/TP.
Giữ API bind localhost; không đưa các endpoint điều khiển lên Internet khi chưa có xác thực.
