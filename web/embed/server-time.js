/** Server timestamps without an offset come from UTC database values. */
export function parseServerTime(value) {
  const utc = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(value)
    && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return new Date(utc ? `${value.replace(' ', 'T')}Z` : value);
}
