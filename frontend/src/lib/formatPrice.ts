export function formatPrice(coins: number | null): string {
  if (coins === null || coins === undefined) return '—';
  if (coins >= 1_000_000) return (coins / 1_000_000).toFixed(1) + 'M';
  if (coins >= 1_000) return (coins / 1_000).toFixed(1) + 'K';
  return String(coins);
}

export function formatChange(change: number, pct: number): string {
  const sign = change >= 0 ? '+' : '';
  return `${sign}${formatPrice(change)} (${sign}${pct.toFixed(1)}%)`;
}
