export type Rule =
  | { feature: string; frame: string }
  | { op: 'all' | 'any'; children: Rule[] }
  | { op: 'then'; within: number; children: Rule[] };

type Genome = {
  family: string;
  lookback: number;
  target_r: number;
  stop_buffer: number;
  exit_policy?: 'fixed' | 'breakeven' | 'trail' | 'structure' | 'adaptive';
  max_er?: number;
  funding_threshold?: number;
  rule?: Rule;
  exit_profile?: string;
};

export function profileOf(g: Genome): string {
  return g.family === 'rule_tree' ? (g.exit_profile ?? 'default') : g.family;
}

type Info = {
  kind: string;
  basis: string;
  entry: string;
  stop: string;
};

export function ruleName(rule: Rule): string {
  if ('feature' in rule) {
    const names: Record<string, string> = {
      trend: 'Xu hướng',
      breakout: 'Breakout',
      compression: 'Nén',
      volume: 'Volume',
      sweep: 'Quét râu',
      rsi: 'RSI',
      retest: 'Retest',
      engulfing: 'Nến nhấn chìm',
      pin_bar: 'Nến râu từ chối',
      star: 'Sao mai / sao hôm',
      soldiers: 'Ba lính trắng / ba quạ đen',
      ema_cross: 'EMA9 cắt EMA21',
      swing_zone: 'Từ chối vùng swing',
      doji_reversal: 'Doji đảo chiều',
      inverted_pin: 'Búa ngược / người treo cổ',
      marubozu: 'Marubozu',
      harami: 'Harami',
      harami_cross: 'Harami Cross',
      piercing: 'Xuyên thấu / mây đen che phủ',
      tweezer: 'Đáy / đỉnh nhíp',
      three_inside: 'Three Inside Up / Down',
      three_outside: 'Three Outside Up / Down',
      three_methods: 'Rising / Falling Three Methods',
      wedge: 'Nêm giảm / nêm tăng',
      ema_bounce: 'Bật khỏi EMA21 / EMA50',
      ema_touch: 'M15 chạm EMA50',
      wick_sweep: 'M15 quét vùng rồi đóng trở lại',
      retest_seq: 'Breakout + retest + xác nhận',
      mtf_seq: 'H4 xu hướng + H1 breakout + M15 retest',
      band_reclaim: 'H4 đi ngang + M15 lấy lại dải BB20 H1',
      funding_extreme: 'Funding cực đoan + xu hướng H4 + breakout 10 M15',
      compression_current: 'Nén theo ATR14 hiện tại',
      ema_cross_n: 'EMA nhanh (N/2, tối thiểu 5) cắt EMA chậm (N)',
      trend_optional: 'M15 cùng EMA50 nếu đủ dữ liệu',
      candle_direction: 'Thân nến cùng chiều',
    };
    return `${names[rule.feature] ?? rule.feature} ${rule.frame}`;
  }
  const children = rule.children.map((child) =>
    'feature' in child ? ruleName(child) : `(${ruleName(child)})`,
  );
  return children.join(
    rule.op === 'then' ? ` rồi ≤${rule.within} nến ` : rule.op === 'all' ? ' + ' : ' / ',
  );
}

export function strategyName(g: Genome): string {
  const profile = profileOf(g);
  const rule = g.rule ? ruleName(g.rule) : 'Cây luật tự sinh';
  return profile === 'default' ? rule : `${profile.replace(/_/g, ' ')} · ${rule}`;
}

export function searchText(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/đ/g, 'd')
    .replace(/_/g, ' ');
}

export function strategyInfo(g: Genome): Info {
  const n = g.lookback;
  const map: Record<string, Info> = {
    breakout: {
      kind: 'Breakout',
      basis: `Đỉnh/đáy ${n} nến M15 gần nhất`,
      entry: `Nến M15 đóng vượt đỉnh ${n} nến trước (Long) hoặc thủng đáy ${n} nến trước (Short).`,
      stop: 'Đáy/đỉnh 5 nến M15 gần nhất',
    },
    retest: {
      kind: 'Breakout + Retest',
      basis: `Vùng ${n} nến M15 vừa bị phá vỡ`,
      entry:
        'Giá phá vùng rồi quay lại retest; vào khi nến M15 xanh/đỏ xác nhận giữ được vùng, không đóng ngược lại qua vùng.',
      stop: 'Đáy/đỉnh của các nến retest chạm vùng',
    },
    mtf_retest: {
      kind: 'Đa khung thời gian',
      basis: 'Breakout H1, xu hướng H4',
      entry:
        'Nến H1 phá vỡ cản, chờ M15 quay về retest thành công cùng chiều với xu hướng đường EMA trên H4.',
      stop: 'Đáy/đỉnh cụm nến retest trên M15',
    },
    mean_reversion: {
      kind: 'Đảo chiều hồi quy',
      basis: `Bollinger Bands (20, 2) trên H1 khi thị trường đi ngang (ER H4 ≤ ${g.max_er ?? 0.3})`,
      entry:
        'Nến H1 đóng ngoài dải BB20, nến M15 đóng quay ngược lại vào trong dải để đánh đảo chiều hồi quy.',
      stop: 'Ngoài đỉnh/đáy nến chạm dải BB20; TP tại đường trung bình SMA20 H1',
    },
    liquidity_sweep: {
      kind: 'Quét thanh khoản',
      basis: `Đỉnh/đáy ${n} nến H1 cùng hướng xu hướng EMA50 H4`,
      entry:
        'Giá thò râu quét qua đỉnh/đáy cũ (kích hoạt stop loss đám đông) rồi rút râu đóng nến ngược lại vào trong vùng.',
      stop: 'Chóp râu nến vừa quét thanh khoản',
    },
    funding_filter: {
      kind: 'Funding + Breakout',
      basis: `Funding Rate MEXC lệch ≥ ±${((g.funding_threshold ?? 0.0003) * 100).toFixed(2)}% kết hợp EMA50 H4`,
      entry:
        'Phe đông đang chịu phí funding cao; vào lệnh ép phe đó khi nến M15 phá vỡ vùng 10 nến cùng hướng EMA50 H4.',
      stop: 'Đáy/đỉnh 6 nến M15 gần nhất',
    },
    squeeze: {
      kind: 'Nén biến động',
      basis: `Vùng tích lũy ${n} nến M15 hẹp (biên độ ≤ 3× ATR14)`,
      entry:
        'Sau giai đoạn nén chặt tích lũy, nến M15 đóng cửa bùng nổ phá vỡ hộp nén với động lượng mạnh.',
      stop: 'Đáy/đỉnh 5 nến M15 gần nhất của vùng nén',
    },
    trend_pullback: {
      kind: 'Theo xu hướng',
      basis: 'Đường trung bình EMA50 khung H1',
      entry:
        'Khi H1 có xu hướng rõ ràng, giá M15 hồi quy về chạm EMA50 H1 rồi đóng nến bật ngược lại tiếp diễn xu hướng.',
      stop: 'Đáy/đỉnh 5 nến M15 gần nhất quanh EMA50',
    },
    volume_spike: {
      kind: 'Breakout + Volume',
      basis: `Khối lượng M15 gấp ≥ 2× trung bình ${n} nến trước`,
      entry: `Nến M15 có volume gấp đôi trở lên và đóng cửa vượt đỉnh/đáy ${n} nến.`,
      stop: 'Đáy/đỉnh 5 nến M15 gồm cây nến đột biến',
    },
    donchian_trend: {
      kind: 'Theo xu hướng',
      basis: `Kênh Donchian ${n} nến kết hợp bộ lọc EMA50 H1`,
      entry:
        'Nến M15 đóng cửa phá vỡ biên trên/dưới của kênh Donchian theo đúng hướng xu hướng của EMA50 H1.',
      stop: 'Đường giữa của kênh Donchian',
    },
    ema_cross: {
      kind: 'Giao cắt xu hướng',
      basis: `EMA nhanh ${Math.max(5, Math.floor(n / 2))} và EMA chậm ${n}`,
      entry: 'Đường EMA nhanh cắt lên EMA chậm (Long) hoặc cắt xuống (Short) trên khung M15.',
      stop: 'Đáy/đỉnh 5 nến M15 trước điểm giao cắt',
    },
    rsi_reversal: {
      kind: 'Đảo chiều động lượng',
      basis: 'Chỉ số RSI(14) khung M15',
      entry:
        'RSI rơi vào vùng quá bán (< 30) rồi cắt ngược lên (Long), hoặc vượt quá mua (> 70) rồi cắt ngược xuống (Short).',
      stop: 'Đáy/đỉnh 5 nến M15 vùng đảo chiều RSI',
    },
  };

  if (profileOf(g) === 'default')
    return {
      kind: 'Cây luật',
      basis: 'cấu trúc điều kiện bot tự ghép',
      entry: 'Vào lệnh khi cây điều kiện boolean thoả.',
      stop: 'Đáy/đỉnh 5 nến M15 gần nhất',
    };
  return (
    map[profileOf(g)] ?? {
      kind: 'Quy tắc khác',
      basis: profileOf(g),
      entry: 'Chiến thuật theo bộ quy tắc riêng.',
      stop: 'Theo mức dừng của quy tắc',
    }
  );
}

export function riskText(g: Genome, stop: string) {
  if (g.exit_policy === 'adaptive') {
    return `SL theo swing 5 nến M15 + 0.25 ATR14, tối thiểu 1 ATR14; đệm thêm ${g.stop_buffer} ATR14. ` +
      `TP ban đầu trước cản M15/H1 gần nhất (đệm 0.1 ATR14), không quá ${Math.max(2, g.target_r)}R; bỏ tín hiệu nếu cản gần hơn 1.5R. Khi đạt 1R: chốt 50% chỉ lúc nến yếu (thân ngược, râu từ chối, hoặc chững lại sát cản); nến khỏe giữ chạy tới cản/TP. Hòa vốn ngay từ 1R, ` +
      'SL hòa vốn có phí, funding và trượt giá. Từ 1.5R trailing theo 3 nến + 0.1 ATR14. ' +
      'Chỉ kéo TP gần khi nến yếu và cản còn ít nhất 1.5R; SL không nới. Thay đổi áp dụng từ nến sau.';
  }
  const buf = g.stop_buffer ? ` (cộng thêm ${g.stop_buffer}×ATR14 đệm)` : '';
  const tp =
    profileOf(g) === 'mean_reversion'
      ? 'Chốt lời tại đường trung bình SMA20 H1'
      : `Chốt lời ${g.target_r}R (lợi nhuận = ${g.target_r}× khoảng cách cắt lỗ)`;
  const policy = g.exit_policy ?? 'fixed';
  const management =
    policy === 'fixed'
      ? 'Giữ nguyên SL/TP đến khi thoát.'
      : 'M15 đóng đạt 1R: dời SL về hòa vốn có đệm phí, funding đã ghi nhận và trượt giá. ' +
        (policy === 'trail'
          ? 'Từ 1.5R: trailing theo đỉnh/đáy 3 nến M15 + đệm 0.1 ATR14.'
          : policy === 'structure'
            ? 'TP giới hạn trước cản H1/H4, không vượt mục tiêu R; bỏ tín hiệu nếu dưới 1.5R.'
            : 'Không trailing.');
  return `Cắt lỗ ban đầu: ${stop}${buf}. ${tp}. ${management}`;
}

export function exitLabel(policy?: string) {
  return (
    (
      {
        fixed: 'SL/TP cố định',
        breakeven: 'Hòa vốn 1R',
        trail: 'Hòa vốn + trailing',
        adaptive: 'SL/TP tự tính + chốt khi nến yếu + trailing',
        structure: 'TP cấu trúc + hòa vốn',
      } as Record<string, string>
    )[policy ?? 'fixed'] ?? policy
  );
}
