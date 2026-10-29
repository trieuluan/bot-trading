import type { ForexShadowTrade } from '../types/forex-entry';

export function shadowPhase(trade: ForexShadowTrade) {
  if (trade.status === 'open') return 'open';
  if (['waiting', 'pending'].includes(trade.status)) return 'waiting';
  return 'history';
}

export function shadowLevels(trade: ForexShadowTrade) {
  // ponytail: mức giá chỉ lấy từ execution hoặc planned_levels trực tiếp; nâng cấp khi có trailing stop chi tiết theo từng nến.
  const levels = trade.entry == null ? (trade.planned_levels ?? []).map((level) => ({
    ...level, label: `${level.label} DỰ KIẾN`,
  })) : [
    { label: 'ENTRY', price: trade.entry },
    ...(trade.legs ?? []).filter((leg) => trade.status !== 'open' || leg.open).flatMap((leg) => [
      { label: `SL ${leg.role}`, price: leg.stop },
      { label: `TP ${leg.role}`, price: leg.target },
    ]),
  ];
  return levels.filter((level): level is { label: string; price: number } =>
    level.price != null && Number.isFinite(level.price) && level.price > 0);
}

export function shadowPnlR(trade: ForexShadowTrade, quote?: { bid: number; ask: number } | null) {
  if (trade.status === 'resolved') return Number.isFinite(trade.net_r) ? trade.net_r! : null;
  const valuation = trade.valuation;
  if (trade.status !== 'open' || !quote || !valuation?.verified || trade.entry == null || !Number.isFinite(trade.entry)) return null;
  const { risk, realized_r, slippage_bps, commission_per_unit, gain_rate, loss_rate } = valuation;
  if ([risk, realized_r, slippage_bps, commission_per_unit, gain_rate, loss_rate]
    .some((value) => value == null || !Number.isFinite(value)) || risk! <= 0 || gain_rate! <= 0 || loss_rate! <= 0
    || slippage_bps! < 0 || commission_per_unit! < 0) return null;
  const direction = trade.side === 'long' ? 1 : -1;
  const mark = trade.side === 'long' ? quote.bid : quote.ask;
  if (!Number.isFinite(mark) || mark <= 0) return null;
  const legs = (trade.legs ?? []).filter((leg) => leg.open);
  if (!legs.length || legs.some((leg) => leg.weight == null || !Number.isFinite(leg.weight) || leg.weight <= 0)) return null;
  const gross = direction * (mark * (1 - direction * slippage_bps! / 10_000) - trade.entry);
  const net = gross * (gross >= 0 ? gain_rate! : loss_rate!) - commission_per_unit! * gain_rate!;
  const total = realized_r! + legs.reduce((sum, leg) => sum + leg.weight! * net / (risk! * loss_rate!), 0);
  return Number.isFinite(total) ? total : null;
}

export function shadowPnlText(trade: ForexShadowTrade, quote?: { bid: number; ask: number } | null) {
  // ponytail: USD PnL chỉ hiện khi có risk_usd từ điều kiện tài khoản USD đã xác minh; thiếu conversion fallback về R thuần.
  const result = shadowPnlR(trade, quote);
  if (result == null) return 'P&L —';
  const risk = trade.valuation?.risk_usd;
  const cash = risk != null && Number.isFinite(risk) && risk > 0 ? result * risk : null;
  return `${cash != null && Number.isFinite(cash) ? `${cash >= 0 ? '+' : ''}${cash.toFixed(2)} USD · ` : ''}${result >= 0 ? '+' : ''}${result.toFixed(2)}R`;
}
