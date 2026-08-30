import {
  expect,
  test,
  type Page,
  type Route,
} from '@playwright/test';

const taskUid = '70000000-0000-4000-8000-000000000001';
const basePath = '/api/v1/web/platform/operations/reliable-tasks';

const platformSession = {
  sessionUid: '10000000-0000-4000-8000-000000000002',
  accountType: 'PLATFORM_ADMIN',
  subjectUid: '20000000-0000-4000-8000-000000000002',
  displayName: '平台管理员',
  contactPhone: null,
  tenantCode: null,
  capabilities: ['platform-admin.manage'],
  organizations: [],
  expiresAt: '2026-08-23T12:00:00Z',
  version: 3,
  authVersion: 5,
};

function envelope(data: unknown) {
  return { code: 'OK', data, requestId: 'req-operations-e2e' };
}

function problem(status: number, code: string, message: string) {
  return {
    status,
    contentType: 'application/problem+json',
    body: JSON.stringify({
      code,
      message,
      requestId: 'req-operations-e2e',
      retryable: status >= 500,
      details: {},
    }),
  };
}

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(envelope(data)),
  });
}

function reliableTask(
  state: 'PENDING' | 'DONE' | 'CANCELLED' | 'BLOCKED' = 'BLOCKED',
  version = 4,
) {
  return {
    taskUid,
    taskType: 'PROCESS_INBOX',
    taskKind: 'BUSINESS_INTENT',
    executionLane: 'DEVICE',
    state,
    version,
    scopeKind: 'PLATFORM',
    targetType: 'INBOX_MESSAGE',
    targetKey: 'message-42',
    nextRunAt: state === 'PENDING' ? '2026-08-23T08:10:02Z' : null,
    leased: false,
    leaseUntil: null,
    maxAutoAttempts: 20,
    attemptCount: state === 'DONE' ? 3 : 2,
    consecutiveFailureCount: state === 'BLOCKED' ? 2 : 0,
    wakeVersion: state === 'BLOCKED' ? 0 : 1,
    handledWakeVersion: state === 'DONE' ? 1 : 0,
    blockedReasonCode: state === 'BLOCKED' ? 'ONENET_10415' : null,
    blockedDiagnostic: state === 'BLOCKED' ? '物模型服务不存在' : null,
    correlationId: null,
    causationId: null,
    createdAt: '2026-08-23T08:00:00Z',
    updatedAt: '2026-08-23T08:10:00Z',
    nextActions: state === 'BLOCKED' ? ['RESUME'] : [],
  };
}

function attempt(
  attemptNo: number,
  actionKind: string,
  externalCallMayHaveStartedAt: string | null = null,
  resultRecordedAt: string | null = null,
) {
  return {
    attemptUid: `71000000-0000-4000-8000-${String(attemptNo).padStart(12, '0')}`,
    attemptNo,
    actionKind,
    technicalResult: resultRecordedAt ? 'SUCCEEDED' : null,
    claimedAt: `2026-08-23T08:0${attemptNo}:00Z`,
    externalCallMayHaveStartedAt,
    resultRecordedAt,
    httpStatus: resultRecordedAt ? 200 : null,
    externalApiErrorCode: null,
    externalRequestId: resultRecordedAt
      ? 'a25087f46df04b69b29e90ef0acfd115'
      : null,
    durationMs: resultRecordedAt ? 120 : null,
    diagnostic: null,
  };
}

type ApiHandler = (route: Route, url: URL) => Promise<boolean>;

async function mockOperations(page: Page, handler: ApiHandler) {
  await page.addInitScript(() => {
    sessionStorage.setItem('ecobin.web.login-domain', 'platform');
  });
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/platform/auth/sessions/current') {
      await json(route, platformSession);
      return;
    }
    if (url.pathname === '/api/v1/web/auth/sessions/current') {
      await route.fulfill(problem(
        401,
        'SECURITY.UNAUTHENTICATED',
        '未登录',
      ));
      return;
    }
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, {
        token: 'operations-csrf-e2e',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (await handler(route, url)) return;
    await route.fulfill(problem(404, 'COMMON.NOT_FOUND', '接口不存在'));
  });
}

async function openResumeDialog(page: Page) {
  await page.getByRole('button', { name: /恢复$/ }).click();
  const dialog = page.getByRole('dialog', { name: '恢复可靠任务' });
  await dialog.getByText('我已核实并排除上述阻断原因').click();
  return dialog;
}

test('从列表恢复后独立跟踪可靠任务直到终态', async ({ page }) => {
  let resumed = false;
  let detailReads = 0;

  await mockOperations(page, async (route, url) => {
    const request = route.request();
    if (request.method() === 'GET' && url.pathname === basePath) {
      await json(route, {
        items: resumed ? [] : [reliableTask()],
        page: 1,
        pageSize: 20,
        total: resumed ? 0 : 1,
      });
      return true;
    }
    if (request.method() === 'POST'
        && url.pathname === `${basePath}/${taskUid}/resumptions`) {
      resumed = true;
      await json(route, {
        operationId: '72000000-0000-4000-8000-000000000001',
        resourceId: taskUid,
        taskUid,
        state: 'PENDING',
        version: 5,
        statusUrl: `${basePath}/${taskUid}`,
        recommendedPollAfterMs: 500,
      }, 202);
      return true;
    }
    if (request.method() === 'GET'
        && url.pathname === `${basePath}/${taskUid}`) {
      detailReads += 1;
      await json(route, detailReads >= 3
        ? reliableTask('DONE', 6)
        : reliableTask('PENDING', 5));
      return true;
    }
    if (request.method() === 'GET'
        && url.pathname === `${basePath}/${taskUid}/attempts`) {
      await json(route, { items: [], nextCursor: null });
      return true;
    }
    return false;
  });

  await page.goto('/operations/reliable-tasks');
  await expect(page.getByText('PROCESS_INBOX', { exact: true })).toBeVisible();
  const dialog = await openResumeDialog(page);
  await dialog.getByLabel('恢复说明').fill('OneNet 物模型已重新导入');
  await dialog.getByRole('button', { name: '确认恢复原任务' }).click();

  await expect.poll(() => detailReads, { timeout: 5_000 }).toBeGreaterThanOrEqual(3);
  const tracker = page.locator('.operations-recovery-tracker').filter({
    hasText: 'PROCESS_INBOX',
  });
  await expect(tracker.getByText('已完成', { exact: true })).toBeVisible();
});

test('旧的尝试分页响应不能污染同一任务的新详情代次', async ({ page }) => {
  let firstPageReads = 0;
  let oldPageRequested = false;
  let releaseOldPage: (() => void) | undefined;
  const oldPageGate = new Promise<void>((resolve) => {
    releaseOldPage = resolve;
  });

  await mockOperations(page, async (route, url) => {
    const request = route.request();
    if (request.method() === 'GET' && url.pathname === basePath) {
      await json(route, {
        items: [reliableTask()],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return true;
    }
    if (request.method() === 'GET'
        && url.pathname === `${basePath}/${taskUid}`) {
      await json(route, reliableTask());
      return true;
    }
    if (request.method() === 'GET'
        && url.pathname === `${basePath}/${taskUid}/attempts`) {
      const cursor = url.searchParams.get('cursor');
      if (cursor === 'cursor-before-refresh') {
        oldPageRequested = true;
        await oldPageGate;
        await json(route, {
          items: [attempt(2, 'STALE_OLD_PAGE')],
          nextCursor: null,
        });
        return true;
      }
      if (cursor === 'cursor-after-refresh') {
        await json(route, {
          items: [attempt(3, 'CURRENT_OLDER_PAGE')],
          nextCursor: null,
        });
        return true;
      }
      firstPageReads += 1;
      await json(route, firstPageReads === 1
        ? {
            items: [attempt(3, 'FIRST_DETAIL_PAGE')],
            nextCursor: 'cursor-before-refresh',
          }
        : {
            items: [attempt(4, 'REFRESHED_DETAIL_PAGE')],
            nextCursor: 'cursor-after-refresh',
          });
      return true;
    }
    return false;
  });

  await page.goto('/operations/reliable-tasks');
  await page.getByRole('button', { name: /详情$/ }).click();
  let drawer = page.getByRole('dialog', { name: /PROCESS_INBOX/ });
  await expect(drawer.getByText('FIRST_DETAIL_PAGE', { exact: true })).toBeVisible();
  await drawer.getByRole('button', { name: '加载更早记录' }).click();
  await expect.poll(() => oldPageRequested).toBe(true);

  await drawer.locator('.ant-drawer-close').click();
  await page.getByRole('button', { name: /详情$/ }).click();
  drawer = page.getByRole('dialog', { name: /PROCESS_INBOX/ });
  await expect(drawer.getByText('REFRESHED_DETAIL_PAGE', { exact: true })).toBeVisible();
  releaseOldPage?.();

  await expect(drawer.getByText('STALE_OLD_PAGE', { exact: true })).toHaveCount(0);
  const loadMore = drawer.getByRole('button', { name: '加载更早记录' });
  await expect(loadMore).toBeVisible();
  await expect(loadMore).toBeEnabled();
  await loadMore.click();
  await expect(drawer.getByText('CURRENT_OLDER_PAGE', { exact: true })).toBeVisible();
});

test('恢复说明按去空格后的内容校验', async ({ page }) => {
  let resumePosts = 0;

  await mockOperations(page, async (route, url) => {
    const request = route.request();
    if (request.method() === 'GET' && url.pathname === basePath) {
      await json(route, {
        items: [reliableTask()],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return true;
    }
    if (request.method() === 'POST'
        && url.pathname === `${basePath}/${taskUid}/resumptions`) {
      resumePosts += 1;
      await route.fulfill(problem(
        422,
        'OPERATIONS.INVALID_REASON',
        '恢复说明无效',
      ));
      return true;
    }
    return false;
  });

  await page.goto('/operations/reliable-tasks');
  const dialog = await openResumeDialog(page);
  await dialog.getByLabel('恢复说明').fill('abcd ');
  await dialog.getByRole('button', { name: '确认恢复原任务' }).click();

  await expect(dialog.getByText('恢复说明至少 5 个字符')).toBeVisible();
  expect(resumePosts).toBe(0);
});

test('尝试时间线分开任务领取、可能外调和结果落库', async ({ page }) => {
  await mockOperations(page, async (route, url) => {
    const request = route.request();
    if (request.method() === 'GET' && url.pathname === basePath) {
      await json(route, {
        items: [reliableTask()],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return true;
    }
    if (request.method() === 'GET'
        && url.pathname === `${basePath}/${taskUid}`) {
      await json(route, reliableTask());
      return true;
    }
    if (request.method() === 'GET'
        && url.pathname === `${basePath}/${taskUid}/attempts`) {
      await json(route, {
        items: [attempt(
          2,
          'CALL_ONENET',
          '2026-08-23T08:02:01Z',
          '2026-08-23T08:02:02Z',
        )],
        nextCursor: null,
      });
      return true;
    }
    return false;
  });

  await page.goto('/operations/reliable-tasks');
  await page.getByRole('button', { name: /详情$/ }).click();
  const drawer = page.getByRole('dialog', { name: /PROCESS_INBOX/ });
  await expect(drawer.getByRole('columnheader', { name: '任务领取' })).toBeVisible();
  await expect(drawer.getByRole('columnheader', { name: '可能开始外调' })).toBeVisible();
  await expect(drawer.getByRole('columnheader', { name: '结果落库' })).toBeVisible();
  await expect(drawer.getByText(
    '请求 a25087f46df04b69b29e90ef0acfd115',
  )).toBeVisible();
  await expect(drawer.getByRole('columnheader', { name: '发起时间' })).toHaveCount(0);
});
