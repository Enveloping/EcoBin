import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const webRoot = new URL('../web/', import.meta.url);

function source(relativePath) {
  return readFileSync(new URL(relativePath, webRoot), 'utf8');
}

test('operations governance uses generated contracts and caller-owned intents', () => {
  const api = source('src/api/operationsGovernance.ts');

  assert.match(api, /Schemas\['ReliableTask'\]/);
  assert.match(api, /Schemas\['ReliableTaskType'\]/);
  assert.match(api, /operations\['listReliableTasks'\]/);
  assert.match(api, /function listReliableTaskTypes\(\)/);
  assert.match(api, /intent\.executeAccepted/);
  assert.match(
    api,
    /\/api\/v1\/web\/platform\/operations\/reliable-tasks/,
  );
  assert.doesNotMatch(api, /randomUUID|Math\.random/);
});

test('operations governance is platform-only and grouped under one menu', () => {
  const routes = source('src/router/routes.tsx');
  const layout = source('src/layouts/MainLayout.tsx');

  assert.match(
    routes,
    /path: '\/operations\/reliable-tasks'[\s\S]*?<OperationalGovernancePage \/>[\s\S]*?allOf: \['platform-admin\.manage'\][\s\S]*?accountTypes: PLATFORM/,
  );
  assert.match(routes, /name: '运营治理'/);
  assert.match(routes, /path: '\/menu\/operations'/);
  assert.match(
    layout,
    /'\/operations\/reliable-tasks': '\/menu\/operations'/,
  );
});

test('resume action trusts nextActions and requires current facts', () => {
  const page = source('src/pages/operational-governance/index.tsx');

  assert.match(
    page,
    /function canResume\(task: ReliableTask\)[\s\S]*?task\.nextActions\.includes\('RESUME'\)/,
  );
  assert.match(page, /expectedVersion: resumeTarget\.version/);
  assert.match(page, /causeFixedConfirmed: true/);
  assert.match(page, /我已核实并排除上述阻断原因/);
  assert.match(page, /恢复说明至少 5 个字符/);
  assert.match(page, /commandKey\('reliable-task-resume'/);
  assert.match(page, /listReliableTaskAttempts/);
  assert.match(page, /加载更早记录/);
  assert.doesNotMatch(page, /fetch\(|axios\.|randomUUID|Math\.random/);
});

test('recovery tracking and attempt history preserve their async generations', () => {
  const page = source('src/pages/operational-governance/index.tsx');

  assert.match(page, /recoveryPollGenerations/);
  assert.match(page, /task\.state !== 'PENDING'/);
  assert.match(page, /operations-recovery-tracker/);
  assert.match(page, /sequence !== detailSequence\.current/);
  assert.match(page, /setAttemptsLoading\(false\)/);
  assert.doesNotMatch(page, /const pollTimer =/);
});

test('recovery reason and attempt timeline use persisted facts precisely', () => {
  const page = source('src/pages/operational-governance/index.tsx');

  assert.match(page, /value\?\.trim\(\) \?\? ''/);
  assert.match(page, /label="任务领取"[\s\S]*?dataIndex: 'claimedAt'/);
  assert.match(
    page,
    /label="可能开始外调"[\s\S]*?dataIndex: 'externalCallMayHaveStartedAt'/,
  );
  assert.match(
    page,
    /label="结果落库"[\s\S]*?dataIndex: 'resultRecordedAt'/,
  );
  assert.doesNotMatch(page, /title: '发起时间'/);
});

test('task filters and technical terms use the server catalog and explanations', () => {
  const page = source('src/pages/operational-governance/index.tsx');

  assert.match(page, /listReliableTaskTypes\(\)/);
  assert.match(page, /placeholder: '选择或输入完整任务类型代码'/);
  assert.match(page, /options: taskTypeOptions/);
  assert.match(page, /mode: 'tags'/);
  assert.match(page, /option\?\.searchText \?\? option\?\.value/);
  assert.match(page, /taskTypesError/);
  assert.match(page, /重新加载任务类型目录/);
  assert.match(page, /完整英文代码并回车查询/);
  assert.match(page, /taskKind: normalized\(params\.taskKind\)/);
  assert.match(page, /function ExplainedLabel/);
  assert.match(page, /ExclamationCircleOutlined/);
  assert.match(page, /explanation=\{termExplanations\.lease\}/);
  assert.match(page, /explanation=\{termExplanations\.externalCallAt\}/);
  assert.doesNotMatch(page, /nextActions 包含 RESUME/);
});

test('recovery guidance describes original side effects without promising no work', () => {
  const page = source('src/pages/operational-governance/index.tsx');

  assert.match(page, /可能首次完成原任务本来应产生的业务结果/);
  assert.match(page, /不会创建重复或替代的业务结果/);
  assert.doesNotMatch(
    page,
    /不会创建新的订单、提现或设备命令/,
  );
});
