export type MoneyCny = string;
export type UnitPriceCnyPerKg = string;
export type BusinessWeightKg = string;

const MONEY = /^-?(0|[1-9]\d*)\.\d{2}$/;
const POSITIVE_MONEY = /^(0|[1-9]\d*)\.\d{2}$/;
const UNIT_PRICE = /^(0|[1-9]\d*)\.\d{4}$/;
const WEIGHT = /^-?(0|[1-9]\d*)\.\d{2}$/;
const UTC_MILLIS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

function groupInteger(integer: string): string {
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function formatDecimal(value: string, pattern: RegExp, suffix: string): string {
  if (!pattern.test(value)) return '—';
  const negative = value.startsWith('-');
  const unsigned = negative ? value.slice(1) : value;
  const [integer, fraction] = unsigned.split('.');
  return `${negative ? '-' : ''}${groupInteger(integer)}.${fraction}${suffix}`;
}

export function isMoneyCny(value: string): value is MoneyCny {
  return MONEY.test(value);
}

export function isPositiveMoneyCny(value: string): value is MoneyCny {
  return POSITIVE_MONEY.test(value);
}

export function isUnitPriceCnyPerKg(value: string): value is UnitPriceCnyPerKg {
  return UNIT_PRICE.test(value);
}

export function isBusinessWeightKg(value: string): value is BusinessWeightKg {
  return WEIGHT.test(value);
}

export function formatMoneyCny(value: string): string {
  return formatDecimal(value, MONEY, '');
}

export function formatUnitPrice(value: string): string {
  return formatDecimal(value, UNIT_PRICE, ' 元/千克');
}

export function formatBusinessWeight(value: string): string {
  return formatDecimal(value, WEIGHT, ' 千克');
}

export function formatShanghaiTime(value: string): string {
  if (!UTC_MILLIS.test(value)) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

/** Compare canonical two-decimal monetary strings without IEEE-754 arithmetic. */
export function compareMoneyCny(left: string, right: string): number {
  if (!MONEY.test(left) || !MONEY.test(right)) {
    throw new TypeError('money must use a canonical two-decimal string');
  }
  const normalize = (value: string) => {
    const negative = value.startsWith('-');
    const unsigned = negative ? value.slice(1) : value;
    const digits = unsigned.replace('.', '').replace(/^0+(?=\d)/, '');
    return { negative, digits };
  };
  const a = normalize(left);
  const b = normalize(right);
  if (a.negative !== b.negative) return a.negative ? -1 : 1;
  const magnitude = a.digits.length === b.digits.length
    ? a.digits.localeCompare(b.digits)
    : a.digits.length - b.digits.length;
  return a.negative ? -Math.sign(magnitude) : Math.sign(magnitude);
}
