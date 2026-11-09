---
name: Signal Desk
description: "Bàn điều hành chính xác: đẹp, gọn, dễ quét và đủ thông tin."
colors:
  canvas: "#0b0e14"
  surface-1: "#11161f"
  surface-2: "#181f2a"
  surface-3: "#232d3b"
  border-subtle: "#1d2633"
  border-base: "#2a3545"
  border-active: "#45566d"
  text-primary: "#f1f5f9"
  text-secondary: "#bec9d7"
  text-muted: "#8d9db2"
  bull: "#10b981"
  bull-bg: "#0d281e"
  bull-border: "#174734"
  bear: "#f43f5e"
  bear-bg: "#2d1017"
  bear-border: "#4d1a27"
  tone-waiting: "#fbbf24"
  tone-waiting-bg: "#281d09"
  tone-waiting-border: "#473210"
  tone-triggered: "#fbbf24"
  tone-triggered-bg: "#281d09"
  tone-triggered-border: "#473210"
  tone-filled: "#10b981"
  tone-filled-bg: "#0c2b20"
  tone-filled-border: "#18523c"
  tone-uncertain: "#f87171"
  tone-uncertain-bg: "#2b1111"
  tone-uncertain-border: "#4a1c1c"
  tone-cancelled: "#ef4444"
  tone-cancelled-bg: "#260e0e"
  tone-cancelled-border: "#3d1515"
  tone-ended: "#94a3b8"
  tone-ended-bg: "#141c26"
  tone-ended-border: "#233142"
typography:
  display:
    fontFamily: "Manrope, 'DM Sans', sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.4
  title:
    fontFamily: "Manrope, 'DM Sans', sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    lineHeight: 1.5
  body:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.3333333333
  metric:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.4
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "12px"
  full: "9999px"
spacing:
  unit: "4px"
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
components:
  button-secondary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    height: "40px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.text-secondary}"
  input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.md}"
    padding: "8px 10px"
  navigation-item:
    textColor: "{colors.text-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "10px 8px"
  chip-neutral:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
  panel:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.xl}"
    padding: "16px"
---

# Design System: Signal Desk

## Overview

**Creative North Star: "Bàn điều hành chính xác"**

Đẹp nhờ phân cấp rõ, nhịp khoảng cách chặt chẽ và điểm nhấn có chủ đích; không nhờ thêm đồ trang trí. Giao diện cần bắt mắt khi nhìn tổng thể, dễ quét khi theo dõi liên tục và đủ thông tin khi cần kiểm tra sâu. Đây là ưu tiên người dùng xác nhận cho những lần thiết kế tiếp theo, không phải tuyên bố mọi màn hình hiện tại đã đạt chuẩn.

Giữ thế giới thị giác hiện có: nền xanh đen, các lớp bề mặt trung tính, chữ sáng và màu trạng thái dứt khoát. Dữ liệu và trạng thái dẫn mắt; công cụ hỗ trợ đứng sau. Gọn nghĩa là giảm lặp và giảm thao tác ghi nhớ, không phải thu nhỏ mọi chữ hay giấu thông tin cần để phân biệt đối tượng.

**Key Characteristics:**
- Đẹp, bắt mắt và tiết chế; điểm nhấn nằm ở thông tin quan trọng.
- Gọn nhưng đủ thông tin; chi tiết kỹ thuật có đường truy cập rõ ràng.
- Phân cấp ổn định, so sánh số liệu nhanh, trạng thái có nhãn chữ.
- Dùng lại hệ màu, typography và thành phần hiện có.

Phạm vi: tài liệu thị giác dùng chung cho frontend. Trích xuất từ `frontend/src/tailwind.css`, theme Tailwind đang cài và các component hiện có; token frontmatter là bản ghi chuẩn để dùng lại. Khi triển khai token mới, cập nhật code và tài liệu cùng nhau. Nội dung sản phẩm và ràng buộc giao dịch nằm trong `PRODUCT.md`; tài liệu này không thay đổi chúng.

## Colors

### Primary

- **Xanh ngọc tín hiệu** (`bull`): giá trị dương, hướng mua và trạng thái hoạt động trong ngữ cảnh có nhãn. Cặp nền/viền tương ứng giữ vùng màu nhỏ và dễ nhận biết.

### Secondary

- **Hồng đỏ rủi ro** (`bear`): giá trị âm, hướng bán và cảnh báo theo ngữ cảnh. Không mặc định mọi lệnh bán là lỗi; nhãn phải giải thích ý nghĩa.
- **Vàng hổ phách chờ xác nhận** (`tone-waiting`, `tone-triggered`): trạng thái cần theo dõi, không phải bằng chứng đã khớp lệnh.
- **Xanh xác nhận khớp** (`tone-filled`), **đỏ nhạt chưa chắc chắn** (`tone-uncertain`), **đỏ hủy** (`tone-cancelled`): giữ vai trò trạng thái riêng dù một số giá trị màu trùng nhau.

### Neutral

- **Xanh đen nền làm việc** (`canvas`): nền ngoài cùng.
- **Các lớp xanh than** (`surface-1` đến `surface-3`): panel, thanh điều khiển, hover và nhóm dữ liệu.
- **Các cấp viền** (`border-subtle`, `border-base`, `border-active`): phân hàng, ranh giới panel và điều khiển cần nhận diện.
- **Các cấp chữ sáng** (`text-primary`, `text-secondary`, `text-muted`): nội dung chính, nhãn hỗ trợ và metadata. Chữ phụ vẫn phải đọc được, không dùng độ mờ để giấu vấn đề bố cục.
- **Xám trạng thái kết thúc** (`tone-ended`): kết thúc hoặc không còn cần chú ý.

**The Meaning Before Color Rule.** Màu bổ sung ý nghĩa cho nhãn, không thay thế nhãn. Không suy ra trạng thái broker, quyền giao dịch hoặc chất lượng chiến lược chỉ từ màu xanh.

Các mã màu nằm trong frontmatter; tên mô tả không tạo bảng màu thứ hai. Những màu utility cục bộ như xanh da trời ở focus hoặc emerald sáng ở nhãn đủ điều kiện đang tồn tại nhưng chưa phải token dùng chung mới.

## Typography

- **Display / title:** Manrope, với DM Sans dự phòng; dùng cho `h1`, `h2`, `h3` trong stylesheet chung.
- **Body / label:** DM Sans; `h4` và nhiều tiêu đề component hiện kế thừa nhóm font này.
- **Metric:** nhóm font monospace hệ thống; số liệu so sánh dùng chữ số có độ rộng bằng nhau.

Thang hiện có: tiêu đề trang dùng `display`, tiêu đề mục dùng `title`, nội dung dùng `body`, metadata dùng `label`. Component có biến thể nhỏ hơn hoặc weight riêng; không ép mọi cấp tiêu đề thành một kích thước. Font được tải qua Google Fonts; khi không tải được, stack dự phòng vẫn phải giữ bố cục dùng được.

**The Numbers Align Rule.** Căn phải số liệu trong cột và dùng tabular numerals cho giá, R, số lượng, thời gian. Monospace dành cho đo lường hoặc mã, không dành cho toàn bộ đoạn mô tả.

## Layout

Nhịp cơ bản lấy từ `spacing.unit`. Dùng khoảng cách nhỏ trong một nhóm có nghĩa; khoảng cách lớn hơn giữa các nhóm. Panel thường có padding `spacing.4`, chi tiết trong drawer dùng `spacing.5`. Tránh mỗi dòng lại có một khung riêng.

Sidebar hiện cố định bên trái: desktop có trạng thái mở và thu gọn; màn hình nhỏ dùng menu trượt. Nội dung phải co được trong không gian còn lại. Các breakpoint hiện dùng kế thừa theme Tailwind, được ghi trong sidecar, không có bộ breakpoint tự đặt khác.

Danh sách và bảng ưu tiên cột ổn định trên desktop. Trên mobile, bố cục được đổi thứ tự hoặc gộp dòng theo mức quan trọng; không coi việc hết tràn ngang là hoàn tất thiết kế responsive. Bảng đối chiếu rộng có vùng cuộn riêng, không làm cả trang cuộn ngang.

**The Compact, Not Hidden Rule.** Thu gọn thông tin lặp trước khi giấu thông tin quyết định. Tên, nhận diện đối tượng, chỉ số chính và trạng thái phải giúp người dùng phân biệt ngay; tham số sâu và phả hệ có thể mở theo nhu cầu.

## Elevation & Depth

Phần lớn giao diện phân lớp bằng sắc độ bề mặt và viền mảnh, không dùng bóng đổ cho mọi panel. Hover đổi lớp nền; focus có outline. Drawer là ngoại lệ nổi rõ: dùng native dialog ở top layer, backdrop tối có blur nhẹ và bóng đổ lớn. Thông số bóng, motion và focus nằm trong sidecar vì không thuộc schema token frontmatter.

Giữ hỗ trợ giảm chuyển động hiện có. Hiệu ứng chỉ phục vụ định hướng mở/đóng hoặc thay đổi trạng thái; không thêm chuyển động liên tục cạnh số liệu realtime.

## Shapes

Điều khiển dùng góc `rounded.md`; nhãn nhỏ dùng `rounded.sm`; khung và panel dùng `rounded.lg` hoặc `rounded.xl` theo component. `rounded.full` dành cho chip trạng thái nhỏ, không biến mọi nút hay khung thành hình viên thuốc.

Viền phân chia mảnh là đặc điểm hiện có. Chỉ tăng độ nổi ở đối tượng đang được chọn, điều khiển có focus hoặc trạng thái cần chú ý; không cho mọi hàng cùng mức nhấn.

## Components

### Buttons

Nút phụ có nền trung tính, viền và chữ phụ rõ; hover nâng sắc độ nền. Biến thể phân trang hiện có chiều cao tối thiểu theo `button-secondary`. Điều khiển icon và nút khác có kích thước riêng, chưa phải một component Button thống nhất. Nút thao tác rủi ro phải có nhãn đúng hành động; không đổi thành màu xanh chỉ để nổi bật.

Focus toàn cục dùng outline xanh ngọc; một số component ghi đè bằng xanh da trời. Đây là sai khác đang tồn tại, không phải yêu cầu thêm hai kiểu focus cho màn hình mới. Disabled giảm opacity và đổi con trỏ; không được là tín hiệu duy nhất giải thích vì sao thao tác bị khóa.

### Inputs / Fields

Field mặc định dùng nền `surface-2`, viền `border-base`, góc và padding của `input`. Một số bộ lọc dùng `surface-1` và `border-active`. Giữ label thấy được, placeholder chỉ gợi ý. Nhập liệu dài phải co được; bộ lọc có kết quả rỗng cần đường đặt lại rõ ràng.

### Navigation

Sidebar dùng icon Lucide và nhãn chữ, trạng thái chọn có nền xanh ngọc mờ và chữ xanh ngọc. Trạng thái thu gọn không được làm mất accessible name. Menu mobile có thao tác đóng rõ; không đánh đồng trạng thái chọn trang với cảnh báo giao dịch.

### Chips

Chip trung tính dùng `chip-neutral`; chip nghiệp vụ phối nền, viền và chữ cùng vai trò trạng thái. Hướng mua/bán, môi trường DEMO/PAPER và lifecycle là ba loại thông tin khác nhau, không gộp vào một chấm màu không có ngữ cảnh.

### Cards / Containers

Panel dùng nền và padding của `panel`, một lớp viền để gom nội dung. Bên trong ưu tiên hàng, khoảng cách và divider thay vì lồng thêm nhiều card. Chỉ số lớn phải có tên và ngữ cảnh dữ liệu, không đứng riêng như trang trí.

### Data Lists / Disclosure

Danh sách cây hiện dùng native `details`/`summary`, mở trực tiếp dưới hàng, với metadata dạng description list. Bảng số liệu có header và cột số căn phải. Cần giữ nhãn chỉ số cho trình đọc màn hình ngay cả khi header thị giác bị ẩn.

Giới hạn đã biết của danh sách Quần thể tiến hoá: nhận diện cây trùng tên còn yếu; trạng thái shadow/demo chưa rõ từng hàng; mobile còn cao; bộ lọc cây đủ mẫu chưa đầy đủ; nhãn Mean R chưa đủ cho screen reader desktop. Đây là backlog từ critique, không phải mẫu chuẩn để sao chép. Bản ghi review nằm trong `.impeccable/critique/`; lệnh document không sửa các lỗi đó.

### Drawer

Dùng `Drawer.tsx` và native modal dialog: Escape, focus trap, khôi phục focus về nơi mở, backdrop và vùng cuộn riêng. Chiều rộng tối đa hiện tại (560px) không vượt viewport. Không tự dựng modal bằng các lớp div nếu component sẵn có đáp ứng.

## Do's and Don'ts

### Do:

- **Do** làm giao diện đẹp bằng phân cấp, căn chỉnh và điểm nhấn có nghĩa.
- **Do** ưu tiên gọn, dễ quét và đủ thông tin; cho mở chi tiết mà không bắt nhớ giữa nhiều hàng.
- **Do** giữ nhãn môi trường và nguồn dữ liệu rõ ràng: demo, paper, shadow hoặc broker.
- **Do** kiểm tra chữ dài, dữ liệu rỗng, số âm, chưa có kết quả, focus bàn phím và mobile.
- **Do** dùng token hiện có; cập nhật tài liệu khi chủ động thay đổi hệ thiết kế.

### Don't:

- **Don't** thêm màu, bóng, card lồng hoặc hiệu ứng chỉ để trang trí.
- **Don't** dùng màu xanh thay cho bằng chứng đã khớp, đủ mẫu hoặc được chọn chạy.
- **Don't** giấu mọi nhận diện của đối tượng để đạt cảm giác tối giản.
- **Don't** làm mobile bằng cách chỉ ép bảng desktop thành hai cột chật.
- **Don't** coi lỗi UI đã ghi nhận là quy ước cần duy trì, hoặc đổi cơ chế giao dịch khi chỉ chỉnh giao diện.
