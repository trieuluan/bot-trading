export type Rule =
  | { feature: string; frame: string }
  | { op: 'all' | 'any'; children: Rule[] }
  | { op: 'then'; within: number; children: Rule[] };

export type Genome = {
  family: string;
  lookback: number;
  target_r: number;
  stop_buffer: number;
  exit_params?: Record<string, number>;
  exit_policy?:
    | 'fixed'
    | 'breakeven'
    | 'trail'
    | 'structure'
    | 'adaptive'
    | 'atr_chandelier'
    | 'time_stop'
    | 'scale_out_ladder'
    | 'signal_invalidation'
    | 'runner'
    | 'volatility_stop'
    | 'funding_exit';
  max_er?: number;
  funding_threshold?: number;
  rule?: Rule;
  exit_profile?: string;
  entry_policy?: string;
};

export const HTF_LABELS: Record<string, string> = {
  htf_retest: 'Retest HTF',
  htf_squeeze: 'Squeeze HTF',
  htf_breakout: 'Breakout HTF',
  htf_sweep: 'Quét thanh khoản HTF',
  htf_pullback: 'Pullback EMA HTF',
  htf_ema_cross: 'EMA cross HTF',
  htf_wedge: 'Nêm HTF',
  htf_swing: 'Vùng swing HTF',
  htf_trend_align: 'Đồng thuận Trend HTF',
  htf_bollinger: 'Bollinger HTF',
  htf_dryup: 'Hồi cạn volume HTF',
  htf_fib: 'Fibonacci thoái lui HTF',
  htf_divergence: 'Phân kỳ RSI HTF',
  htf_fvg: 'Fair Value Gap HTF',
  htf_bos: 'Phá vỡ cấu trúc BOS HTF',
  htf_vwap: 'VWAP phiên HTF',
  htf_pdh_pdl: 'PDH/PDL HTF',
  htf_other: 'Trend + nến',
};

export function features(rule: Rule): string[] {
  return 'feature' in rule ? [rule.feature] : rule.children.flatMap(features);
}

// Seed trees: H4 trend filter, H1 setup (child 1), M15 trigger (child 2).
function htfProfile(rule?: Rule): string | null {
  if (!rule || !('op' in rule) || rule.op !== 'all' || rule.children.length !== 3) return null;
  const used = features(rule.children[1]);
  const has = (...names: string[]) => names.some((name) => used.includes(name));
  if (has('retest', 'retest_seq')) return 'htf_retest';
  if (has('compression', 'compression_current')) return 'htf_squeeze';
  if (has('bb_pierce')) return 'htf_bollinger';
  if (has('volume_dryup')) return 'htf_dryup';
  if (has('breakout')) return 'htf_breakout';
  if (has('trend')) return 'htf_trend_align';
  if (has('wick_sweep', 'sweep', 'pdh_pdl_sweep')) return 'htf_sweep';
  if (has('ema_touch', 'ema_bounce')) return 'htf_pullback';
  if (has('ema_cross_n', 'ema_cross')) return 'htf_ema_cross';
  if (has('wedge')) return 'htf_wedge';
  if (has('swing_zone')) return 'htf_swing';
  if (has('fib_retrace')) return 'htf_fib';
  if (has('rsi_divergence')) return 'htf_divergence';
  if (has('fvg')) return 'htf_fvg';
  if (has('bos')) return 'htf_bos';
  if (has('session_vwap')) return 'htf_vwap';
  if (has('pdh_pdl')) return 'htf_pdh_pdl';
  return 'htf_other';
}

export function profileOf(g: Genome): string {
  if (g.family !== 'rule_tree') return g.family;
  const profile = g.exit_profile ?? 'default';
  return profile === 'default' ? (htfProfile(g.rule) ?? 'default') : profile;
}

type Info = {
  kind: string;
  basis: string;
  entry: string;
  stop: string;
};

export function ruleName(rule: Rule, withFrame = true): string {
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
      ema_touch: 'Chạm EMA50',
      wick_sweep: 'Quét vùng rồi đóng trở lại',
      session_active: 'Khung giờ cố định 07:00-21:00 UTC, không đổi theo DST',
      basis_aligned:
        'Lọc lệch giá trade/fair cùng nến: long premium tối đa 5 bps, short discount tối đa 5 bps; không phải index basis',
      macro_safe: 'Tránh giờ tin vĩ mô lớn (CPI, FOMC, NFP)',
      relative_strength: 'Sức mạnh tương đối so với BTC',
      funding_aligned: 'Funding rate thuận chiều',
      oi_surge: 'Open interest tăng ≥1% trong 1h',
      fib_retrace: 'Đóng trong vùng Fib 50-61.8%, swing xác nhận sau 2 nến',
      rsi_divergence: 'Phân kỳ RSI14 >2 điểm, pivot xác nhận cách tối đa 4 nến',
      fvg: 'Chạm lần đầu FVG 3 nến, không xuyên biên xa, nến cùng chiều',
      bos: 'Đóng phá swing đã xác nhận, chưa bị phá trước đó',
      session_vwap: 'Đóng cắt lại VWAP ngày UTC, xấp xỉ theo volume nến',
      pdh_pdl: 'Đóng phá đỉnh/đáy ngày UTC trước, nến cùng chiều',
      pdh_pdl_sweep: 'Quét PDL/PDH rồi đóng trở lại vùng ngày UTC trước (H1/H4)',
      adx_regime: 'ADX14 > 25, +DI/-DI cùng chiều (chỉ H1/H4)',
      ema_distance: 'Cách EMA50 cùng chiều tối thiểu 0.5 ATR14',
      ema_slope: 'Độ dốc EMA50 qua 3 nến đã đóng cùng chiều',
      swing_structure: 'Hai đỉnh và hai đáy cùng hướng, pivot xác nhận 2 nến',
      market_alignment: 'BTC cùng chiều và ít nhất 5/10 coin đồng thuận H4',
      support_proximity: 'Cách hỗ trợ/kháng cự đã xác nhận tối đa 1 ATR14',
      bb_pierce: 'Chọc ra ngoài BB20 rồi đóng lại',
      volume_dryup: 'Hồi cạn volume',
      retest_seq: 'Breakout + retest + xác nhận',
      mtf_seq: 'H4 xu hướng + H1 breakout + M15 retest',
      band_reclaim: 'H4 đi ngang + M15 lấy lại dải BB20 H1',
      funding_extreme: 'Funding cực đoan + xu hướng H4 + breakout 10 M15',
      compression_current: 'Nén theo ATR14 hiện tại',
      ema_cross_n: 'EMA nhanh (N/2, tối thiểu 5) cắt EMA chậm (N)',
      trend_optional: 'M15 cùng EMA50 nếu đủ dữ liệu',
      candle_direction: 'Thân nến cùng chiều',
    };
    return `${names[rule.feature] ?? rule.feature}${withFrame ? ` ${rule.frame}` : ''}`;
  }
  const children = rule.children.map((child) =>
    'feature' in child ? ruleName(child, withFrame) : `(${ruleName(child, withFrame)})`,
  );
  return children.join(
    rule.op === 'then' ? ` rồi ≤${rule.within} nến ` : rule.op === 'all' ? ' + ' : ' / ',
  );
}

export function strategyShortTitle(g: Genome, id?: string): string {
  const profile = profileOf(g);
  const label =
    HTF_LABELS[profile] ?? (profile === 'default' ? 'Cây tự sinh' : profile.replace(/_/g, ' '));
  const hex = id ? id.split(':')[1]?.slice(0, 6) : '';
  const entry = g.entry_policy && g.entry_policy !== 'market' ? ` · ${g.entry_policy}` : '';
  return (hex ? `${label} #${hex}` : label) + entry;
}

export function strategyName(g: Genome): string {
  const profile = profileOf(g);
  const rule = g.rule ? ruleName(g.rule) : 'Cây luật tự sinh';
  if (profile === 'default') return rule;
  return `${HTF_LABELS[profile] ?? profile.replace(/_/g, ' ')} · ${rule}`;
}

export function searchText(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/đ/g, 'd')
    .replace(/_/g, ' ');
}

export function chartStrategyText(row: {
  strategy: string;
  genome?: Genome | null;
  note?: string;
  setup?: { reason: string } | null;
}) {
  const title = row.genome
    ? strategyShortTitle(row.genome, row.strategy)
    : row.strategy.replace(/_/g, ' ').replace(':', ' #');
  const description = row.genome ? strategyName(row.genome) : '';
  const featList = row.genome?.rule ? features(row.genome.rule).join(' ') : '';
  const profile = row.genome ? profileOf(row.genome) : '';
  return {
    title,
    description,
    search: searchText(
      [title, description, profile, featList, row.strategy, row.note, row.setup?.reason].join(' '),
    ),
  };
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

  if (profileOf(g) === 'default' || profileOf(g) in HTF_LABELS)
    return {
      kind: 'Cây luật',
      basis: 'H4 lọc xu hướng, H1 xác định cấu trúc, M15 tìm điểm vào',
      entry: 'Chờ nến đóng; H4 và H1 đồng thuận rồi M15 xác nhận điểm vào.',
      stop: 'Swing 5 nến H1 và ATR14 H1',
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

export function riskParts(g: Genome) {
  const sl = `Swing 5 nến H1 đã đóng + 0.25 ATR14 H1, tối thiểu 1 ATR14 H1; đệm thêm ${g.stop_buffer} ATR14 H1. Thiếu H1 thì chờ, không dùng SL M15 thay thế.`;
  const tp = `Trước cản H1/H4 gần nhất (đệm 0.1 ATR14 H1), tối đa ${Math.max(2, g.target_r)}R; bỏ tín hiệu nếu cản gần hơn 1.5R.`;
  return { sl, tp, manage: manageText(g, `${sl} ${tp} `).slice(`${sl} ${tp} `.length) };
}

export function riskText(g: Genome, _stop: string) {
  const { sl, tp } = riskParts(g);
  return manageText(g, `SL: ${sl} TP: ${tp} `);
}

function manageText(g: Genome, initial: string) {
  if (g.exit_policy === 'adaptive') {
    return (
      initial +
      'Khi đạt 1R: chốt 50% chỉ lúc nến yếu; nến khỏe giữ chạy tới cản/TP. Hòa vốn từ 1R, ' +
      'có phí, funding và trượt giá. Từ 1.5R trailing theo 3 nến H1 + 0.1 ATR14 H1. ' +
      'Chỉ kéo TP gần khi nến yếu và cản còn ít nhất 1.5R; SL không nới. Thay đổi áp dụng từ nến sau.'
    );
  }
  const policy = g.exit_policy ?? 'fixed';
  const params = g.exit_params ?? {};
  const extra: Record<string, string> = {
    atr_chandelier: `SL trail theo đỉnh/đáy 20 nến H1 trừ ${params.atr_multiple ?? 3} ATR14 H1.`,
    volatility_stop: `Từ 1R: SL theo giá đóng nến và ${params.atr_multiple ?? 2} ATR14 H1.`,
    time_stop: `Thoát nếu sau ${params.max_bars ?? 32} giờ chưa đạt ${params.min_progress_r ?? 0.5}R.`,
    signal_invalidation: 'Thoát khi bộ lọc xu hướng H1/H4 đảo ngược đủ số lần xác nhận.',
    runner: `TP chỉ tham chiếu, không kích hoạt đóng lệnh. Chốt ${(params.partial_fraction ?? 0.5) * 100}% tại ${params.partial_r ?? 1}R; phần còn lại trail theo H1.`,
    scale_out_ladder: `Không đóng theo TP cố định. Chốt từng phần tại ${params.step_r ?? 1}R và ${(params.step_r ?? 1) * 2}R; trail H1 rồi đóng hết tại ${(params.step_r ?? 1) * 3}R.`,
    funding_exit: `Thoát khi funding đã trả vượt ${params.funding_r ?? 0.1}R.`,
  };
  if (extra[policy]) return initial + extra[policy] + ' SL không nới.';
  const management =
    policy === 'fixed'
      ? 'Giữ nguyên SL/TP đến khi thoát.'
      : 'M15 đóng đạt 1R: dời SL về hòa vốn có đệm phí, funding đã ghi nhận và trượt giá. ' +
        (policy === 'trail'
          ? 'Từ 1.5R: trailing theo đỉnh/đáy 3 nến H1 + đệm 0.1 ATR14 H1.'
          : policy === 'structure'
            ? 'TP giới hạn trước cản H1/H4, không vượt mục tiêu R; bỏ tín hiệu nếu dưới 1.5R.'
            : 'Không trailing.');
  return initial + management;
}

const ENTRY_TEXT: Record<string, string> = {
  market: 'Market tại giá mở nến M15 kế tiếp sau khi tín hiệu đóng nến.',
  body38:
    'Lệnh chờ tại 38% thân nến tín hiệu tính từ giá đóng. Khớp khi giá xuyên mức; chờ tối đa 4 nến M15.',
  body50:
    'Lệnh chờ tại 50% thân nến tín hiệu tính từ giá đóng. Khớp khi giá xuyên mức; chờ tối đa 4 nến M15.',
  ema20_m15: 'Lệnh chờ tại EMA20 M15. Khớp khi giá xuyên mức; chờ tối đa 4 nến M15.',
  breakout_h1:
    'Lệnh chờ tại đỉnh/đáy 20 nến H1 trước đó (vùng breakout). Khớp khi giá quay lại xuyên mức; chờ tối đa 4 nến M15.',
  retest_h1:
    'Chờ giá quay lại chạm vùng breakout H1, rồi nến M15 đóng cùng chiều trên vùng mới vào market; tối đa 4 nến M15.',
};

function frames(rule: Rule): string[] {
  return 'feature' in rule ? [rule.frame] : [...new Set(rule.children.flatMap(frames))];
}

export type PlayStep = { label: string; text?: string; items?: string[] };

// Mô tả từ genome thật; mỗi bước một khối để card dễ quét.
export function howToPlay(g: Genome, _stop: string): PlayStep[] {
  const rule = g.rule;
  const leaves = (node: Rule): string[] =>
    'feature' in node ? [ruleName(node, false)] : node.children.flatMap(leaves);
  const signal: PlayStep[] =
    rule && 'op' in rule && rule.op === 'all'
      ? rule.children.map((child) => {
          const frame = frames(child).join('/');
          const all = 'op' in child && child.op === 'all' ? 'Cần đủ' : '';
          const any = 'op' in child && child.op === 'any' ? 'Một trong' : '';
          const seq =
            'op' in child && child.op === 'then' ? `Theo thứ tự, trong ≤${child.within} nến` : '';
          const mode = all || any || seq;
          return {
            label: `Tín hiệu ${frame}`,
            ...(mode
              ? {
                  text: mode,
                  items: 'op' in child ? child.children.map((c) => ruleName(c, false)) : [],
                }
              : { text: leaves(child)[0] }),
          };
        })
      : [{ label: 'Tín hiệu', text: rule ? ruleName(rule) : strategyInfo(g).entry }];
  const policy = g.entry_policy ?? 'market';
  const entry: PlayStep[] = [
    { label: 'Điểm vào', text: ENTRY_TEXT[policy] ?? policy },
    ...(policy === 'market'
      ? []
      : [
          {
            label: 'Huỷ lệnh chờ',
            text: 'Hết 4 nến chưa khớp, hoặc TP chạm trước khi khớp, hoặc mức vào không nằm giữa SL và giá tín hiệu.',
          },
        ]),
  ];
  const risk = riskParts(g);
  return [
    ...signal,
    ...entry,
    { label: 'Cắt lỗ SL', text: risk.sl },
    { label: 'Chốt lời TP', text: risk.tp },
    { label: 'Quản lý lệnh', text: risk.manage },
  ];
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
        atr_chandelier: 'Chandelier: SL trail k×ATR H1',
        time_stop: 'Time-stop: thoát nếu lệnh chết',
        scale_out_ladder: 'Chốt nhiều bậc 1R/2R + trail',
        signal_invalidation: 'Thoát khi xu hướng HTF đảo',
        runner: 'Runner: chốt một phần, thả trôi, không TP',
        volatility_stop: 'SL theo biến động ATR',
        funding_exit: 'Thoát khi funding ăn quá R',
      } as Record<string, string>
    )[policy ?? 'fixed'] ?? policy
  );
}

export type TradeStrategy = { title: string; detail: string; generation: string; source: string };

export function tradeStrategy(
  audit:
    | {
        strategy: string;
        genome?: Genome | null;
        generation?: number;
        exit_policy?: string;
        strategy_source?: 'entry_snapshot' | 'registry' | 'missing';
      }
    | null
    | undefined,
): TradeStrategy | null {
  if (!audit?.strategy) return null;
  const { genome } = audit;
  const generation = audit.generation === undefined ? '' : `G${audit.generation}`;
  if (!genome) {
    return {
      title: audit.strategy.startsWith('rule_tree:')
        ? `Cây chưa rõ họ #${audit.strategy.split(':')[1].slice(0, 6)}`
        : audit.strategy.replace(/_/g, ' ').replace(':', ' #'),
      detail: 'Lệnh cũ chưa lưu snapshot chiến thuật lúc vào.',
      generation,
      source: 'Thiếu snapshot lúc vào lệnh',
    };
  }
  return {
    title: strategyShortTitle(genome, audit.strategy),
    detail: `${strategyName(genome)} · ${exitLabel(audit.exit_policy ?? genome.exit_policy)}`,
    generation,
    source:
      audit.strategy_source === 'registry'
        ? 'Tra cứu danh mục cây · không phải snapshot lúc vào'
        : 'Bản lưu chiến thuật lúc vào lệnh',
  };
}

export function stepKind(label: string) {
  return label.startsWith('Cắt lỗ')
    ? 'sl'
    : label.startsWith('Chốt lời')
      ? 'tp'
      : label.startsWith('Điểm vào') || label.startsWith('Huỷ')
        ? 'entry'
        : '';
}
