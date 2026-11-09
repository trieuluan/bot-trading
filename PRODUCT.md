# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Trader và hệ thống operator theo dõi bot giao dịch tự động, phân tích tín hiệu và kiểm soát paper trading realtime trên crypto (MEXC) và forex.

## Product Purpose
Signal Desk là trung tâm điều hành và giám sát hệ thống bot trading: theo dõi trạng thái worker, kiểm tra tín hiệu chiến lược đa khung thời gian, đọc biểu đồ nến tương tác, quản lý danh mục lệnh paper và nhận diện cảnh báo rủi ro kịp thời. Thành công là nắm bắt thị trường tức thì, không chậm trễ, không nhầm lẫn trạng thái hoạt động của bot.

## Positioning
Dashboard trading chuyên dụng (Operate-first) kết hợp biểu đồ nến hiệu năng cao và bảng tín hiệu trực quan. Tối ưu cho mật độ thông tin dày, quét nhanh số liệu quan trọng, phản xạ thao tác dứt khoát.

## Operating Context
- Sử dụng chủ yếu trên màn hình desktop khi phiên giao dịch đang diễn ra hoặc màn hình phụ treo giám sát.
- Nhận luồng dữ liệu stream WebSocket và polling REST từ worker FastAPI.
- Nhãn DEMO DATA / PAPER MODE luôn định danh rõ ràng, ngăn ngừa nhầm lẫn với tài khoản thật.

## Capabilities and Constraints
- Tech stack: React 19, TypeScript, Tailwind CSS v4, Vite, lightweight-charts, Lucide icons.
- Surface chính: Forex Strategy Page, Strategy Overlay, Paper Wallet, Connection & Worker Status.
- Ràng buộc: Giữ tính toàn vẹn của chart và dữ liệu thị trường; trạng thái điều khiển bị khóa khi backend/worker mất kết nối; ưu tiên độ tương phản cao cho các vùng cảnh báo.

## Brand Commitments
- Tên: Signal Desk.
- Phong cách: Kỹ thuật, chuẩn xác, kỷ luật, tin cậy. Điểm nhấn thị giác mạnh (bolder) ở các khu vực cảnh báo trạng thái và tín hiệu vào lệnh.

## Evidence on Hand
- Triển khai giao diện hiện có tại `frontend/src/`.
- UI concept tham khảo tại `docs/ui-concept.md`.
- Tài liệu quy tắc chiến lược và dữ liệu tại `docs/`.

## Product Principles
1. **Operate over decorate**: Tốc độ quét thông tin và độ chính xác của thao tác quan trọng hơn trang trí.
2. **Status clarity**: Trạng thái bot, kết nối feed và nhãn Paper/Live phải đập vào mắt ngay lập tức.
3. **High density with strict rhythm**: Mật độ thông tin cao nhưng bố cục phải có nhịp điệu phân cấp rõ ràng.
4. **Decisive accents**: Màu sắc và độ tương phản dồn vào các sự kiện then chốt (vào lệnh, cắt lỗ, chốt lời, mất kết nối).
