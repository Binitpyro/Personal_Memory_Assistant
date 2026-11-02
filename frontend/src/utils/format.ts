/**
 * Locale-aware formatting for values the UI prints verbatim.
 *
 * These existed inline as `$${n.toFixed(2)}` and raw ISO strings, which hardcode
 * a currency symbol, a decimal separator and a date order that are only correct
 * in one locale. `Intl` resolves all three from the browser.
 *
 * Both formatters are module-level singletons: constructing an `Intl.*Format` is
 * the expensive part, and these are called inside render for every row of a
 * model list.
 */

const CURRENCY = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  // Provider pricing runs to fractions of a cent, so the 2-digit default would
  // round a real $0.00012 hint to $0.00 and show every model as free.
  minimumFractionDigits: 2,
  maximumFractionDigits: 5,
});

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

/**
 * A retrieval score to two places, truncated toward zero and never rounded, so
 * 0.449 reads 0.44 and cannot look like it cleared a line it did not.
 * The 1e-9 absorbs binary float error: 0.29 * 100 is 28.999999999999996.
 */
export function formatScore(n: number): string {
  return ((Math.sign(n) * Math.floor(Math.abs(n) * 100 + 1e-9)) / 100).toFixed(2);
}

/** Prices and costs. USD because that is the unit every provider quotes in. */
export function formatCurrency(value: number): string {
  return CURRENCY.format(value);
}

/**
 * A timestamp from the API, which may be an ISO string, epoch millis, or a
 * value the server could not produce. Anything unparseable is returned as it
 * arrived rather than rendered as "Invalid Date" — these are diagnostic fields,
 * and the raw text is more useful than a lie.
 */
export function formatDateTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : DATE_TIME.format(d);
}
