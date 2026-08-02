export type ReviewDecision =
  | 'ORIGINAL_APPROVED'
  | 'MODIFIED_APPROVED';

export interface DeliveryReviewCalculationOrder {
  deliveryOrderNo: string;
  raw: {
    weightKg: string | null;
    amountYuan: string | null;
    unitPriceYuanPerKg: string | null;
  };
  review: {
    status: 'PENDING' | 'APPROVED';
    currentRevisionNo: number;
    maxReviewAbsoluteWeightKg: string;
    finalAmountYuan: string | null;
  };
}

export interface ClientDeliveryReviewPreview {
  deliveryOrderNo: string;
  revisionType: 'INITIAL_REVIEW' | 'CORRECTION';
  expectedRevisionNo: number;
  decision: ReviewDecision;
  finalWeightKg: string;
  finalAmountYuan: string;
  walletDeltaYuan: string;
  walletEffect: 'APPLIED' | 'NO_CHANGE';
}

export interface AmountToWeightConversion {
  targetAmountYuan: string;
  candidateWeightKg: string;
  actualAmountYuan: string;
  exact: boolean;
  realizingWeightMinKg: string | null;
  realizingWeightMaxKg: string | null;
  realizingCandidateCount: string;
}

const MONEY = /^-?(0|[1-9]\d*)\.\d{2}$/;
const UNIT_PRICE = /^(0|[1-9]\d*)\.\d{4}$/;
const WEIGHT = /^-?(0|[1-9]\d*)\.\d{2}$/;

function parseFixed(value: string, pattern: RegExp): bigint {
  if (!pattern.test(value)) {
    throw new TypeError(`invalid fixed decimal: ${value}`);
  }
  const negative = value.startsWith('-');
  const unsigned = negative ? value.slice(1) : value;
  const magnitude = BigInt(unsigned.replace('.', ''));
  return negative ? -magnitude : magnitude;
}

function formatFixed(value: bigint, fractionDigits: number): string {
  const negative = value < 0n;
  const magnitude = (negative ? -value : value)
    .toString()
    .padStart(fractionDigits + 1, '0');
  const integer = magnitude.slice(0, -fractionDigits);
  const fraction = magnitude.slice(-fractionDigits);
  return `${negative ? '-' : ''}${integer}.${fraction}`;
}

/** Java BigDecimal HALF_UP semantics, including negative ties. */
function divideHalfUp(numerator: bigint, denominator: bigint): bigint {
  if (denominator <= 0n) {
    throw new RangeError('denominator must be positive');
  }
  const negative = numerator < 0n;
  const magnitude = negative ? -numerator : numerator;
  const quotient = magnitude / denominator;
  const remainder = magnitude % denominator;
  const rounded = quotient + (remainder * 2n >= denominator ? 1n : 0n);
  return negative ? -rounded : rounded;
}

function unitPriceUnits(value: string): bigint {
  const units = parseFixed(value, UNIT_PRICE);
  if (units <= 0n) {
    throw new RangeError('unit price must be positive');
  }
  return units;
}

function amountCentAtWeight(
  weightHundredths: bigint,
  priceTenThousandths: bigint,
): bigint {
  return divideHalfUp(
    weightHundredths * priceTenThousandths,
    10_000n,
  );
}

export function calculateAmountForWeight(
  weightKg: string,
  unitPriceYuanPerKg: string | null,
): string {
  const weightHundredths = parseFixed(weightKg, WEIGHT);
  if (unitPriceYuanPerKg === null) {
    if (weightHundredths === 0n) return '0.00';
    throw new RangeError('locked unit price is unavailable');
  }
  const priceTenThousandths = unitPriceUnits(unitPriceYuanPerKg);
  return formatFixed(
    amountCentAtWeight(weightHundredths, priceTenThousandths),
    2,
  );
}

function lowerBoundWeight(
  minimum: bigint,
  maximumExclusive: bigint,
  predicate: (weightHundredths: bigint) => boolean,
): bigint {
  let low = minimum;
  let high = maximumExclusive;
  while (low < high) {
    const middle = low + (high - low) / 2n;
    if (predicate(middle)) {
      high = middle;
    } else {
      low = middle + 1n;
    }
  }
  return low;
}

export function convertAmountToWeight(
  targetAmountYuan: string,
  unitPriceYuanPerKg: string,
  maxReviewAbsoluteWeightKg: string,
): AmountToWeightConversion {
  const targetCent = parseFixed(targetAmountYuan, MONEY);
  const priceTenThousandths = unitPriceUnits(unitPriceYuanPerKg);
  const maximumWeight = parseFixed(maxReviewAbsoluteWeightKg, WEIGHT);
  if (maximumWeight < 0n) {
    throw new RangeError('maximum review weight cannot be negative');
  }

  const candidateWeight = divideHalfUp(
    targetCent * 10_000n,
    priceTenThousandths,
  );
  const actualCent = amountCentAtWeight(
    candidateWeight,
    priceTenThousandths,
  );
  const exact = actualCent === targetCent;

  let realizingWeightMinKg: string | null = null;
  let realizingWeightMaxKg: string | null = null;
  let realizingCandidateCount = 0n;
  if (exact && candidateWeight >= -maximumWeight
      && candidateWeight <= maximumWeight) {
    const minimum = -maximumWeight;
    const maximumExclusive = maximumWeight + 1n;
    const first = lowerBoundWeight(
      minimum,
      maximumExclusive,
      (weight) => amountCentAtWeight(weight, priceTenThousandths)
        >= targetCent,
    );
    const afterLast = lowerBoundWeight(
      minimum,
      maximumExclusive,
      (weight) => amountCentAtWeight(weight, priceTenThousandths)
        > targetCent,
    );
    if (first < maximumExclusive
        && amountCentAtWeight(first, priceTenThousandths) === targetCent) {
      const last = afterLast - 1n;
      realizingWeightMinKg = formatFixed(first, 2);
      realizingWeightMaxKg = formatFixed(last, 2);
      realizingCandidateCount = last - first + 1n;
    }
  }

  return {
    targetAmountYuan,
    candidateWeightKg: formatFixed(candidateWeight, 2),
    actualAmountYuan: formatFixed(actualCent, 2),
    exact,
    realizingWeightMinKg,
    realizingWeightMaxKg,
    realizingCandidateCount: realizingCandidateCount.toString(),
  };
}

export function isCanonicalReviewWeight(value: string): boolean {
  return WEIGHT.test(value);
}

export function isCanonicalReviewAmount(value: string): boolean {
  return MONEY.test(value);
}

export function isWeightWithinAbsoluteLimit(
  weightKg: string,
  maxReviewAbsoluteWeightKg: string,
): boolean {
  const weight = parseFixed(weightKg, WEIGHT);
  const maximum = parseFixed(maxReviewAbsoluteWeightKg, WEIGHT);
  return weight >= -maximum && weight <= maximum;
}

function subtractMoney(left: string, right: string): string {
  return formatFixed(
    parseFixed(left, MONEY) - parseFixed(right, MONEY),
    2,
  );
}

export function calculateClientReviewPreview(
  order: DeliveryReviewCalculationOrder,
  decision: ReviewDecision,
  requestedFinalWeightKg: string | null,
): ClientDeliveryReviewPreview {
  let finalWeightKg: string;
  let finalAmountYuan: string;
  if (decision === 'ORIGINAL_APPROVED') {
    if (requestedFinalWeightKg !== null
        || order.raw.weightKg === null
        || order.raw.amountYuan === null) {
      throw new TypeError('original delivery facts are unavailable');
    }
    finalWeightKg = order.raw.weightKg;
    finalAmountYuan = order.raw.amountYuan;
  } else {
    if (requestedFinalWeightKg === null
        || !isCanonicalReviewWeight(requestedFinalWeightKg)) {
      throw new TypeError('modified review weight is invalid');
    }
    finalWeightKg = requestedFinalWeightKg;
    finalAmountYuan = calculateAmountForWeight(
      requestedFinalWeightKg,
      order.raw.unitPriceYuanPerKg,
    );
  }
  if (!isWeightWithinAbsoluteLimit(
    finalWeightKg,
    order.review.maxReviewAbsoluteWeightKg,
  )) {
    throw new RangeError('review weight exceeds the frozen limit');
  }
  const beforeAmountYuan = order.review.finalAmountYuan ?? '0.00';
  const walletDeltaYuan = subtractMoney(
    finalAmountYuan,
    beforeAmountYuan,
  );
  return {
    deliveryOrderNo: order.deliveryOrderNo,
    revisionType: order.review.status === 'PENDING'
      ? 'INITIAL_REVIEW'
      : 'CORRECTION',
    expectedRevisionNo: order.review.currentRevisionNo,
    decision,
    finalWeightKg,
    finalAmountYuan,
    walletDeltaYuan,
    walletEffect: walletDeltaYuan === '0.00' ? 'NO_CHANGE' : 'APPLIED',
  };
}

export function reviewPreviewMatches(
  client: ClientDeliveryReviewPreview,
  server: ClientDeliveryReviewPreview,
): boolean {
  return client.deliveryOrderNo === server.deliveryOrderNo
    && client.revisionType === server.revisionType
    && client.expectedRevisionNo === server.expectedRevisionNo
    && client.decision === server.decision
    && client.finalWeightKg === server.finalWeightKg
    && client.finalAmountYuan === server.finalAmountYuan
    && client.walletDeltaYuan === server.walletDeltaYuan
    && client.walletEffect === server.walletEffect;
}
