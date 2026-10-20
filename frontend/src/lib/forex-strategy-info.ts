export const forexSetupGuides: Record<string, string> = {
  channel_breakout: 'Nến H1 đóng vượt đỉnh hoặc đáy kênh của cửa sổ nến trước đó.',
  sweep: 'Nến H1 quét qua cực trị cũ, đóng trở lại trong vùng và có thân thuận hướng setup.',
  fvg: 'Vùng FVG hình thành từ ba nến H1; nến hiện tại kiểm định vùng rồi đóng vượt biên theo hướng setup.',
  squeeze: 'So sánh hộp giá gần nhất với biên độ trung bình trước đó; nến H1 đóng phá hộp nén. Không dùng Bollinger/Keltner.',
  pullback: 'Giá chạm EMA vào lệnh H1 rồi đóng trở lại theo xu hướng EMA nhanh/chậm H1. Chu kỳ EMA theo tham số cây.',
  retest: 'Sau khi H1 phá kênh, giá kiểm định lại mốc breakout trong cửa sổ chờ của cây; đóng giữ mốc và chưa phá mức vô hiệu.',
  mss: 'H1 quét cực trị rồi phá cấu trúc đối diện; giá hồi về tỷ lệ retracement của cây và đóng giữ vùng, chưa phá cực trị quét.',
};

export const forexTriggerGuides: Record<string, string> = {
  channel: 'Theo mode của cây: M15 đóng phá biên kênh hoặc quét biên rồi đóng trở lại trong kênh.',
  sweep: 'M15 quét cực trị rồi đóng trở lại trong vùng, thân nến thuận hướng lệnh và không xuyên biên đối diện.',
  rsi: 'RSI từ nến M15 trước đó ở ngoài ngưỡng quá bán/quá mua của cây; nến xác nhận đổi hướng và đóng vượt close trước. Không yêu cầu RSI cắt ngược ngưỡng.',
  fvg: 'Ba nến M15 trước tạo FVG; nến xác nhận chạm vùng, đóng giữ biên và có thân cùng hướng.',
  squeeze: 'M15 đóng vượt hộp nén sau khi biên độ hộp đạt tỷ lệ so với biên độ trung bình trước đó.',
  pullback: 'Xu hướng EMA H1 đồng thuận; nến M15 chạm EMA vào lệnh rồi đóng giữ EMA với thân thuận hướng.',
  retest: 'M15 lần đầu kiểm định mốc breakout H1 trong cửa sổ chờ rồi đóng giữ mốc. Bỏ nếu đã phá ngược hoặc đã retest trước đó.',
  vsa: 'Tick volume vượt trung bình theo hệ số của cây; râu nến đủ tỷ lệ biên độ và thân thuận hướng. Tick volume là proxy, không phải khối lượng khớp; thiếu volume thì loại.',
  vwap: 'Giá quét ngoài dải độ lệch rolling tick-VWAP rồi đóng trở vào dải, chưa vượt tâm VWAP, kèm cực trị mới và phân kỳ RSI. Không phải VWAP phiên; thiếu tick volume thì loại.',
  mss: 'M15 quét cực trị, phá cấu trúc đối diện rồi hồi về tỷ lệ của cây; nến xác nhận đóng giữ mức retracement.',
  session: 'Trong khung giờ UTC của cây, M15 đóng phá vùng giá trước giờ bắt đầu; không nhận breakout đã xảy ra trước đó. Khác ORB theo giờ địa phương.',
  orb: 'M15 lần đầu đóng phá vùng mở phiên từ 08:00 giờ London hoặc New York, theo số nến và cửa sổ của cây. Có điều chỉnh DST; G0 dùng hai nến mở phiên (30 phút).',
  range: 'Vùng range H1 đạt điều kiện độ rộng và hiệu quả giá; M15 chạm vùng biên rồi đóng ngược vào trong với thân cùng hướng.',
  router: 'Độ tách EMA H1 quyết định nhánh: pullback EMA M15 khi có trend, hoặc từ chối biên range H1 khi đi ngang.',
};

const partialGuide = (frame: string) =>
  `Chia gần một nửa lot cho TP1 +1R theo bước lot sàn; không chia đủ thì chỉ còn runner. BE khi đạt +1R hoặc TP1 đã chốt; từ +1.5R có thể trailing theo ba nến ${frame} và buffer. Thoát sớm nếu EMA20 hoặc cấu trúc trên ${frame} bị phá. Stop mới chỉ có hiệu lực từ nến sau trong mô phỏng.`;

export const forexExitGuides: Record<string, string> = {
  partial_be_h1: partialGuide('H1'),
  partial_be_m15: partialGuide('M15'),
  fixed_structural: 'Không chia lot, không BE, trailing hoặc thoát sớm theo chỉ báo. Giữ SL/TP cấu trúc ban đầu; vẫn chịu quy tắc đóng phiên và giới hạn thời gian theo dõi.',
};
