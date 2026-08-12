import assert from 'node:assert/strict';
import test from 'node:test';

import {
  businessOperationPollDelay,
} from '../miniprogram/miniprogram/utils/business-operation-polling.ts';

test('business operation polling uses five seconds during the first 120 seconds', () => {
  const acceptedAtMs = 1_000_000;
  assert.equal(businessOperationPollDelay(acceptedAtMs, acceptedAtMs), 5_000);
  assert.equal(
    businessOperationPollDelay(acceptedAtMs, acceptedAtMs + 119_999),
    5_000,
  );
});

test('business operation polling uses three seconds from the 120-second boundary', () => {
  const acceptedAtMs = 1_000_000;
  assert.equal(
    businessOperationPollDelay(acceptedAtMs, acceptedAtMs + 120_000),
    3_000,
  );
  assert.equal(
    businessOperationPollDelay(acceptedAtMs, acceptedAtMs + 10 * 60_000),
    3_000,
  );
});

test('clock rollback and invalid timestamps stay in the initial polling stage', () => {
  assert.equal(businessOperationPollDelay(10_000, 9_000), 5_000);
  assert.equal(businessOperationPollDelay(Number.NaN, 10_000), 5_000);
});
