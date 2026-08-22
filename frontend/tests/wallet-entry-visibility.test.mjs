import assert from 'node:assert/strict';
import test from 'node:test';

import {
  loadVisibleWalletEntryPage,
} from '../miniprogram/miniprogram/utils/wallet-entry-visibility.ts';

const AS_OF = '2026-08-22T03:58:00.000Z';

function entry(entryUid, entryType) {
  return {
    entryUid,
    entrySequenceNo: entryUid === 'freeze' ? 12 : 11,
    entryType,
    availableDeltaYuan: entryType === 'WITHDRAWAL_FREEZE'
      ? '-0.54'
      : '0.00',
    processingDeltaYuan: entryType === 'WITHDRAWAL_FREEZE'
      ? '0.54'
      : '-0.54',
    availableBalanceAfterYuan: '1.33',
    withdrawalProcessingAfterYuan: entryType === 'WITHDRAWAL_FREEZE'
      ? '0.54'
      : '0.00',
    sourceType: 'WITHDRAWAL_ORDER',
    sourceNo: 'AW3829ea777c3e4133b73736664e0480',
    occurredAt: '2026-08-18T09:52:00.000Z',
  };
}

test('out-of-contract withdrawal transfers never become visible cards', async () => {
  const result = await loadVisibleWalletEntryPage(async () => ({
    items: [
      entry('freeze', 'WITHDRAWAL_FREEZE'),
      entry('released', 'WITHDRAWAL_RELEASED'),
      entry('succeeded', 'WITHDRAWAL_SUCCEEDED'),
    ],
    asOf: AS_OF,
    nextCursor: null,
  }));

  assert.deepEqual(
    result.items.map(item => item.entryUid),
    ['succeeded'],
  );
});

test('an all-hidden transfer page is skipped so older entries stay reachable', async () => {
  const requestedCursors = [];
  const result = await loadVisibleWalletEntryPage(async (cursor) => {
    requestedCursors.push(cursor);
    if (!cursor) {
      return {
        items: [
          entry('freeze', 'WITHDRAWAL_FREEZE'),
          entry('released', 'WITHDRAWAL_RELEASED'),
        ],
        asOf: AS_OF,
        nextCursor: 'older-page',
      };
    }
    return {
      items: [entry('succeeded', 'WITHDRAWAL_SUCCEEDED')],
      asOf: AS_OF,
      nextCursor: null,
    };
  });

  assert.deepEqual(requestedCursors, [undefined, 'older-page']);
  assert.deepEqual(
    result.items.map(item => item.entryUid),
    ['succeeded'],
  );
  assert.equal(result.nextCursor, null);
});

test('an exhausted release-only snapshot returns a finished empty page', async () => {
  const result = await loadVisibleWalletEntryPage(async () => ({
    items: [entry('released', 'WITHDRAWAL_RELEASED')],
    asOf: AS_OF,
    nextCursor: null,
  }));

  assert.deepEqual(result.items, []);
  assert.equal(result.nextCursor, null);
});

test('a non-advancing hidden-page cursor is rejected instead of looping', async () => {
  await assert.rejects(
    loadVisibleWalletEntryPage(async () => ({
      items: [entry('freeze', 'WITHDRAWAL_FREEZE')],
      asOf: AS_OF,
      nextCursor: 'same-page',
    }), 'same-page'),
    /钱包明细分页游标未向后推进/,
  );
});
