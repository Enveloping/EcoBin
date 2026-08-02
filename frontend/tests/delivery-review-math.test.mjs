import assert from 'node:assert/strict';
import test from 'node:test';
import {
  calculateAmountForWeight,
  calculateClientReviewPreview,
  convertAmountToWeight,
  reviewPreviewMatches,
} from '../web/src/pages/delivery-orders/deliveryReviewMath.ts';

test('weight to amount uses signed HALF_UP cents without floating point', () => {
  assert.equal(calculateAmountForWeight('0.01', '0.5000'), '0.01');
  assert.equal(calculateAmountForWeight('-0.01', '0.5000'), '-0.01');
  assert.equal(calculateAmountForWeight('0.00', '9999.0000'), '0.00');
  assert.equal(calculateAmountForWeight('1.25', '0.4500'), '0.56');
});

test('0.45 yuan per kg reverses 1.00 yuan to a deterministic candidate', () => {
  const converted = convertAmountToWeight('1.00', '0.4500', '100.00');
  assert.equal(converted.candidateWeightKg, '2.22');
  assert.equal(converted.actualAmountYuan, '1.00');
  assert.equal(converted.exact, true);
});

test('reverse conversion reports every two-decimal weight realizing a cent', () => {
  const converted = convertAmountToWeight('1.00', '0.4500', '100.00');
  assert.equal(converted.realizingCandidateCount, '2');
  assert.equal(converted.realizingWeightMinKg, '2.22');
  assert.equal(converted.realizingWeightMaxKg, '2.23');

  const negative = convertAmountToWeight('-1.00', '0.4500', '100.00');
  assert.equal(negative.candidateWeightKg, '-2.22');
  assert.equal(negative.actualAmountYuan, '-1.00');
  assert.equal(negative.realizingCandidateCount, '2');
  assert.equal(negative.realizingWeightMinKg, '-2.23');
  assert.equal(negative.realizingWeightMaxKg, '-2.22');
});

test('high unit price exposes an amount that no two-decimal weight can realize', () => {
  const converted = convertAmountToWeight('1.00', '200.0000', '100.00');
  assert.equal(converted.candidateWeightKg, '0.01');
  assert.equal(converted.actualAmountYuan, '2.00');
  assert.equal(converted.exact, false);
  assert.equal(converted.realizingCandidateCount, '0');
});

test('client preview includes correction delta and rejects a server mismatch', () => {
  const order = {
    deliveryOrderNo: 'DO-MATH-1',
    raw: {
      weightKg: '1.25',
      amountYuan: '0.56',
      unitPriceYuanPerKg: '0.4500',
    },
    review: {
      status: 'APPROVED',
      currentRevisionNo: 2,
      maxReviewAbsoluteWeightKg: '100.00',
      finalAmountYuan: '0.90',
    },
  };
  const client = calculateClientReviewPreview(
    order,
    'MODIFIED_APPROVED',
    '1.00',
  );
  assert.deepEqual(client, {
    deliveryOrderNo: 'DO-MATH-1',
    revisionType: 'CORRECTION',
    expectedRevisionNo: 2,
    decision: 'MODIFIED_APPROVED',
    finalWeightKg: '1.00',
    finalAmountYuan: '0.45',
    walletDeltaYuan: '-0.45',
    walletEffect: 'APPLIED',
  });
  assert.equal(reviewPreviewMatches(client, client), true);
  assert.equal(reviewPreviewMatches(client, {
    ...client,
    finalAmountYuan: '0.46',
  }), false);
});
