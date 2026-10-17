import type { PaperTrade } from '../types/paper';

export function estimatedPaperPnl(
  trade: PaperTrade,
  price: number | null,
  feeBps: number,
  net = true,
) {
  if (
    trade.exit_time !== null ||
    price === null ||
    price <= 0 ||
    trade.quantity <= 0 ||
    feeBps < 0 ||
    ![price, feeBps, trade.quantity, trade.entry, trade.entry_fee, trade.funding_cost].every(
      Number.isFinite,
    )
  )
    return null;
  const gross = (trade.side === 'long' ? 1 : -1) * trade.quantity * (price - trade.entry);
  return net
    ? gross - trade.entry_fee - trade.funding_cost - (price * trade.quantity * feeBps) / 10000
    : gross;
}
