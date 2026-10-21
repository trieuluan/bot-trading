import type { PaperTrade } from '../types/paper';

export function summarizePaperTrades(trades: PaperTrade[]) {
  const closed = trades.filter((trade) => trade.net_pnl !== null);
  const rValues = closed.flatMap((trade) => {
    const risk = Math.abs(trade.entry - trade.stop) * trade.quantity;
    return risk > 0 ? [trade.net_pnl! / risk] : [];
  });
  return {
    count: trades.length,
    closed: closed.length,
    wins: closed.filter((trade) => trade.net_pnl! > 0).length,
    net: closed.reduce((sum, trade) => sum + trade.net_pnl!, 0),
    fees: closed.reduce(
      (sum, trade) => sum + trade.entry_fee + trade.exit_fee + trade.funding_cost,
      0,
    ),
    meanR: rValues.length ? rValues.reduce((sum, value) => sum + value, 0) / rValues.length : null,
  };
}

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

export function tradePartialProgress(trade: PaperTrade) {
  const exits = trade.monitoring?.partial_exits ?? [];
  if (
    !exits.length ||
    !exits.every(
      (leg) =>
        [leg.quantity, leg.gross, leg.entry_fee, leg.exit_fee].every(Number.isFinite) &&
        leg.quantity > 0,
    )
  )
    return null;
  const closedQuantity = exits.reduce((sum, leg) => sum + leg.quantity, 0);
  const originalQuantity =
    trade.monitoring?.original_quantity && trade.monitoring.original_quantity > 0
      ? trade.monitoring.original_quantity
      : trade.quantity + closedQuantity;
  if (!Number.isFinite(originalQuantity) || originalQuantity <= 0) return null;
  const closedPercent = Math.max(
    0,
    Math.min(100, Math.round((closedQuantity / originalQuantity) * 1000) / 10),
  );
  const remainingPercent = Math.round((100 - closedPercent) * 10) / 10;
  const realizedNet = exits.reduce((sum, leg) => sum + leg.gross - leg.entry_fee - leg.exit_fee, 0);
  return {
    closedQuantity,
    originalQuantity,
    closedPercent,
    remainingPercent,
    realizedNet,
    count: exits.length,
  };
}
