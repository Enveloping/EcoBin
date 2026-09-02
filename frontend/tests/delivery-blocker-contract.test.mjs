import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

const blockerCodes = [
  'PORT_FULL',
  'WEIGHT_BASELINE_MISSING',
  'DEVICE_SOFTWARE_NOT_ACCEPTING',
];

test('delivery blocker codes stay aligned across the API and clients', () => {
  const sources = new Map([
    ['OpenAPI', source('../../contracts/http/openapi.yaml')],
    ['generated Web type', source(
      '../web/src/api/generated/openapi.d.ts',
    )],
    ['miniapp type', source(
      '../miniprogram/miniprogram/types/api.d.ts',
    )],
    ['miniapp message mapping', source(
      '../miniprogram/miniprogram/pages/delivery-entry/delivery-entry.ts',
    )],
  ]);

  for (const blockerCode of blockerCodes) {
    for (const [sourceName, contents] of sources) {
      assert.equal(
        contents.includes(blockerCode),
        true,
        `${sourceName} must include ${blockerCode}`,
      );
    }
  }
});

test('new delivery blockers have actionable Chinese messages', () => {
  const deliveryEntry = source(
    '../miniprogram/miniprogram/pages/delivery-entry/delivery-entry.ts',
  );

  assert.match(
    deliveryEntry,
    /PORT_FULL:\s*'投口已满，请选择其他投口'/,
  );
  assert.match(
    deliveryEntry,
    /WEIGHT_BASELINE_MISSING:\s*'投口称重基准未就绪，请联系工作人员'/,
  );
  assert.match(
    deliveryEntry,
    /DEVICE_SOFTWARE_NOT_ACCEPTING:\s*'设备正在维护或业务程序尚未准备好，请稍后再试'/,
  );
});
