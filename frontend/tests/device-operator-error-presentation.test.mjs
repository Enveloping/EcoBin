import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import test from 'node:test';

const source = readFileSync(new URL(
  '../web/src/pages/device-management/operatorErrorPresentation.ts',
  import.meta.url,
), 'utf8').replace(
  "import { ApiProblem } from '@/api/request';",
  `class ApiProblem extends Error {
    constructor(status, code, message, retryable = false) {
      super(message);
      this.status = status;
      this.code = code;
      this.retryable = retryable;
    }
  }`,
);
test('device busy conflicts show the actionable server message', async () => {
  const { ApiProblem, operatorErrorMessage: present } = await import(
    `data:text/javascript;base64,${Buffer.from(
      `${stripTypeScriptTypes(source)}\nexport { ApiProblem };`,
    ).toString('base64')}`
  );
  const message = '设备仍有投递或待处理的投递结果，结束后才能禁用或报废';
  assert.equal(
    present(new ApiProblem(409, 'DEVICE.CONTROL_BUSY_DELIVERY', message), 'fallback'),
    message,
  );
  assert.equal(
    present(new ApiProblem(
      409,
      'DEVICE.ABNORMAL_DELIVERY_TERMINATION_NOT_ALLOWED',
      '设备尚未连续离线 10 分钟',
    ), 'fallback'),
    '设备尚未连续离线 10 分钟',
  );
});

test('unrecognized conflicts still hide diagnostics and ask for refresh', async () => {
  const { ApiProblem, operatorErrorMessage: present } = await import(
    `data:text/javascript;base64,${Buffer.from(
      `${stripTypeScriptTypes(source)}\nexport { ApiProblem };`,
    ).toString('base64')}`
  );
  assert.equal(
    present(new ApiProblem(409, 'COMMON.VERSION_CONFLICT', 'internal'), 'fallback'),
    '设备状态已经更新，请刷新页面后按最新状态操作',
  );
  assert.equal(
    present(new ApiProblem(409, 'UNKNOWN.CONFLICT', 'private detail'), 'fallback'),
    '设备状态已经更新，请刷新页面后按最新状态操作',
  );
});
