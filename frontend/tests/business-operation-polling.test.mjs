import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  businessOperationPollDelay,
} from '../miniprogram/miniprogram/utils/business-operation-polling.ts';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('business operation polling always uses a fixed five-second interval', () => {
  assert.equal(businessOperationPollDelay(), 5_000);
  assert.equal(businessOperationPollDelay(), 5_000);
});

test('delivery and cleaning operation pages enable native pull-down refresh', () => {
  for (const page of ['delivery-entry', 'clean-operation']) {
    const config = JSON.parse(source(
      `../miniprogram/miniprogram/pages/${page}/${page}.json`,
    ));
    assert.equal(config.enablePullDownRefresh, true);
  }
});

test('manual refresh reuses an in-flight status request on both operation pages', () => {
  const delivery = source(
    '../miniprogram/miniprogram/pages/delivery-entry/delivery-entry.ts',
  );
  const cleaning = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.ts',
  );

  assert.match(delivery, /onPullDownRefresh\(\)/);
  assert.match(delivery, /wx\.stopPullDownRefresh\(\)/);
  assert.match(
    delivery,
    /if \(this\.sessionRefreshRequest\) return this\.sessionRefreshRequest/,
  );
  assert.match(delivery, /await this\.refreshSessionOnce\(entry\)/);

  assert.match(cleaning, /onPullDownRefresh\(\)/);
  assert.match(cleaning, /wx\.stopPullDownRefresh\(\)/);
  assert.match(cleaning, /if \(this\.pollRequest\) return this\.pollRequest/);
  assert.match(cleaning, /await this\.pollOnce\(\)/);
});

test('cleaning keeps polling only after an explicit offline occupancy release', () => {
  const cleaning = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.ts',
  );

  assert.match(cleaning, /offlineReleasedRecoveryPending:\s*false/);
  assert.match(
    cleaning,
    /projection\.offlineOccupancyReleasedAt[\s\S]*?projection\.status === 'RECOVERY_REQUIRED'/,
  );
  assert.match(
    cleaning,
    /status === 'RECOVERY_REQUIRED'[\s\S]*?&& this\.offlineReleasedRecoveryPending/,
  );
  assert.match(
    cleaning,
    /status === 'RECOVERY_REQUIRED'[\s\S]*?&& !this\.offlineReleasedRecoveryPending[\s\S]*?clearPollTimer\(\)/,
  );
});
