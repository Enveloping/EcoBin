import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  rejectedDeliveryStartDisposition,
} from '../miniprogram/miniprogram/utils/delivery-start-recovery.ts';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('only definite start rejections release the pending request identity', () => {
  for (const status of [400, 401, 403, 404, 422]) {
    assert.equal(rejectedDeliveryStartDisposition(status), 'RELEASE');
  }
  for (const status of [0, 409, 429, 500, 502, 503]) {
    assert.equal(rejectedDeliveryStartDisposition(status), 'RETAIN');
  }
});

test('the page releases any matching rejected attempt and keeps stale-response guards', () => {
  const pageSource = source(
    '../miniprogram/miniprogram/pages/delivery-entry/delivery-entry.ts',
  );
  const intentSource = source(
    '../miniprogram/miniprogram/utils/device-entry-intent.ts',
  );

  assert.match(pageSource, /releaseRejectedPendingDeviceStart\(/);
  assert.doesNotMatch(pageSource, /attemptNumber === 1/);
  assert.match(intentSource, /entry\.startAttemptCount !== expectedAttemptCount/);
});

test('home distinguishes an accepted delivery from an unknown request result', () => {
  const homeSource = source(
    '../miniprogram/miniprogram/pages/home/home.ts',
  );
  const homeMarkup = source(
    '../miniprogram/miniprogram/pages/home/home.wxml',
  );

  assert.match(homeSource, /ongoingDeliveryTitle/);
  assert.match(homeSource, /accepted\s*\?\s*'投递进行中'/);
  assert.match(homeSource, /'投递结果待确认'/);
  assert.match(homeMarkup, /\{\{ongoingDeliveryTitle\}\}/);
  assert.match(homeMarkup, /\{\{ongoingDeliverySubtitle\}\}/);
  assert.doesNotMatch(homeMarkup, />投递请求处理中</);
});
