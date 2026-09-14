import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  CLEAN_ABORTED_COPY,
  deliveryAbortedMessage,
} from '../miniprogram/miniprogram/utils/business-failure-copy.ts';
import {
  cleanAbortDescription,
} from '../web/src/pages/clean-operations/cleanOperationPresentation.ts';

test('delivery terminal copy distinguishes restart, MCU cancel and MCU failure', () => {
  assert.match(deliveryAbortedMessage('EDGE_RESTARTED'), /香橙派重启/);
  assert.match(
    deliveryAbortedMessage('MCU_RESTART_FINAL_RESULT_UNAVAILABLE'),
    /单片机重启，未取得最终结果包/,
  );
  assert.equal(
    deliveryAbortedMessage('MCU_WORK_CANCELLED'),
    '设备已取消本次投递，请按提示处理',
  );
  assert.equal(
    deliveryAbortedMessage('MCU_WORK_FAILED'),
    '设备执行本次投递失败，请按提示处理',
  );
  assert.equal(
    deliveryAbortedMessage('FUTURE_END_REASON'),
    '本次投递已中止，请按提示处理',
  );
  assert.equal(
    deliveryAbortedMessage(null),
    '本次投递已中止，请按提示处理',
  );
});

test('delivery page passes the authoritative end reason into terminal copy', () => {
  const page = readFileSync(new URL(
    '../miniprogram/miniprogram/pages/delivery-entry/delivery-entry.ts',
    import.meta.url,
  ), 'utf8');
  assert.match(page, /deliveryAbortedMessage\(session\.endReason\)/);
  assert.match(page, /message:\s*sessionMessage\(session\)/);
  assert.doesNotMatch(page, /DEVICE_RESTART_ABORTED:\s*'设备重启/);
});

test('clean miniapp uses generic aborted copy when its API has no end reason', () => {
  assert.equal(CLEAN_ABORTED_COPY.title, '本次清运已中止');
  assert.doesNotMatch(CLEAN_ABORTED_COPY.description, /重启/);
});

test('Web clean detail uses its end reason without calling every abort a restart', () => {
  assert.match(cleanAbortDescription('EDGE_RESTARTED'), /香橙派.*重启/);
  assert.match(cleanAbortDescription('MCU_WORK_CANCELLED'), /单片机已取消/);
  assert.match(cleanAbortDescription('MCU_WORK_FAILED'), /单片机报告.*失败/);
  assert.doesNotMatch(cleanAbortDescription('MCU_WORK_CANCELLED'), /重启/);
  assert.doesNotMatch(cleanAbortDescription('FUTURE_END_REASON'), /重启/);
  assert.doesNotMatch(cleanAbortDescription(null), /重启/);
});
