export type MoneyCny = string
export type UnitPriceCnyPerKg = string
export type BusinessWeightKg = string

const MONEY = /^-?(0|[1-9]\d*)\.\d{2}$/
const POSITIVE_MONEY = /^(0|[1-9]\d*)\.\d{2}$/
const UNIT_PRICE = /^(0|[1-9]\d*)\.\d{4}$/
const WEIGHT = /^-?(0|[1-9]\d*)\.\d{2}$/
const MONEY_INPUT = /^(0|[1-9]\d*)(?:\.(\d{0,2}))?$/

function formatDecimal(value: string, pattern: RegExp, suffix: string): string {
  if (!pattern.test(value)) return '—'
  const negative = value.startsWith('-')
  const unsigned = negative ? value.slice(1) : value
  const [integer, fraction] = unsigned.split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}${grouped}.${fraction}${suffix}`
}

export function normalizeMoneyInput(value: string): MoneyCny | null {
  const normalized = value.trim()
  const match = MONEY_INPUT.exec(normalized)
  if (!match) return null
  return `${match[1]}.${(match[2] ?? '').padEnd(2, '0')}`
}

/** 输入过程允许空值和末尾小数点，但绝不接受第三位小数。 */
export function isMoneyInputDraft(value: string): boolean {
  return value === '' || MONEY_INPUT.test(value)
}

export function isMoneyCny(value: string): value is MoneyCny {
  return MONEY.test(value)
}

export function isPositiveMoneyCny(value: string): value is MoneyCny {
  return POSITIVE_MONEY.test(value)
}

export function formatMoneyCny(value: string): string {
  return formatDecimal(value, MONEY, '')
}

export function formatUnitPrice(value: string): string {
  return formatDecimal(value, UNIT_PRICE, ' 元/千克')
}

export function formatBusinessWeight(value: string): string {
  return formatDecimal(value, WEIGHT, ' 千克')
}

/** 比较规范两位小数字符串，不经过 IEEE-754 浮点运算。 */
export function compareMoneyCny(left: string, right: string): number {
  if (!MONEY.test(left) || !MONEY.test(right)) {
    throw new TypeError('money must use a canonical two-decimal string')
  }
  const normalize = (value: string) => {
    const negative = value.startsWith('-')
    const unsigned = negative ? value.slice(1) : value
    const digits = unsigned.replace('.', '').replace(/^0+(?=\d)/, '')
    return { negative, digits }
  }
  const a = normalize(left)
  const b = normalize(right)
  if (a.negative !== b.negative) return a.negative ? -1 : 1
  const magnitude = a.digits.length === b.digits.length
    ? a.digits.localeCompare(b.digits)
    : a.digits.length - b.digits.length
  return a.negative ? -Math.sign(magnitude) : Math.sign(magnitude)
}
