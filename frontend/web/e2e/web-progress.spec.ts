import { expect, test, type Page, type Route } from '@playwright/test';

const tenantSession = {
  sessionUid: '10000000-0000-4000-8000-000000000001',
  accountType: 'STAFF',
  subjectUid: '20000000-0000-4000-8000-000000000001',
  displayName: '林晓',
  contactPhone: null,
  tenantCode: 'tenant-a',
  capabilities: [] as string[],
  organizations: [],
  expiresAt: '2026-07-28T12:00:00Z',
  version: 3,
  authVersion: 5,
};

const platformSession = {
  ...tenantSession,
  sessionUid: '10000000-0000-4000-8000-000000000002',
  accountType: 'PLATFORM_ADMIN',
  subjectUid: '20000000-0000-4000-8000-000000000002',
  displayName: '平台管理员',
  tenantCode: null,
};

function envelope(data: unknown) {
  return { code: 'OK', data, requestId: 'req-e2e' };
}

function problem(
  status: number,
  code = status === 401 ? 'SECURITY.UNAUTHENTICATED' : 'COMMON.ERROR',
  message = '请求失败',
) {
  return {
    status,
    contentType: 'application/problem+json',
    body: JSON.stringify({
      code,
      message,
      requestId: 'req-e2e',
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

const deliveryOrderNo = 'DO-20260730-000001';
const deliveryUserUid = '40000000-0000-4000-8000-000000000041';

function deliveryItem(
  status: 'PENDING' | 'APPROVED',
  revisionNo: number,
) {
  return {
    deliveryOrderNo,
    organizationUserUid: deliveryUserUid,
    deploymentCode: 'dp-hz-01',
    portNo: 2,
    deviceOccurredAt: '2026-07-30T02:10:00.123Z',
    receivedAt: '2026-07-30T02:10:02.123Z',
    rawWeightKg: '1.25',
    rawAmountYuan: '1.00',
    rawWeightReliability: 'RELIABLE',
    rawAmountReliability: 'RELIABLE',
    reviewStatus: status,
    currentRevisionNo: revisionNo,
    finalWeightKg: status === 'APPROVED' ? '1.25' : null,
    finalAmountYuan: status === 'APPROVED' ? '1.00' : null,
    anomalyCodes: [],
    photoCompleteness: 'INCOMPLETE',
  };
}

function deliveryDetail(
  status: 'PENDING' | 'APPROVED',
  revisionNo: number,
) {
  return {
    deliveryOrderNo,
    source: {
      eventUid: '40000000-0000-4000-8000-000000000042',
      sessionUid: '40000000-0000-4000-8000-000000000043',
      deploymentCode: 'dp-hz-01',
      portNo: 2,
      deviceOccurredAt: '2026-07-30T02:10:00.123Z',
      receivedAt: '2026-07-30T02:10:02.123Z',
    },
    ownership: {
      organizationUserUid: deliveryUserUid,
    },
    raw: {
      firstPreOpenWeightGram: 12000,
      finalPostCloseWeightGram: 13250,
      netWeightGram: 1250,
      weightKg: '1.25',
      unitPriceYuanPerKg: '0.8000',
      amountYuan: '1.00',
      weightReliability: 'RELIABLE',
      amountReliability: 'RELIABLE',
      negativeWeightAnomaly: false,
    },
    review: {
      status,
      currentRevisionNo: revisionNo,
      maxReviewAbsoluteWeightKg: '100.00',
      finalWeightKg: status === 'APPROVED' ? '1.25' : null,
      finalAmountYuan: status === 'APPROVED' ? '1.00' : null,
      firstApprovedAt:
        status === 'APPROVED' ? '2026-07-30T03:00:00.123Z' : null,
    },
    anomalies: [],
    photos: [
      'BEFORE_INNER',
      'BEFORE_OUTER',
      'AFTER_INNER',
      'AFTER_OUTER',
    ].map((position) => ({
      position,
      status: 'UPLOAD_PENDING',
      url: null,
      capturedAt: null,
      missingReason: null,
    })),
    revisions: status === 'APPROVED'
      ? [{
          revisionUid: '40000000-0000-4000-8000-000000000044',
          revisionNo,
          revisionType: revisionNo === 1 ? 'INITIAL_REVIEW' : 'CORRECTION',
          decision: 'ORIGINAL_APPROVED',
          beforeFinalWeightKg: revisionNo === 1 ? null : '1.10',
          beforeFinalAmountYuan: revisionNo === 1 ? null : '0.88',
          afterFinalWeightKg: '1.25',
          afterFinalAmountYuan: '1.00',
          amountDeltaYuan: revisionNo === 1 ? '1.00' : '0.12',
          reason: null,
          operator: {
            actorKind: 'STAFF_ACCOUNT',
            actorUid: '40000000-0000-4000-8000-000000000045',
            displayName: '审核员甲',
          },
          reviewedAt: '2026-07-30T03:00:00.123Z',
        }]
      : [],
  };
}

async function mockAnonymous(page: Page) {
  const legacyRequests: string[] = [];
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith('/api/') && !pathname.startsWith('/api/v1/')) {
      legacyRequests.push(pathname);
    }
  });
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, { token: 'csrf-e2e', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (
      route.request().method() === 'GET'
      && url.pathname.endsWith('/auth/sessions/current')
    ) {
      await route.fulfill(problem(401));
      return;
    }
    await route.fulfill(problem(404, 'COMMON.NOT_FOUND', '接口不存在'));
  });
  return legacyRequests;
}

test('login is neutral, uses the native cursor and fits a 375px viewport', async ({
  page,
}) => {
  const legacyRequests = await mockAnonymous(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/login');

  await page.locator('.login-page').waitFor({ state: 'visible', timeout: 15_000 });
  await expect(page.getByRole('heading', { name: '管理后台登录' })).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.locator('[class*="cursor"], .custom-cursor')).toHaveCount(0);
  const visual = await page.locator('.login-page').evaluate((element) => ({
    background: getComputedStyle(element).backgroundColor,
    cursor: getComputedStyle(document.body).cursor,
  }));
  expect(visual.background).toBe('rgb(248, 250, 252)');
  expect(['auto', 'default']).toContain(visual.cursor);
  const card = await page.locator('.login-card').boundingBox();
  expect(card).not.toBeNull();
  expect(card!.width).toBeLessThanOrEqual(343);
  expect(legacyRequests).toEqual([]);
});

for (const domain of ['tenant', 'platform'] as const) {
  test(`${domain} login uses the correct Cookie+CSRF endpoint without Authorization`, async ({
    page,
  }) => {
    let csrfRequestCount = 0;
    let loginRequest:
      | { path: string; headers: Record<string, string>; body: unknown }
      | undefined;
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.endsWith('/auth/csrf-token')) {
        csrfRequestCount += 1;
        await json(route, {
          token: csrfRequestCount === 1
            ? 'csrf-before-login'
            : 'csrf-after-login',
          headerName: 'X-CSRF-TOKEN',
        });
        return;
      }
      if (
        request.method() === 'GET'
        && url.pathname.endsWith('/auth/sessions/current')
      ) {
        await route.fulfill(problem(401));
        return;
      }
      if (request.method() === 'POST' && url.pathname.endsWith('/auth/sessions')) {
        loginRequest = {
          path: url.pathname,
          headers: request.headers(),
          body: request.postDataJSON(),
        };
        await json(
          route,
          domain === 'tenant' ? tenantSession : platformSession,
          201,
        );
        return;
      }
      await route.fulfill(problem(404));
    });

    await page.goto('/login');
    if (domain === 'platform') {
      await page.getByText('平台管理员', { exact: true }).click();
    }
    await page.getByPlaceholder('登录名').fill('operator');
    await page.getByPlaceholder('密码').fill('not-a-real-secret');
    await page.getByRole('button', { name: /登\s*录/ }).click();
    await expect.poll(() => loginRequest).toBeTruthy();

    expect(loginRequest!.path).toBe(
      domain === 'platform'
        ? '/api/v1/web/platform/auth/sessions'
        : '/api/v1/web/auth/sessions',
    );
    expect(loginRequest!.headers.authorization).toBeUndefined();
    expect(loginRequest!.headers['x-csrf-token']).toBe('csrf-before-login');
    expect(loginRequest!.body).toEqual({
      loginName: 'operator',
      password: 'not-a-real-secret',
    });
    await expect.poll(() => csrfRequestCount).toBe(2);
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter((key) =>
          /token|session|auth/i.test(key))),
    ).toEqual([]);
  });
}

test('login page reuses the last non-credential backend entry preference', async ({
  page,
}) => {
  let loginPath = '';
  await page.addInitScript(() => {
    sessionStorage.setItem('ecobin.web.login-domain', 'platform');
  });
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, {
        token: 'csrf-domain-preference',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname.endsWith('/auth/sessions/current')
    ) {
      await route.fulfill(problem(401));
      return;
    }
    if (request.method() === 'POST' && url.pathname.endsWith('/auth/sessions')) {
      loginPath = url.pathname;
      await json(route, platformSession, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/login');
  await expect(
    page.locator('.ant-segmented-item-selected').getByText('平台管理员'),
  ).toBeVisible();
  await page.getByPlaceholder('登录名').fill('admin');
  await page.getByPlaceholder('密码').fill('not-a-real-secret');
  await page.getByRole('button', { name: /登\s*录/ }).click();

  await expect.poll(() => loginPath).toBe(
    '/api/v1/web/platform/auth/sessions',
  );
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem('ecobin.web.login-domain')),
  ).toBe('platform');
});

test('session recovery checks the other audience after a cross-tab Cookie switch', async ({
  page,
}) => {
  const session = {
    ...platformSession,
    capabilities: ['tenant.read'],
  };
  const currentSessionRequests: string[] = [];
  await page.addInitScript(() => {
    sessionStorage.setItem('ecobin.web.login-domain', 'tenant');
  });
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname.endsWith('/auth/sessions/current')
    ) {
      currentSessionRequests.push(url.pathname);
      if (url.pathname === '/api/v1/web/auth/sessions/current') {
        await route.fulfill(problem(401, 'AUTH.TOKEN_AUDIENCE_MISMATCH'));
      } else {
        await json(route, session);
      }
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/tenants'
    ) {
      await json(route, {
        items: [],
        page: 1,
        pageSize: 20,
        total: 0,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/login');
  await expect(page).toHaveURL('/tenant');
  expect(currentSessionRequests).toContain(
    '/api/v1/web/auth/sessions/current',
  );
  expect(currentSessionRequests).toContain(
    '/api/v1/web/platform/auth/sessions/current',
  );
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem('ecobin.web.login-domain')),
  ).toBe('platform');
  await expect(
    page.getByRole('heading', { name: '管理后台登录' }),
  ).toHaveCount(0);
});

test('session bootstrap outages are not misreported as logged-out state', async ({
  page,
}) => {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await route.fulfill(
        problem(503, 'AUTH.SESSION_SERVICE_UNAVAILABLE', '认证服务暂不可用'),
      );
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/tenant');
  await expect(page).toHaveURL('/tenant');
  await expect(page.getByText('暂时无法确认登录状态')).toBeVisible();
  await expect(page.getByText('认证服务暂不可用')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: '管理后台登录' }),
  ).toHaveCount(0);
});

test('a completed login wins over a slower anonymous bootstrap response', async ({
  page,
}) => {
  let releaseBootstrap: (() => void) | undefined;
  const bootstrapGate = new Promise<void>((resolve) => {
    releaseBootstrap = resolve;
  });
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, {
        token: 'csrf-login-race',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname.endsWith('/auth/sessions/current')
    ) {
      await bootstrapGate;
      await route.fulfill(problem(401));
      return;
    }
    if (request.method() === 'POST' && url.pathname.endsWith('/auth/sessions')) {
      await json(route, tenantSession, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/login');
  await page.getByPlaceholder('登录名').fill('operator');
  await page.getByPlaceholder('密码').fill('not-a-real-secret');
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await expect(page).toHaveURL('/account');

  releaseBootstrap?.();
  await page.waitForTimeout(150);
  await expect(page).toHaveURL('/account');
  await expect(
    page.getByRole('heading', { name: '管理后台登录' }),
  ).toHaveCount(0);
});

test('429 login feedback honors Retry-After', async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, {
        token: 'csrf-rate-limit',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname.endsWith('/auth/sessions/current')
    ) {
      await route.fulfill(problem(401));
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname.endsWith('/auth/sessions')
    ) {
      await route.fulfill({
        ...problem(429, 'AUTH.RATE_LIMITED', '登录尝试过于频繁'),
        headers: { 'Retry-After': '120' },
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/login');
  await page.getByPlaceholder('登录名').fill('operator');
  await page.getByPlaceholder('密码').fill('not-a-real-secret');
  await page.getByRole('button', { name: /登\s*录/ }).click();

  await expect(
    page.getByText('登录尝试过于频繁，请在 2 分钟后重试'),
  ).toBeVisible();
});

test('stale login CSRF retries once and refreshes after session creation', async ({
  page,
}) => {
  let csrfRequestCount = 0;
  let loginAttemptCount = 0;
  const csrfHeaders: Array<string | undefined> = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      csrfRequestCount += 1;
      await json(route, {
        token: `csrf-generation-${csrfRequestCount}`,
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname.endsWith('/auth/sessions/current')
    ) {
      await route.fulfill(problem(401));
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname.endsWith('/auth/sessions')
    ) {
      loginAttemptCount += 1;
      csrfHeaders.push(request.headers()['x-csrf-token']);
      if (loginAttemptCount === 1) {
        await route.fulfill(
          problem(403, 'SECURITY.CSRF_INVALID', '请求安全令牌已失效'),
        );
        return;
      }
      await json(route, tenantSession, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/login');
  await page.getByPlaceholder('登录名').fill('operator');
  await page.getByPlaceholder('密码').fill('not-a-real-secret');
  await page.getByRole('button', { name: /登\s*录/ }).click();

  await expect.poll(() => loginAttemptCount).toBe(2);
  await expect.poll(() => csrfRequestCount).toBe(3);
  expect(csrfHeaders).toEqual([
    'csrf-generation-1',
    'csrf-generation-2',
  ]);
});

test('legacy user-binding navigation redirects into organization users', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: ['user.read'],
  };
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/user-bindings');
  await expect(page).toHaveURL(/\/organization-users$/);
  await expect(page.getByText('用户绑定', { exact: true })).toHaveCount(0);
});

test('platform URL tenant context overrides the remembered default', async ({
  page,
}) => {
  const session = {
    ...platformSession,
    capabilities: ['tenant.read', 'user.read'],
  };
  let targetOrganizationsRequested = false;
  await page.addInitScript(() => {
    sessionStorage.setItem('ecobin.web.target-tenant', 'tenant-a');
  });
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await route.fulfill(problem(401));
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/tenants'
    ) {
      await json(route, {
        items: [
          {
            tenantCode: 'tenant-a',
            enterpriseName: '默认租户',
            status: 'ENABLED',
            version: 1,
            createdAt: '2026-07-01T00:00:00Z',
            updatedAt: '2026-07-01T00:00:00Z',
          },
          {
            tenantCode: 'tenant-b',
            enterpriseName: 'URL 目标租户',
            status: 'ENABLED',
            version: 1,
            createdAt: '2026-07-01T00:00:00Z',
            updatedAt: '2026-07-01T00:00:00Z',
          },
        ],
        page: 1,
        pageSize: 200,
        total: 2,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/platform/tenants/tenant-b/organizations'
    ) {
      targetOrganizationsRequested = true;
      await json(route, {
        items: [],
        page: 1,
        pageSize: 200,
        total: 0,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/organization-users?tenant=tenant-b');
  await expect.poll(() => targetOrganizationsRequested).toBe(true);
  await expect(page).toHaveURL(/tenant=tenant-b/);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem('ecobin.web.target-tenant')),
  ).toBe('tenant-b');
});

test('organization-user commands reuse idempotency after a retryable failure', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: ['user.read', 'user.freeze'],
  };
  let currentUser = {
    organizationUserUid: '30000000-0000-4000-8000-000000000001',
    nickname: '周宁',
    avatarUrl: null,
    maskedPhoneNumber: '138****2046',
    phoneBound: true,
    registeredAt: '2026-07-20T03:20:00Z',
    registrationSource: {
      deploymentCode: 'hz-box-07',
      lifecycleStatus: 'ACTIVE',
    },
    status: 'ACTIVE',
    cleanOperationEnabled: false,
    version: 7,
    authVersion: 4,
  };
  const idempotencyKeys: string[] = [];
  let freezeAttempts = 0;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, { token: 'csrf-e2e', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [
          {
            organizationCode: 'org-a',
            organizationName: '湖州运营中心',
            status: 'ENABLED',
            version: 2,
            createdAt: '2026-07-01T00:00:00Z',
            updatedAt: '2026-07-01T00:00:00Z',
          },
        ],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations/org-a/organization-users'
    ) {
      await json(route, {
        items: [currentUser],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    if (url.pathname.endsWith('/freezes') && request.method() === 'POST') {
      freezeAttempts += 1;
      idempotencyKeys.push(request.headers()['idempotency-key']);
      expect(request.postDataJSON()).toMatchObject({
        expectedVersion: 7,
        expectedAuthVersion: 4,
      });
      if (freezeAttempts === 1) {
        await route.abort('failed');
        return;
      }
      currentUser = {
        ...currentUser,
        status: 'FROZEN',
        version: 8,
        authVersion: 5,
      };
      await json(route, currentUser);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname.endsWith(`/${currentUser.organizationUserUid}`)
    ) {
      await json(route, currentUser);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/organization-users');
  await expect(page.getByText('周宁', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '编辑' }).click();
  const disabledChoice = page
    .getByRole('dialog', { name: '编辑机构用户' })
    .locator('label.ant-radio-button-wrapper')
    .filter({ hasText: '禁用' });

  for (let attempt = 0; attempt < 2; attempt += 1) {
    await expect(disabledChoice).not.toHaveClass(/ant-radio-button-wrapper-disabled/);
    await disabledChoice.click();
    await page
      .getByRole('dialog', { name: '确认禁用该用户？' })
      .getByRole('button', { name: '确认禁用' })
      .click();
    await expect.poll(() => freezeAttempts).toBe(attempt + 1);
  }

  expect(idempotencyKeys[0]).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
  await expect(page.getByText('已禁用', { exact: true }).first()).toBeVisible();
});

test('organization recharge creates inline QR codes and lists only posted orders', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: ['organization.read', 'fund.read', 'recharge.create'],
    organizations: [{
      organizationCode: 'org-funds',
      organizationName: '资金运营中心',
    }],
  };
  const createPayloads: Array<{ grossAmountYuan: string }> = [];
  const listStatuses: Array<string | null> = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, { token: 'funds-csrf', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [{
          organizationCode: 'org-funds',
          organizationName: '资金运营中心',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-08-01T00:00:00Z',
          updatedAt: '2026-08-01T00:00:00Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations/org-funds/payout-account'
    ) {
      await json(route, {
        availablePayoutYuan: '980.00',
        frozenWithdrawalYuan: '0.00',
        totalPayoutYuan: '980.00',
        cumulativeRechargeGrossYuan: '1000.00',
        cumulativeRechargeFeeYuan: '6.00',
        cumulativeRechargeNetYuan: '994.00',
        cumulativeSuccessfulWithdrawalYuan: '14.00',
        merchantBindingStatus: 'VERIFIED',
        payoutGateStatus: 'OPEN',
        version: 3,
        asOf: '2026-08-06T00:00:00Z',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-funds/withdrawal-configuration'
    ) {
      await json(route, {
        versionNo: 1,
        hardLimitYuan: '5000.00',
        manualMinimumYuan: '1.00',
        manualMaximumYuan: '5000.00',
        manualReviewFreeThresholdYuan: '0.00',
        publishedAt: '2026-08-01T00:00:00Z',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-funds/recharge-orders'
    ) {
      const status = url.searchParams.get('status');
      listStatuses.push(status);
      await json(route, {
        items: status === 'POSTED' ? [{
          operationId: 'payment-posted',
          resourceId: 'RCPOSTED000000000000000000000001',
          rechargeNo: 'RCPOSTED000000000000000000000001',
          status: 'POSTED',
          version: 2,
          grossAmountYuan: '1000.00',
          feeYuan: '6.00',
          netAmountYuan: '994.00',
          paymentPreparationStatus: 'READY',
          qrCodeUrl: null,
          expiresAt: '2026-08-06T02:00:00Z',
          statusUrl: '/api/v1/web/organizations/org-funds/recharge-orders/RCPOSTED000000000000000000000001',
          recommendedPollAfterMs: 1000,
          createdAt: '2026-08-06T00:00:00Z',
          paidAt: '2026-08-06T00:01:00Z',
          postedAt: '2026-08-06T00:01:01Z',
        }] : [],
        asOf: '2026-08-06T00:02:00Z',
        nextCursor: null,
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname
        === '/api/v1/web/organizations/org-funds/recharge-orders'
    ) {
      const payload = request.postDataJSON() as { grossAmountYuan: string };
      createPayloads.push(payload);
      const index = createPayloads.length;
      if (index === 1) {
        await new Promise((resolve) => setTimeout(resolve, 450));
      }
      const rechargeNo = `RC${String(index).padStart(30, '0')}`;
      const qrReady = index !== 1 && index !== 4;
      await json(route, {
        operationId: `payment-${index}`,
        resourceId: rechargeNo,
        rechargeNo,
        status: 'PENDING_PAYMENT',
        version: qrReady ? 1 : 0,
        grossAmountYuan: payload.grossAmountYuan,
        feeYuan: '0.01',
        netAmountYuan: payload.grossAmountYuan,
        paymentPreparationStatus: qrReady ? 'READY' : 'PENDING',
        qrCodeUrl: qrReady ? `weixin://wxpay/bizpayurl?order=${index}` : null,
        expiresAt: '2026-08-06T02:00:00Z',
        statusUrl: `/api/v1/web/organizations/org-funds/recharge-orders/${rechargeNo}`,
        recommendedPollAfterMs: 1000,
        createdAt: `2026-08-06T00:00:0${index}Z`,
        paidAt: null,
        postedAt: null,
      }, 202);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname.startsWith(
        '/api/v1/web/organizations/org-funds/recharge-orders/RC',
      )
    ) {
      const rechargeNo = url.pathname.split('/').at(-1)!;
      await json(route, {
        operationId: `payment-query-${rechargeNo}`,
        resourceId: rechargeNo,
        rechargeNo,
        status: 'PENDING_PAYMENT',
        version: 1,
        grossAmountYuan: '0.40',
        feeYuan: '0.01',
        netAmountYuan: '0.39',
        paymentPreparationStatus: 'READY',
        qrCodeUrl: `weixin://wxpay/bizpayurl?order=${rechargeNo}`,
        expiresAt: '2026-08-06T02:00:00Z',
        statusUrl: url.pathname,
        recommendedPollAfterMs: 1000,
        createdAt: '2026-08-06T00:00:04Z',
        paidAt: null,
        postedAt: null,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/funds?organization=org-funds');
  await expect(page.getByText('机构充值记录', { exact: true })).toBeVisible();
  await expect.poll(() => listStatuses).toContain('POSTED');
  expect(listStatuses).toContain('PENDING_PAYMENT');
  expect(listStatuses).toContain('PAID_PENDING_POST');
  await expect(
    page.locator('.ant-table').getByText('支付二维码', { exact: true }),
  ).toHaveCount(0);

  const payButton = page.getByRole('button', { name: /支付/ });
  await page
    .locator('label.ant-radio-button-wrapper')
    .filter({ hasText: '¥50.00' })
    .click();
  await expect(payButton).toBeEnabled();
  await payButton.click();
  await expect(payButton).toBeDisabled();

  await page
    .locator('label.ant-radio-button-wrapper')
    .filter({ hasText: '¥200.00' })
    .click();
  await expect(payButton).toBeEnabled();
  await payButton.click();
  await expect(page.getByText('请使用微信扫描二维码完成支付。')).toBeVisible();
  await expect(payButton).toBeEnabled();
  await expect.poll(() => createPayloads).toEqual([
    { grossAmountYuan: '50.00' },
    { grossAmountYuan: '200.00' },
  ]);

  await payButton.click();
  await expect.poll(() => createPayloads).toHaveLength(3);
  expect(createPayloads[2]).toEqual({ grossAmountYuan: '200.00' });
  await page.waitForTimeout(550);
  await expect(page.getByText('请使用微信扫描二维码完成支付。')).toBeVisible();

  await page
    .locator('label.ant-radio-button-wrapper')
    .filter({ hasText: '自定义充值' })
    .click();
  const customAmount = page.getByLabel('自定义充值金额');
  await customAmount.fill('4.4');
  await payButton.click();
  await expect(payButton).toBeDisabled();
  await customAmount.fill('4.5');
  await expect(payButton).toBeEnabled();
});

test('organization-user wallet drawer reads one consistent summary and ledger', async ({
  page,
}) => {
  const organizationUserUid =
    '30000000-0000-4000-8000-000000000011';
  const session = {
    ...tenantSession,
    capabilities: ['user.read', 'wallet.read', 'delivery.read'],
    organizations: [{
      organizationCode: 'org-wallet',
      organizationName: '钱包运营中心',
    }],
  };
  const user = {
    organizationUserUid,
    nickname: '钱敏',
    avatarUrl: null,
    maskedPhoneNumber: '138****3110',
    phoneBound: true,
    registeredAt: '2026-07-30T03:20:00.123Z',
    registrationSource: null,
    status: 'ACTIVE',
    cleanOperationEnabled: false,
    version: 2,
    authVersion: 1,
  };
  const walletRequests: Array<{
    path: string;
    cacheControl?: string;
  }> = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-wallet/organization-users'
    ) {
      await json(route, {
        items: [user],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-wallet/organization-users/${organizationUserUid}/wallet`
    ) {
      walletRequests.push({
        path: url.pathname,
        cacheControl: request.headers()['cache-control'],
      });
      await json(route, {
        walletVersion: 12,
        pendingRewardYuan: '3.20',
        availableBalanceYuan: '-1.50',
        withdrawalProcessingYuan: '2.00',
        asOf: '2026-07-31T01:00:00.123Z',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-wallet/organization-users/${organizationUserUid}/wallet/entries`
    ) {
      walletRequests.push({
        path: url.pathname,
        cacheControl: request.headers()['cache-control'],
      });
      await json(route, {
        items: [{
          entryUid: '30000000-0000-4000-8000-000000000012',
          entrySequenceNo: 12,
          entryType: 'DELIVERY_INITIAL_REVIEW',
          availableDeltaYuan: '1.00',
          processingDeltaYuan: '0.00',
          availableBalanceAfterYuan: '-1.50',
          withdrawalProcessingAfterYuan: '2.00',
          sourceType: 'DELIVERY_ORDER',
          sourceNo: deliveryOrderNo,
          occurredAt: '2026-07-31T00:45:00.123Z',
        }],
        asOf: '2026-07-31T01:00:00.123Z',
        nextCursor: null,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/organization-users?organization=org-wallet');
  await expect(page.getByText('钱敏', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /钱包/ }).click();

  const drawer = page.getByRole('dialog', { name: /用户钱包/ });
  await expect(drawer.getByText('待审核返现', { exact: true })).toBeVisible();
  await expect(drawer.getByText('¥ 3.20', { exact: true })).toBeVisible();
  await expect(drawer.getByText('¥ -1.50', { exact: true })).toBeVisible();
  await expect(
    drawer.getByText('投递首次审核', { exact: true }),
  ).toBeVisible();
  await expect(
    drawer.getByText(deliveryOrderNo, { exact: true }),
  ).toBeVisible();
  await expect.poll(() => walletRequests.length).toBe(2);
  expect(walletRequests.every(
    (request) => request.cacheControl === 'no-store',
  )).toBe(true);
});

test('organization-user list renders both empty and service-error states', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: ['user.read'],
  };
  let failList = false;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [
          {
            organizationCode: 'org-empty',
            organizationName: '空目录机构',
            status: 'ENABLED',
            version: 1,
            createdAt: '2026-07-01T00:00:00Z',
            updatedAt: '2026-07-01T00:00:00Z',
          },
        ],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-empty/organization-users'
    ) {
      if (failList) {
        await route.fulfill(
          problem(503, 'IDENTITY.DIRECTORY_UNAVAILABLE', '机构用户目录暂不可用'),
        );
      } else {
        await json(route, {
          items: [],
          page: 1,
          pageSize: 20,
          total: 0,
        });
      }
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/organization-users');
  await expect(page.locator('.ant-empty-description')).toHaveText('暂无数据');

  failList = true;
  await page.reload();
  await expect(page.getByText('机构用户目录暂不可用')).toBeVisible();
});

test('platform principal reset sends both versions and reports session revocation', async ({
  page,
}) => {
  const session = {
    ...platformSession,
    capabilities: ['tenant.read', 'tenant.manage'],
  };
  let resetPayload: unknown;
  const tenant = {
    tenantCode: 'tenant-a',
    enterpriseName: '清源再生资源',
    status: 'ENABLED',
    contactName: '陈卓',
    contactPhone: '138****1008',
    contactAddress: '湖州市吴兴区',
    version: 6,
    principalAccount: {
      staffAccountUid: '40000000-0000-4000-8000-000000000001',
      status: 'ENABLED',
      version: 11,
      authVersion: 9,
    },
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-20T00:00:00Z',
  };

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, { token: 'csrf-e2e', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await route.fulfill(problem(401));
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/tenants'
    ) {
      await json(route, {
        items: [tenant],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname
        === '/api/v1/web/platform/tenants/tenant-a/principal-account/password-resets'
    ) {
      resetPayload = request.postDataJSON();
      expect(request.headers()['idempotency-key']).toBeTruthy();
      await json(route, {
        staffAccountUid: tenant.principalAccount.staffAccountUid,
        accountKind: 'TENANT_PRINCIPAL',
        loginName: 'tenant-owner',
        displayName: '租户主体',
        status: 'ENABLED',
        version: 12,
        authVersion: 10,
        createdAt: tenant.createdAt,
        updatedAt: tenant.updatedAt,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/tenants/tenant-a'
    ) {
      await json(route, {
        ...tenant,
        principalAccount: {
          ...tenant.principalAccount,
          version: 12,
          authVersion: 10,
        },
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/tenant');
  await page.getByText('编辑', { exact: true }).click();
  await page.getByText('重置主体密码', { exact: true }).click();
  await page.locator('input#newPassword').fill('new-password-2026');
  await page.locator('input#confirmPassword').fill('new-password-2026');
  await page
    .getByLabel('重置主体密码 · 清源再生资源')
    .getByRole('button', { name: '确 定' })
    .click();

  await expect.poll(() => resetPayload).toEqual({
    newPassword: 'new-password-2026',
    expectedVersion: 11,
    expectedAuthVersion: 9,
  });
  await expect(
    page.getByText('主体密码已重置，原有主体会话已撤销'),
  ).toBeVisible();
});

test('organization-user detail binds staff and grants cleaner capability', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: [
      'organization.read',
      'staff.read',
      'staff.bind',
      'user.read',
      'cleaner.manage',
    ],
  };
  let csrfRequestCount = 0;
  let lookupAttemptCount = 0;
  const lookupHeaders: Array<string | undefined> = [];
  let bindingPayload: Record<string, unknown> | undefined;
  let cleanerPayload: Record<string, unknown> | undefined;
  let bound = false;
  let currentUser = {
    organizationUserUid: '40000000-0000-4000-8000-000000000002',
    nickname: '清运测试用户',
    avatarUrl: null,
    phoneNumber: '13700137000',
    maskedPhoneNumber: '13700137000',
    phoneBound: true,
    registeredAt: '2026-07-02T00:00:00Z',
    registrationSource: null,
    status: 'ACTIVE',
    cleanOperationEnabled: false,
    version: 3,
    authVersion: 2,
  };
  const staffUid = '3102b64d-433d-4015-9a29-b21f309ddf8a';
  const disabledStaffUid = '3102b64d-433d-4015-9a29-b21f309ddf8b';
  const bindingUid = '50000000-0000-4000-8000-000000000001';

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      csrfRequestCount += 1;
      await json(route, {
        token: `binding-csrf-${csrfRequestCount}`,
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [{
          organizationCode: 'org-binding',
          organizationName: '清运机构',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-07-01T00:00:00Z',
          updatedAt: '2026-07-01T00:00:00Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations/org-binding/organization-users'
    ) {
      await json(route, {
        items: [currentUser],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-binding/organization-users/${currentUser.organizationUserUid}`
    ) {
      await json(route, currentUser);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/staff-accounts'
    ) {
      await json(route, {
        items: [
          {
            staffAccountUid: staffUid,
            accountKind: 'STAFF',
            loginName: 'operator',
            displayName: '现场工作人员',
            status: 'ENABLED',
            version: 2,
            authVersion: 3,
            createdAt: '2026-07-01T00:00:00Z',
            updatedAt: '2026-07-01T00:00:00Z',
          },
          {
            staffAccountUid: disabledStaffUid,
            accountKind: 'STAFF',
            loginName: 'disabled.account',
            displayName: '停用工作人员',
            status: 'DISABLED',
            version: 4,
            authVersion: 5,
            createdAt: '2026-07-01T00:00:00Z',
            updatedAt: '2026-07-01T00:00:00Z',
          },
        ],
        page: 1,
        pageSize: 200,
        total: 2,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === `/api/v1/web/staff-accounts/${staffUid}`
    ) {
      await json(route, {
        staffAccountUid: staffUid,
        accountKind: 'STAFF',
        loginName: 'operator',
        displayName: '现场工作人员',
        status: 'ENABLED',
        version: 2,
        authVersion: 3,
        createdAt: '2026-07-01T00:00:00Z',
        updatedAt: '2026-07-01T00:00:00Z',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname.endsWith(
        `/staff-accounts/${staffUid}/miniapp-binding`,
      )
    ) {
      await json(route, {
        currentMiniappBinding: bound
          ? {
              bindingUid,
              organizationUserUid: currentUser.organizationUserUid,
              version: 1,
              nickname: currentUser.nickname,
              phoneNumber: currentUser.phoneNumber,
              maskedPhoneNumber: currentUser.phoneNumber,
            }
          : null,
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname.endsWith('/organization-user-lookups')
    ) {
      lookupAttemptCount += 1;
      lookupHeaders.push(request.headers()['x-csrf-token']);
      const { phoneNumber } = request.postDataJSON() as {
        phoneNumber: string;
      };
      if (phoneNumber === currentUser.phoneNumber && lookupAttemptCount === 1) {
        await route.fulfill(
          problem(403, 'SECURITY.CSRF_INVALID', 'CSRF 校验失败'),
        );
      } else if (phoneNumber === currentUser.phoneNumber) {
        await json(route, {
          organizationUserUid: currentUser.organizationUserUid,
          nickname: currentUser.nickname,
          phoneNumber: currentUser.phoneNumber,
          maskedPhoneNumber: currentUser.phoneNumber,
          registeredAt: currentUser.registeredAt,
          status: currentUser.status,
          currentMiniappBinding: bound
            ? { bindingUid, staffAccountUid: staffUid, version: 1 }
            : null,
        });
      } else {
        await route.fulfill(problem(400));
      }
      return;
    }
    if (
      request.method() === 'PUT'
      && url.pathname.endsWith(`/staff-accounts/${staffUid}/miniapp-binding`)
    ) {
      bindingPayload = request.postDataJSON();
      bound = true;
      await json(route, {
        bindingUid,
        organizationUserUid: currentUser.organizationUserUid,
        staffAccountUid: staffUid,
        status: 'ACTIVE',
        version: 1,
        boundAt: '2026-08-06T00:00:00Z',
        revokedAt: null,
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname.endsWith('/capabilities/clean-operation/grants')
    ) {
      cleanerPayload = request.postDataJSON();
      currentUser = {
        ...currentUser,
        cleanOperationEnabled: true,
        version: 4,
        authVersion: 3,
      };
      await json(route, currentUser);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/organization-users?organization=org-binding');
  await expect(page.getByText('清运测试用户', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '编辑' }).click();
  const editDrawer = page.getByRole('dialog', { name: '编辑机构用户' });
  await editDrawer.getByRole('button', { name: '选择工作人员' }).click();
  const bindingDialog = page.getByRole('dialog', { name: '选择工作人员' });
  await expect(bindingDialog).toBeVisible();
  await expect.poll(() => lookupAttemptCount).toBeGreaterThanOrEqual(2);
  await expect.poll(() => lookupHeaders.includes('binding-csrf-2')).toBe(true);
  expect(lookupHeaders).toContain('binding-csrf-1');

  await expect(bindingDialog.getByText('停用工作人员', { exact: true })).toBeVisible();
  await expect(
    bindingDialog.getByRole('radio', { name: '停用工作人员' }),
  ).toBeDisabled();
  await bindingDialog.getByLabel('搜索工作人员').fill('现场');
  await expect(bindingDialog.getByText('停用工作人员', { exact: true })).toHaveCount(0);
  await bindingDialog.getByLabel('搜索工作人员').clear();

  await bindingDialog.getByRole('radio', { name: '现场工作人员' }).click();
  await bindingDialog.getByRole('button', { name: '确认绑定' }).click();
  await expect.poll(() => bindingPayload).toEqual({
    organizationUserUid: currentUser.organizationUserUid,
    expectedStaffBinding: null,
    expectedOrganizationUserBinding: null,
  });
  await expect(editDrawer.getByText('现场工作人员', { exact: true })).toBeVisible();

  await editDrawer
    .locator('label.ant-radio-button-wrapper')
    .filter({ hasText: '清运员' })
    .click();
  await page
    .getByRole('dialog', { name: '确认设为清运员？' })
    .getByRole('button', { name: /确\s*认/ })
    .click();
  await expect.poll(() => cleanerPayload).toEqual({
    expectedVersion: 3,
    expectedAuthVersion: 2,
    reason: 'Web 管理端授予机构用户清运操作资格',
  });
  await expect(page.getByText('清运员', { exact: true }).first()).toBeVisible();
});

test('tenant sidebar preset and name link apply real directory filters', async ({
  page,
}) => {
  const session = {
    ...platformSession,
    capabilities: [
      'tenant.read',
      'tenant.manage',
      'organization.read',
      'delivery.read',
      'clean.read',
      'withdrawal.read',
    ],
  };
  const tenant = {
    tenantCode: 'tenant-disabled',
    enterpriseName: '停用租户',
    status: 'DISABLED',
    contactName: '陈卓',
    contactPhone: '138****1008',
    contactAddress: '湖州市吴兴区',
    version: 6,
    principalAccount: null,
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-20T00:00:00Z',
  };
  const tenantStatuses: Array<string | null> = [];
  let scopedOrganizationsRequested = false;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await route.fulfill(problem(401));
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/platform/tenants'
    ) {
      tenantStatuses.push(url.searchParams.get('status'));
      await json(route, {
        items: [tenant],
        page: Number(url.searchParams.get('page') ?? 1),
        pageSize: Number(url.searchParams.get('pageSize') ?? 20),
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/platform/tenants/tenant-disabled/organizations'
    ) {
      scopedOrganizationsRequested = true;
      await json(route, {
        items: [],
        page: 1,
        pageSize: 20,
        total: 0,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/tenant?view=disabled');
  await expect(
    page.locator('.ant-pro-sider').getByText('已禁用的租户', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('停用租户', { exact: true })).toBeVisible();
  await expect.poll(() => tenantStatuses).toContain('DISABLED');
  await expect(page.getByText('编辑', { exact: true })).toBeVisible();
  await expect(
    page.getByText('重置主体密码', { exact: true }),
  ).toHaveCount(0);
  const sider = page.locator('.ant-pro-sider');
  const selectedMenuItems = sider.locator('.ant-menu-item-selected');
  await expect(selectedMenuItems).toHaveCount(1);
  await expect(selectedMenuItems).toContainText('已禁用的租户');

  await sider.getByText('所有租户', { exact: true }).click();
  await expect(page).toHaveURL(/\/tenant(?:\?tenant=[^&]+)?$/);
  await expect(selectedMenuItems).toHaveCount(1);
  await expect(selectedMenuItems).toContainText('所有租户');

  await sider.getByText('已禁用的租户', { exact: true }).click();
  await expect(page).toHaveURL(/\/tenant\?view=disabled/);
  await expect(selectedMenuItems).toHaveCount(1);
  await expect(selectedMenuItems).toContainText('已禁用的租户');

  await sider
    .locator('.ant-menu-submenu-title')
    .filter({ hasText: '投递管理' })
    .click();
  await expect(
    sider.getByText('已拒绝订单', { exact: true }),
  ).toBeVisible();
  await expect(
    sider.getByText('已纠正订单', { exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/tenant\?view=disabled/);

  await page.getByText('停用租户', { exact: true }).click();
  await expect(page).toHaveURL(/\/organizations\?tenant=tenant-disabled/);
  await expect.poll(() => scopedOrganizationsRequested).toBe(true);
});

test('organization edit configures, activates and enables miniapp identity login', async ({
  page,
}) => {
  const organizationCode = 'org-miniapp';
  const fullSecret = 'miniapp-secret-only-in-memory';
  const session = {
    ...tenantSession,
    capabilities: ['organization.read', 'miniapp.manage'],
  };
  const organization = {
    organizationCode,
    organizationName: '滨江回收中心',
    contactPhone: '0571-80000000',
    contactAddress: '杭州市滨江区',
    status: 'ENABLED',
    version: 3,
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-20T00:00:00Z',
  };
  let configuration:
    | {
        appId: string;
        displayName: string;
        appSecret: string;
        appSecretConfigured: true;
        maskedAppSecret: string;
        activated: boolean;
        loginEnabled: boolean;
        version: number;
        configuredAt: string;
        activatedAt: string | null;
        updatedAt: string;
      }
    | null = null;
  const mutations: Array<{
    path: string;
    body: Record<string, unknown>;
    idempotencyKey?: string;
  }> = [];
  const apiUrls: string[] = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    apiUrls.push(url.toString());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/csrf-token'
    ) {
      await json(route, {
        token: 'miniapp-csrf',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [organization],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    const configurationPath =
      `/api/v1/web/organizations/${organizationCode}/miniapp-configuration`;
    if (request.method() === 'GET' && url.pathname === configurationPath) {
      if (!configuration) {
        await route.fulfill(
          problem(404, 'COMMON.NOT_FOUND', '尚未配置小程序'),
        );
        return;
      }
      await json(route, configuration);
      return;
    }
    if (request.method() === 'PUT' && url.pathname === configurationPath) {
      const body = request.postDataJSON() as Record<string, unknown>;
      mutations.push({
        path: url.pathname,
        body,
        idempotencyKey: request.headers()['idempotency-key'],
      });
      configuration = {
        appId: String(body.appId),
        displayName: String(body.displayName),
        appSecret: String(body.appSecret),
        appSecretConfigured: true,
        maskedAppSecret: 'mini****mory',
        activated: false,
        loginEnabled: false,
        version: 1,
        configuredAt: '2026-07-31T02:00:00Z',
        activatedAt: null,
        updatedAt: '2026-07-31T02:00:00Z',
      };
      const { appSecret: _secret, ...mutation } = configuration;
      await json(route, mutation);
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname === `${configurationPath}/activations`
      && configuration
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      mutations.push({
        path: url.pathname,
        body,
        idempotencyKey: request.headers()['idempotency-key'],
      });
      configuration = {
        ...configuration,
        activated: true,
        version: 2,
        activatedAt: '2026-07-31T02:05:00Z',
        updatedAt: '2026-07-31T02:05:00Z',
      };
      const { appSecret: _secret, ...mutation } = configuration;
      await json(route, mutation);
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname
        === `/api/v1/web/organizations/${organizationCode}/miniapp-login/enablements`
      && configuration
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      mutations.push({
        path: url.pathname,
        body,
        idempotencyKey: request.headers()['idempotency-key'],
      });
      configuration = {
        ...configuration,
        loginEnabled: true,
        version: 3,
        updatedAt: '2026-07-31T02:10:00Z',
      };
      const { appSecret: _secret, ...mutation } = configuration;
      await json(route, mutation);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/organizations');
  await expect(page.getByText('滨江回收中心', { exact: true })).toBeVisible();
  await page.getByText('编辑', { exact: true }).click();
  await page.getByText('小程序登录', { exact: true }).click();
  await expect(page.getByText('尚未配置小程序身份登录')).toBeVisible();

  await page.getByPlaceholder('wx1234567890abcdef').fill('wx1234567890abcdef');
  await page
    .getByPlaceholder('用于识别该机构对应的小程序')
    .fill('滨江环保小程序');
  await page.getByPlaceholder('请输入 AppSecret').fill(fullSecret);
  await page.getByRole('button', { name: '创建配置' }).click();

  await expect(page.getByText('待激活', { exact: true })).toBeVisible();
  await expect(page.getByLabel('当前完整 AppSecret')).toHaveValue(fullSecret);
  expect(mutations[0].body).toMatchObject({
    appId: 'wx1234567890abcdef',
    displayName: '滨江环保小程序',
    appSecret: fullSecret,
    expectedVersion: null,
  });

  await page.getByRole('button', { name: '激活 AppID' }).click();
  await page.getByRole('button', { name: '确认激活' }).click();
  await expect(
    page.locator('.ant-tag').filter({ hasText: 'AppID 已激活' }),
  ).toBeVisible();
  expect(mutations[1].body).toMatchObject({ expectedVersion: 1 });

  await page.getByRole('button', { name: '启用身份登录' }).click();
  await page.getByRole('button', { name: '确认启用' }).click();
  await expect(page.getByText('登录已启用', { exact: true })).toBeVisible();
  expect(mutations[2].body).toMatchObject({ expectedVersion: 2 });
  expect(mutations.every((mutation) => mutation.idempotencyKey)).toBe(true);
  expect(apiUrls.every((url) => !url.includes(fullSecret))).toBe(true);
  const storedValues = await page.evaluate(() => [
    ...Object.values(localStorage),
    ...Object.values(sessionStorage),
  ]);
  expect(storedValues.every((value) => !value.includes(fullSecret))).toBe(true);
});

test('organization edit publishes an immutable delivery rule version', async ({
  page,
}) => {
  const organizationCode = 'org-delivery-rule';
  const session = {
    ...tenantSession,
    capabilities: [
      'organization.read',
      'delivery.configuration.manage',
    ],
  };
  const organization = {
    organizationCode,
    organizationName: '城北回收中心',
    contactPhone: '0571-81111111',
    contactAddress: '杭州市拱墅区',
    status: 'ENABLED',
    version: 2,
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-20T00:00:00Z',
  };
  let current = {
    versionNo: 1,
    contentSha256: 'a'.repeat(64),
    reviewMode: 'ALL_MANUAL',
    openBalanceFloorYuan: '-10.00',
    maxReviewAbsoluteWeightKg: '100.000',
    publicationSource: 'SYSTEM',
    publishedByStaffAccountUid: null as string | null,
    publishedBy: '系统',
    publishedAt: '2026-07-31T01:00:00Z',
    current: true,
  };
  const history = [current];
  const releases: Array<{
    body: Record<string, unknown>;
    idempotencyKey?: string;
  }> = [];
  let configurationReads = 0;
  let organizationUpdates = 0;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/csrf-token'
    ) {
      await json(route, {
        token: 'delivery-rule-csrf',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [organization],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'PUT'
      && url.pathname === `/api/v1/web/organizations/${organizationCode}`
    ) {
      organizationUpdates += 1;
      await json(route, organization);
      return;
    }
    const base = `/api/v1/web/organizations/${organizationCode}`;
    if (
      request.method() === 'GET'
      && url.pathname === `${base}/delivery-configuration`
    ) {
      configurationReads += 1;
      await json(route, current);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === `${base}/delivery-configuration-versions`
    ) {
      await json(route, {
        items: history,
        nextBeforeVersionNo: null,
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname === `${base}/delivery-configuration-releases`
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      releases.push({
        body,
        idempotencyKey: request.headers()['idempotency-key'],
      });
      current.current = false;
      current = {
        ...current,
        versionNo: 2,
        contentSha256: 'b'.repeat(64),
        openBalanceFloorYuan: String(body.openBalanceFloorYuan),
        maxReviewAbsoluteWeightKg: String(
          body.maxReviewAbsoluteWeightKg,
        ),
        publicationSource: 'STAFF',
        publishedByStaffAccountUid:
          '50000000-0000-4000-8000-000000000050',
        publishedBy: '林晓',
        publishedAt: '2026-08-01T02:00:00Z',
        current: true,
      };
      history.unshift(current);
      await json(route, current, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/organizations');
  await expect(page.getByText('城北回收中心', { exact: true })).toBeVisible();
  await page.getByText('编辑', { exact: true }).click();
  expect(configurationReads).toBe(0);

  await page.getByText('投递规则', { exact: true }).click();
  await expect(page.getByText('投递规则按版本发布')).toBeVisible();
  await expect(page.getByText('v1', { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole('button', { name: '保存机构资料' }),
  ).toHaveCount(0);

  await page.getByRole('button', { name: '发布新版本' }).click();
  await page.getByLabel('负余额停投下限（元）').fill('-20.00');
  await page
    .getByLabel('人工认定重量绝对值上限（kg）')
    .fill('200.000');
  await page.getByLabel('发布原因').fill('扩大人工审核重量范围');
  await page.getByRole('button', { name: '确认发布' }).click();

  await expect(page.getByText('v2', { exact: true }).first()).toBeVisible();
  expect(releases).toHaveLength(1);
  expect(releases[0].body).toEqual({
    expectedLatestVersion: 1,
    reviewMode: 'ALL_MANUAL',
    openBalanceFloorYuan: '-20.00',
    maxReviewAbsoluteWeightKg: '200.000',
    reason: '扩大人工审核重量范围',
  });
  expect(releases[0].idempotencyKey).toBeTruthy();
  expect(organizationUpdates).toBe(0);
});

test('staff table hides security versions and keeps access actions inside edit', async ({
  page,
}) => {
  const staffUid = '50000000-0000-4000-8000-000000000001';
  const session = {
    ...tenantSession,
    capabilities: [
      'staff.read',
      'staff.manage',
      'permission.read',
      'permission.manage',
      'organization.read',
    ],
  };
  const staff = {
    staffAccountUid: staffUid,
    accountKind: 'STAFF',
    loginName: 'field.operator',
    displayName: '现场工作人员',
    contactPhone: '138****2001',
    status: 'ENABLED',
    version: 7,
    authVersion: 4,
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-20T00:00:00Z',
  };
  const organization = {
    organizationCode: 'org-a',
    organizationName: '湖州运营中心',
    status: 'ENABLED',
    version: 2,
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-01T00:00:00Z',
  };
  let createPayload: Record<string, unknown> | undefined;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, { token: 'staff-csrf', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/staff-accounts'
    ) {
      await json(route, {
        items: [staff],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [organization],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/permission-definitions'
    ) {
      await json(route, [
        {
          permissionCode: 'device.read',
          scopeKind: 'TENANT',
          permissionName: '读取设备',
          description: '读取租户设备',
        },
        {
          permissionCode: 'device.manage',
          scopeKind: 'TENANT',
          permissionName: '管理设备',
          description: '管理租户设备',
        },
        {
          permissionCode: 'device.read',
          scopeKind: 'ORGANIZATION',
          permissionName: '读取设备',
          description: '读取机构设备',
        },
      ]);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/staff-accounts/current/effective-access'
    ) {
      await json(route, {
        staffAccountUid: tenantSession.subjectUid,
        tenantPermissionCodes: ['device.read', 'device.manage'],
        organizations: [{
          organizationCode: 'org-a',
          organizationName: '湖州运营中心',
          manager: true,
          permissionCodes: [],
        }],
        authVersion: 5,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/staff-accounts/${staffUid}/effective-access`
    ) {
      await json(route, {
        staffAccountUid: staffUid,
        tenantPermissionCodes: [],
        organizations: [],
        authVersion: 4,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-a/staff-memberships'
    ) {
      await json(route, {
        items: [],
        page: 1,
        pageSize: 200,
        total: 0,
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname === '/api/v1/web/staff-accounts'
    ) {
      createPayload = request.postDataJSON();
      await json(route, {
        ...staff,
        staffAccountUid: '50000000-0000-4000-8000-000000000002',
        loginName: 'device.operator',
        displayName: '设备工作人员',
        version: 1,
        authVersion: 1,
      }, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/staff');
  await expect(page.getByText('现场工作人员', { exact: true })).toBeVisible();
  await expect(page.getByText('v7 / auth 4', { exact: true })).toHaveCount(0);
  await expect(page.getByText('编辑', { exact: true })).toBeVisible();
  await expect(page.getByText('重置密码', { exact: true })).toHaveCount(0);

  await page.getByRole('button', { name: '创建工作人员' }).click();
  const createDialog = page.getByRole('dialog', { name: '创建工作人员' });
  await createDialog.getByLabel('全局登录名').fill('device.operator');
  await createDialog.getByLabel('初始密码').fill('test-password-2026');
  await createDialog
    .locator('.ant-form-item')
    .filter({ hasText: '展示名' })
    .locator('input')
    .fill('设备工作人员');
  await expect(
    createDialog.getByText('读取设备', { exact: true }),
  ).toHaveCount(0);
  await createDialog.getByRole('button', { name: '全选当前范围' }).click();
  await createDialog.getByRole('button', { name: /创\s*建/ }).click();
  await expect.poll(() => createPayload).toEqual({
    loginName: 'device.operator',
    initialPassword: 'test-password-2026',
    displayName: '设备工作人员',
    permissionCodes: ['device.manage', 'device.read'],
  });

  await page.getByText('编辑', { exact: true }).click();
  await expect(page.getByText('账号安全', { exact: true })).toBeVisible();
  await expect(page.getByText('任职与授权', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '重置密码' })).toBeVisible();
  await expect(page.getByText('机构任职', { exact: true })).toBeVisible();
});

test('device management consumes the organization deep link and target API', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: ['device.read'],
    organizations: [{
      organizationCode: 'org-b',
      organizationName: '设备运营中心',
    }],
  };
  let requestedOrganization = false;
  let organizationDirectoryRequested = false;
  const deployment = {
    deploymentCode: 'dp-hz-01',
    tenantCode: 'tenant-a',
    organizationCode: 'org-b',
    asset: {
      hardwareSn: 'EC-BOX-0001',
      modelCode: 'ECO-6P',
      expectedPortCount: 6,
      lifecycleStatus: 'IN_USE',
      version: 2,
    },
    lifecycleStatus: 'ENABLED',
    businessEnabled: true,
    portCount: 6,
    latestConfigurationVersion: 8,
    appliedConfigurationVersion: 8,
    configurationApplicationStatus: 'APPLIED',
    edgeConnectionStatus: 'OFFLINE',
    oneNetConnectionStatus: 'ONLINE',
    oneNetStatusObservedAt: '2026-08-02T02:00:00.123Z',
    trustedRuntimeReceivedAt: '2026-08-02T01:55:00.123Z',
    version: 5,
    commissionedAt: '2026-07-02T00:00:00Z',
    enabledAt: '2026-07-03T00:00:00Z',
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-20T00:00:00Z',
  };
  const runtime = {
    deploymentCode: deployment.deploymentCode,
    lifecycleStatus: deployment.lifecycleStatus,
    businessEnabled: deployment.businessEnabled,
    deploymentVersion: deployment.version,
    configuration: {
      latestPublishedVersion: 8,
      latestAppliedVersion: 8,
      latestApplicationStatus: 'APPLIED',
      latestPreciselyApplied: true,
    },
    health: {
      edgeConnectionStatus: 'OFFLINE',
      oneNetConnectionStatus: 'ONLINE',
      oneNetStatusObservedAt: '2026-08-02T02:00:00.123Z',
      trustedRuntimeReceivedAt: '2026-08-02T01:55:00.123Z',
      mcuLinkStatus: 'ONLINE',
      safetyStatus: 'SAFE',
      aggregateWeightHealth: 'HEALTHY',
      cameraHealth: 'HEALTHY',
      localStorageHealth: 'HEALTHY',
      clockSyncHealth: 'HEALTHY',
      edgeSoftwareVersion: '1.0.0',
      mcuFirmwareVersion: 'fixed-frame',
      uartState: 'ONLINE',
      uartProtocolMajor: 1,
      uartProtocolMinor: 0,
      capabilityBitmapHex: '01',
      lastHeartbeatAt: '2026-08-02T01:55:00.123Z',
      lastDeviceEventAt: '2026-08-02T01:55:00.123Z',
      runtimeVersion: 9,
    },
    occupied: false,
    deliveryAllowed: false,
    cleaningAllowed: false,
    deliveryBlockers: ['EDGE_OFFLINE'],
    cleaningBlockers: ['EDGE_OFFLINE'],
  };

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      organizationDirectoryRequested = true;
      await route.fulfill(problem(403, 'SECURITY.FORBIDDEN', '没有机构目录权限'));
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-b/device-deployments'
    ) {
      requestedOrganization = true;
      await json(route, {
        items: [deployment],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    const deploymentBase =
      '/api/v1/web/organizations/org-b/device-deployments/dp-hz-01';
    if (request.method() === 'GET' && url.pathname === deploymentBase) {
      await json(route, deployment);
      return;
    }
    if (request.method() === 'GET' && url.pathname === `${deploymentBase}/runtime`) {
      await json(route, runtime);
      return;
    }
    if (request.method() === 'GET' && url.pathname === `${deploymentBase}/ports`) {
      await json(route, []);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === `${deploymentBase}/configuration-versions`
    ) {
      await json(route, { items: [], nextBeforeVersionNo: null });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/devices?organization=org-b');
  await expect.poll(() => requestedOrganization).toBe(true);
  expect(organizationDirectoryRequested).toBe(false);
  await expect(page).toHaveURL(/organization=org-b/);
  await expect(
    page.getByText('设备运营中心 · org-b', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('dp-hz-01', { exact: true })).toBeVisible();
  await expect(page.getByText('EC-BOX-0001', { exact: true })).toBeVisible();
  const deploymentRow = page.getByRole('row', { name: /dp-hz-01/ });
  await expect(deploymentRow.getByText('在线', { exact: true })).toBeVisible();
  await expect(deploymentRow.getByText('离线', { exact: true })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: 'OneNet 传输' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: '业务有效在线' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '机构部署' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '租户设备池' })).toHaveCount(0);
  await expect(page.getByRole('tab', { name: '平台资产' })).toHaveCount(0);

  await page.getByText('dp-hz-01', { exact: true }).click();
  const drawer = page.locator('.ant-drawer').filter({ hasText: '设备接入' });
  await expect(
    drawer.getByText('OneNet 已连接，但业务运行事实不可用'),
  ).toBeVisible();
  await drawer.getByRole('tab', { name: '运行事实' }).click();
  await expect(drawer.getByText('两层在线事实不能互相替代')).toBeVisible();
  await expect(drawer.getByText('OneNet 状态观测时间')).toBeVisible();
  await expect(drawer.getByText('可信运行事实时间')).toBeVisible();
});

test('staff with tenant allocation capability sees the tenant pool and deployment entry', async ({
  page,
}) => {
  const allocationUid = '5a000000-0000-4000-8000-000000000001';
  const session = {
    ...tenantSession,
    capabilities: ['device.read', 'device.allocation.manage'],
    organizations: [
      { organizationCode: 'org-a', organizationName: '东门站点' },
      { organizationCode: 'org-b', organizationName: '城北站点' },
    ],
  };
  const allocation = {
    allocationUid,
    tenantCode: 'tenant-a',
    hardwareSn: 'SN-STAFF-POOL-01',
    modelCode: 'ECOBIN-V1',
    expectedPortCount: 1,
    allocationStatus: 'ACTIVE',
    assetLifecycleStatus: 'ALLOCATED',
    allocationSource: 'PLATFORM_ASSIGNMENT',
    currentDeploymentCode: null,
    currentOrganizationCode: null,
    credentialRotationRequired: false,
    allocationVersion: 2,
    assetVersion: 4,
    allocatedAt: '2026-08-02T02:00:00.123Z',
    endedAt: null,
    endMode: null,
    endReason: null,
  };
  let deploymentRequest: {
    body: unknown;
    idempotencyKey?: string;
  } | undefined;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, {
        token: 'csrf-staff-allocation',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && /^\/api\/v1\/web\/organizations\/[^/]+\/device-deployments$/.test(
        url.pathname,
      )
    ) {
      await json(route, { items: [], page: 1, pageSize: 20, total: 0 });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname === '/api/v1/web/organizations/org-b/device-deployments'
    ) {
      deploymentRequest = {
        body: request.postDataJSON(),
        idempotencyKey: request.headers()['idempotency-key'],
      };
      await json(route, {
        deploymentCode: 'dp-staff-pool-01',
        tenantCode: 'tenant-a',
        organizationCode: 'org-b',
      }, 201);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/device-asset-allocations'
    ) {
      await json(route, {
        items: [allocation],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/devices');
  const poolTab = page.getByRole('tab', { name: '租户设备池' });
  await expect(poolTab).toBeVisible();
  await poolTab.click();
  await expect(page.getByText('SN-STAFF-POOL-01', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '部署到机构' }).click();

  const deploymentDialog = page.getByRole('dialog', {
    name: '从租户设备池部署到机构',
  });
  await expect(deploymentDialog).toBeVisible();
  await expect(deploymentDialog.getByText('SN-STAFF-POOL-01')).toBeVisible();
  await deploymentDialog.locator('.ant-select-selector').click();
  await expect(
    page.locator('.ant-select-dropdown:visible')
      .getByText('城北站点 · org-b', { exact: true }),
  ).toBeVisible();
  await page.locator('.ant-select-dropdown:visible')
    .getByText('城北站点 · org-b', { exact: true })
    .click();
  await deploymentDialog.getByRole('button', { name: '创建新部署' }).click();
  await expect.poll(() => deploymentRequest).toEqual({
    body: {
      allocationUid,
      expectedAllocationVersion: 2,
    },
    idempotencyKey: expect.any(String),
  });
});

test('platform registers an asset and allocates it to a tenant without choosing an organization', async ({
  page,
}) => {
  const tenantCode = 'tenant-device';
  const organizationCode = 'org-device';
  const hardwareSn = 'SN-WEB-ONBOARD-01';
  const deploymentCode = 'dp-web-onboard-01';
  const applicationUid = '50000000-0000-4000-8000-000000000001';
  const session = {
    ...platformSession,
    capabilities: [
      'device.read',
      'device.manage',
      'device.configuration.manage',
    ],
  };
  const tenant = {
    tenantCode,
    enterpriseName: '设备接入测试租户',
    status: 'ENABLED',
    contactName: '测试人员',
    contactPhone: '138****0000',
    contactAddress: '杭州市',
    version: 1,
    principalAccount: null,
    createdAt: '2026-08-01T00:00:00.123Z',
    updatedAt: '2026-08-01T00:00:00.123Z',
  };
  const organization = {
    organizationCode,
    organizationName: '设备接入中心',
    contactPhone: '0571-80000000',
    contactAddress: '杭州市滨江区',
    status: 'ENABLED',
    version: 1,
    createdAt: '2026-08-01T00:00:00.123Z',
    updatedAt: '2026-08-01T00:00:00.123Z',
  };
  let asset: Record<string, unknown> | undefined;
  let allocation: Record<string, unknown> | undefined;
  let deployment: Record<string, any> | undefined;
  let configurationPublished = false;
  const mutationHeaders: string[] = [];
  const mutationPayloads: Array<{ path: string; body: any }> = [];

  const applicationSummary = () => ({
    applicationUid,
    status: 'APPLIED',
    dispatchState: 'DONE',
    version: 2,
  });
  const configurationDevice = {
    displayName: '新接入设备',
    address: null,
    longitude: null,
    latitude: null,
    edgeHeartbeatIntervalMs: 30000,
    edgeHeartbeatMissThreshold: 3,
    mcuHeartbeatIntervalMs: 5000,
    mcuHeartbeatMissThreshold: 3,
    doorCloseRetryLimit: 3,
    continueDeliveryWaitMs: 30000,
    negativeWeightThresholdGram: 500,
  };
  const configurationPort = {
    portNo: 1,
    displayName: '1 号投口',
    enabled: true,
    unitPriceYuanPerKg: '0.8000',
    fullnessMode: 'INFRARED_OR_WEIGHT',
    fullnessWeightKg: '50.00',
    deliverySettleDelayMs: 3000,
    fullnessInitialDelayMs: 3000,
    fullnessRecheckDelayMs: 10000,
    doorAutoCloseTimeoutMs: 60000,
  };
  const configurationVersion = () => ({
    deploymentCode,
    versionNo: 1,
    schemaVersion: 1,
    contentSha256: 'a'.repeat(64),
    mcuPayloadSha256: 'b'.repeat(64),
    device: configurationDevice,
    ports: [configurationPort],
    publicationSource: 'WEB',
    publishedBy: '平台管理员',
    publishedAt: '2026-08-01T01:00:00.123Z',
    application: applicationSummary(),
  });
  const runtime = () => ({
    deploymentCode,
    lifecycleStatus: deployment?.lifecycleStatus ?? 'COMMISSIONING',
    businessEnabled: deployment?.businessEnabled ?? false,
    deploymentVersion: deployment?.version ?? 0,
    configuration: {
      latestPublishedVersion: configurationPublished ? 1 : null,
      latestAppliedVersion: configurationPublished ? 1 : null,
      latestApplicationStatus: configurationPublished ? 'APPLIED' : null,
      latestPreciselyApplied: configurationPublished,
    },
    health: {
      edgeConnectionStatus: 'ONLINE',
      oneNetConnectionStatus: 'ONLINE',
      oneNetStatusObservedAt: '2026-08-01T01:01:00.123Z',
      trustedRuntimeReceivedAt: '2026-08-01T01:01:00.123Z',
      mcuLinkStatus: 'ONLINE',
      safetyStatus: 'SAFE',
      aggregateWeightHealth: 'HEALTHY',
      cameraHealth: 'HEALTHY',
      localStorageHealth: 'HEALTHY',
      clockSyncHealth: 'HEALTHY',
      edgeSoftwareVersion: '1.0.0',
      mcuFirmwareVersion: 'fixed-frame',
      uartState: 'ONLINE',
      uartProtocolMajor: 1,
      uartProtocolMinor: 0,
      capabilityBitmapHex: '01',
      lastHeartbeatAt: '2026-08-01T01:01:00.123Z',
      lastDeviceEventAt: '2026-08-01T01:01:00.123Z',
      runtimeVersion: 3,
    },
    occupied: false,
    deliveryAllowed: deployment?.businessEnabled === true,
    cleaningAllowed: deployment?.businessEnabled === true,
    deliveryBlockers: deployment?.businessEnabled
      ? []
      : ['BUSINESS_SWITCH_DISABLED'],
    cleaningBlockers: deployment?.businessEnabled
      ? []
      : ['BUSINESS_SWITCH_DISABLED'],
  });

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, { token: 'csrf-device', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (
      method === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await route.fulfill(problem(401));
      return;
    }
    if (
      method === 'GET'
      && url.pathname === '/api/v1/web/platform/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      method === 'GET'
      && url.pathname === '/api/v1/web/platform/tenants'
    ) {
      await json(route, { items: [tenant], page: 1, pageSize: 200, total: 1 });
      return;
    }
    if (
      method === 'GET'
      && url.pathname
        === `/api/v1/web/platform/tenants/${tenantCode}/organizations`
    ) {
      await json(route, {
        items: [organization],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (url.pathname === '/api/v1/web/platform/device-assets') {
      if (method === 'GET') {
        await json(route, {
          items: asset ? [asset] : [],
          page: 1,
          pageSize: 20,
          total: asset ? 1 : 0,
        });
        return;
      }
      if (method === 'POST') {
        mutationHeaders.push(request.headers()['idempotency-key']);
        mutationPayloads.push({ path: url.pathname, body: request.postDataJSON() });
        const body = request.postDataJSON();
        asset = {
          ...body,
          lifecycleStatus: 'IN_STOCK',
          currentDeployment: null,
          version: 0,
          createdAt: '2026-08-01T00:10:00.123Z',
          updatedAt: '2026-08-01T00:10:00.123Z',
          oneNetMapping: {
            productId: 'onenet-product',
            deviceName: hardwareSn,
            currentComputedValue: true,
          },
        };
        await json(route, asset, 201);
        return;
      }
    }
    if (
      url.pathname === `/api/v1/web/platform/device-assets/${hardwareSn}`
      && method === 'GET'
    ) {
      await json(route, asset);
      return;
    }
    if (
      url.pathname === '/api/v1/web/platform/device-asset-allocations'
      && method === 'GET'
    ) {
      await json(route, {
        items: allocation ? [allocation] : [],
        page: 1,
        pageSize: 200,
        total: allocation ? 1 : 0,
      });
      return;
    }
    if (
      url.pathname
        === `/api/v1/web/platform/tenants/${tenantCode}/device-asset-allocations`
      && method === 'POST'
    ) {
      mutationHeaders.push(request.headers()['idempotency-key']);
      mutationPayloads.push({ path: url.pathname, body: request.postDataJSON() });
      allocation = {
        allocationUid: '60000000-0000-4000-8000-000000000001',
        tenantCode,
        hardwareSn,
        modelCode: 'ECOBIN-V1',
        expectedPortCount: 1,
        allocationStatus: 'ACTIVE',
        assetLifecycleStatus: 'ALLOCATED',
        allocationSource: 'PLATFORM_ASSIGNMENT',
        currentDeploymentCode: null,
        currentOrganizationCode: null,
        credentialRotationRequired: false,
        allocationVersion: 0,
        assetVersion: 1,
        allocatedAt: '2026-08-01T00:15:00.123Z',
        endedAt: null,
        endMode: null,
        endReason: null,
      };
      asset = { ...asset, lifecycleStatus: 'ALLOCATED', version: 1 };
      await json(route, allocation, 201);
      return;
    }
    const deploymentBase =
      `/api/v1/web/platform/tenants/${tenantCode}`
      + `/organizations/${organizationCode}/device-deployments`;
    if (url.pathname === deploymentBase) {
      if (method === 'GET') {
        await json(route, {
          items: deployment ? [deployment] : [],
          page: 1,
          pageSize: 20,
          total: deployment ? 1 : 0,
        });
        return;
      }
    }
    if (url.pathname === `${deploymentBase}/${deploymentCode}` && method === 'GET') {
      await json(route, deployment);
      return;
    }
    if (
      url.pathname === `${deploymentBase}/${deploymentCode}/runtime`
      && method === 'GET'
    ) {
      await json(route, runtime());
      return;
    }
    if (
      url.pathname === `${deploymentBase}/${deploymentCode}/ports`
      && method === 'GET'
    ) {
      await json(route, [{
        portNo: 1,
        displayName: configurationPublished ? '1 号投口' : null,
        enabled: configurationPublished ? true : null,
        unitPriceYuanPerKg: configurationPublished ? '0.8000' : null,
        fullnessMode: configurationPublished ? 'INFRARED_OR_WEIGHT' : null,
        configurationVersion: configurationPublished ? 1 : null,
      }]);
      return;
    }
    if (
      url.pathname === `${deploymentBase}/${deploymentCode}/configuration-versions`
      && method === 'GET'
    ) {
      await json(route, {
        items: configurationPublished ? [{
          versionNo: 1,
          contentSha256: 'a'.repeat(64),
          mcuPayloadSha256: 'b'.repeat(64),
          deviceDisplayName: '新接入设备',
          publicationSource: 'WEB',
          publishedBy: '平台管理员',
          publishedAt: '2026-08-01T01:00:00.123Z',
          application: applicationSummary(),
        }] : [],
        nextBeforeVersionNo: null,
      });
      return;
    }
    if (
      url.pathname
        === `${deploymentBase}/${deploymentCode}/configuration-versions/1`
      && method === 'GET'
    ) {
      await json(route, configurationVersion());
      return;
    }
    const applicationPath =
      `${deploymentBase}/${deploymentCode}`
      + `/configuration-applications/${applicationUid}`;
    if (url.pathname === applicationPath && method === 'GET') {
      await json(route, {
        applicationUid,
        versionNo: 1,
        contentSha256: 'a'.repeat(64),
        mcuPayloadSha256: 'b'.repeat(64),
        status: 'APPLIED',
        version: 2,
        latestDesired: true,
        superseded: false,
        deviceReportedVersionNo: 1,
        deviceReportedContentSha256: 'a'.repeat(64),
        deviceReportedMcuPayloadSha256: 'b'.repeat(64),
        edgePersistedAt: '2026-08-01T01:00:02.123Z',
        mcuSyncedAt: '2026-08-01T01:00:03.123Z',
        appliedAt: '2026-08-01T01:00:03.123Z',
        lastFailureCode: null,
        lastFailedAt: null,
        dispatchState: 'DONE',
        recommendedPollAfterMs: null,
        nextActions: [],
      });
      return;
    }
    if (
      url.pathname === `${deploymentBase}/${deploymentCode}/configuration-releases`
      && method === 'POST'
    ) {
      mutationHeaders.push(request.headers()['idempotency-key']);
      mutationPayloads.push({ path: url.pathname, body: request.postDataJSON() });
      configurationPublished = true;
      deployment = {
        ...deployment,
        latestConfigurationVersion: 1,
        appliedConfigurationVersion: 1,
        configurationApplicationStatus: 'APPLIED',
      };
      const accepted = {
        operationId: request.headers()['idempotency-key'],
        resourceId: applicationUid,
        applicationUid,
        versionNo: 1,
        contentSha256: 'a'.repeat(64),
        mcuPayloadSha256: 'b'.repeat(64),
        status: 'PENDING',
        dispatchState: 'PENDING',
        statusUrl: applicationPath,
        recommendedPollAfterMs: 1000,
      };
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        headers: { Location: applicationPath },
        body: JSON.stringify(envelope(accepted)),
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/devices');
  await expect(page.getByRole('tab', { name: '平台资产' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '租户分配' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '机构部署' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '租户设备池' })).toHaveCount(0);
  await page.getByRole('button', { name: '登记硬件' }).click();
  const assetDialog = page.getByRole('dialog', { name: '登记平台物理设备' });
  await assetDialog.getByPlaceholder('例如 SN-001').fill(hardwareSn);
  await assetDialog.getByPlaceholder('例如 ECOBIN-V1').fill('ECOBIN-V1');
  await assetDialog.getByPlaceholder('可选，例如 2026-08').fill('2026-08');
  await assetDialog.getByRole('spinbutton').fill('1');
  await assetDialog.getByRole('button', { name: '确认登记' }).click();
  const assetDrawer = page.locator('.ant-drawer').filter({
    hasText: '平台资产详情',
  });
  await expect(assetDrawer.getByText(hardwareSn, { exact: true }).first()).toBeVisible();
  await assetDrawer.getByRole('button', { name: '分配给租户' }).click();
  const allocationDialog = page.getByRole('dialog', { name: '分配给租户' });
  await allocationDialog.getByLabel('目标租户').click();
  await page.locator('.ant-select-dropdown:visible')
    .getByText('设备接入测试租户 · tenant-device', { exact: true })
    .click();
  await allocationDialog.getByLabel('分配原因').fill('投放到试点租户设备池');
  await allocationDialog.getByRole('button', { name: '提交分配' }).click();
  await expect(assetDrawer.getByText('已分配租户', { exact: true })).toBeVisible();
  await expect(assetDrawer.getByText(tenantCode, { exact: true }).first()).toBeVisible();

  expect(mutationHeaders).toHaveLength(2);
  expect(mutationHeaders.every(Boolean)).toBe(true);
  expect(mutationPayloads[1].body).toEqual({
    hardwareSn,
    expectedAssetVersion: 0,
    reason: '投放到试点租户设备池',
  });
});

test('platform handles reclaim blockers, maintenance clearance and cross-tenant credential rotation', async ({
  page,
}) => {
  const hardwareSn = 'SN-RECLAIM-01';
  const allocationUid = '61000000-0000-4000-8000-000000000001';
  const tenantA = 'tenant-a';
  const tenantB = 'tenant-b';
  let assetVersion = 7;
  let lifecycleStatus = 'ALLOCATED';
  let allocationStatus = 'ACTIVE';
  let allocationTenant = tenantA;
  let allocationVersion = 3;
  let normalReclaimAttempts = 0;
  let clearanceAttempts = 0;
  let targetAllocationAttempts = 0;
  const mutations: Array<{ path: string; body: any; key?: string }> = [];
  const session = {
    ...platformSession,
    capabilities: ['device.read', 'device.manage'],
  };
  const tenants = [tenantA, tenantB].map((tenantCode, index) => ({
    tenantCode,
    enterpriseName: index === 0 ? '原租户' : '目标租户',
    status: 'ENABLED',
    contactPhone: null,
    contactAddress: null,
    version: 1,
    principalAccount: null,
    createdAt: '2026-08-01T00:00:00.123Z',
    updatedAt: '2026-08-01T00:00:00.123Z',
  }));
  const asset = () => ({
    hardwareSn,
    modelCode: 'ECOBIN-V1',
    productionBatch: '2026-08',
    expectedPortCount: 2,
    lifecycleStatus,
    currentDeployment: null,
    version: assetVersion,
    createdAt: '2026-08-01T00:00:00.123Z',
    updatedAt: '2026-08-01T01:00:00.123Z',
    oneNetMapping: {
      productId: 'onenet-product',
      deviceName: hardwareSn,
      currentComputedValue: true,
    },
  });
  const allocation = () => ({
    allocationUid,
    tenantCode: allocationTenant,
    hardwareSn,
    modelCode: 'ECOBIN-V1',
    expectedPortCount: 2,
    allocationStatus,
    assetLifecycleStatus: lifecycleStatus,
    allocationSource: 'PLATFORM_ASSIGNMENT',
    currentDeploymentCode: null,
    currentOrganizationCode: null,
    credentialRotationRequired: false,
    allocationVersion,
    assetVersion,
    allocatedAt: '2026-08-01T00:10:00.123Z',
    endedAt: allocationStatus === 'ENDED'
      ? '2026-08-01T01:10:00.123Z'
      : null,
    endMode: allocationStatus === 'ENDED' ? 'EXCEPTIONAL' : null,
    endReason: allocationStatus === 'ENDED' ? '设备故障，平台取回检查' : null,
  });

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, { token: 'csrf-reclaim', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (method === 'GET' && url.pathname === '/api/v1/web/auth/sessions/current') {
      await route.fulfill(problem(401));
      return;
    }
    if (
      method === 'GET'
      && url.pathname === '/api/v1/web/platform/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (method === 'GET' && url.pathname === '/api/v1/web/platform/tenants') {
      await json(route, { items: tenants, page: 1, pageSize: 200, total: 2 });
      return;
    }
    if (
      method === 'GET'
      && /^\/api\/v1\/web\/platform\/tenants\/[^/]+\/organizations$/.test(
        url.pathname,
      )
    ) {
      await json(route, { items: [], page: 1, pageSize: 200, total: 0 });
      return;
    }
    if (url.pathname === '/api/v1/web/platform/device-assets' && method === 'GET') {
      await json(route, { items: [asset()], page: 1, pageSize: 20, total: 1 });
      return;
    }
    if (
      url.pathname === `/api/v1/web/platform/device-assets/${hardwareSn}`
      && method === 'GET'
    ) {
      await json(route, asset());
      return;
    }
    if (
      url.pathname === '/api/v1/web/platform/device-asset-allocations'
      && method === 'GET'
    ) {
      await json(route, {
        items: allocationStatus === 'ACTIVE' || allocationStatus === 'ENDED'
          ? [allocation()]
          : [],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      url.pathname
        === `/api/v1/web/platform/device-asset-allocations/${allocationUid}/reclaims`
      && method === 'POST'
    ) {
      const body = request.postDataJSON();
      mutations.push({
        path: url.pathname,
        body,
        key: request.headers()['idempotency-key'],
      });
      if (body.mode === 'NORMAL') {
        normalReclaimAttempts += 1;
        if (normalReclaimAttempts === 1) {
          await route.fulfill({
            status: 503,
            contentType: 'application/problem+json',
            body: JSON.stringify({
              code: 'COMMON.SERVICE_UNAVAILABLE',
              message: '设备服务暂时不可用，请重试',
              requestId: 'req-reclaim-retry',
              retryable: true,
              details: {},
            }),
          });
          return;
        }
        await route.fulfill({
          status: 422,
          contentType: 'application/problem+json',
          body: JSON.stringify({
            code: 'DEVICE.ALLOCATION_NOT_NORMALLY_RECLAIMABLE',
            message: '当前运行事实不允许正常收回',
            requestId: 'req-reclaim-blocked',
            retryable: false,
            details: {
              blockers: ['BUSINESS_STILL_ENABLED', 'PENDING_RELIABLE_EVENTS'],
            },
          }),
        });
        return;
      }
      lifecycleStatus = 'MAINTENANCE';
      allocationStatus = 'ENDED';
      allocationVersion += 1;
      assetVersion += 1;
      await json(route, allocation());
      return;
    }
    if (
      url.pathname
        === `/api/v1/web/platform/device-assets/${hardwareSn}/maintenance-clearances`
      && method === 'POST'
    ) {
      clearanceAttempts += 1;
      mutations.push({
        path: url.pathname,
        body: request.postDataJSON(),
        key: request.headers()['idempotency-key'],
      });
      if (clearanceAttempts === 1) {
        assetVersion += 1;
        await route.fulfill({
          status: 409,
          contentType: 'application/problem+json',
          body: JSON.stringify({
            code: 'COMMON.VERSION_CONFLICT',
            message: '资产版本已变化',
            requestId: 'req-clearance-conflict',
            retryable: false,
            details: {},
          }),
        });
        return;
      }
      lifecycleStatus = 'IN_STOCK';
      assetVersion += 1;
      await json(route, allocation());
      return;
    }
    if (
      url.pathname
        === `/api/v1/web/platform/tenants/${tenantB}/device-asset-allocations`
      && method === 'POST'
    ) {
      targetAllocationAttempts += 1;
      mutations.push({
        path: url.pathname,
        body: request.postDataJSON(),
        key: request.headers()['idempotency-key'],
      });
      if (targetAllocationAttempts === 1) {
        await route.fulfill({
          status: 422,
          contentType: 'application/problem+json',
          body: JSON.stringify({
            code: 'DEVICE.CREDENTIAL_ROTATION_REQUIRED',
            message: '跨租户重新分配前必须轮换 OneNet Device Key',
            requestId: 'req-rotation-required',
            retryable: false,
            details: { blockers: ['ONENET_CREDENTIAL_ROTATION_REQUIRED'] },
          }),
        });
        return;
      }
      allocationTenant = tenantB;
      allocationStatus = 'ACTIVE';
      allocationVersion += 1;
      lifecycleStatus = 'ALLOCATED';
      assetVersion += 1;
      await json(route, allocation(), 201);
      return;
    }
    if (
      url.pathname
        === `/api/v1/web/platform/device-assets/${hardwareSn}/onenet-credential-rotation-confirmations`
      && method === 'POST'
    ) {
      mutations.push({
        path: url.pathname,
        body: request.postDataJSON(),
        key: request.headers()['idempotency-key'],
      });
      assetVersion += 1;
      await json(route, {
        hardwareSn,
        confirmedAt: '2026-08-01T01:20:00.123Z',
        assetVersion,
      });
      return;
    }
    await route.fulfill(problem(404, 'COMMON.NOT_FOUND', '接口不存在'));
  });

  await page.goto('/devices');
  await page.getByRole('button', { name: '详情' }).click();
  const drawer = page.getByRole('dialog', { name: /平台资产详情/ });
  await drawer.getByRole('button', { name: '正常 / 异常收回' }).click();
  const reclaimDialog = page.getByRole('dialog', { name: '平台收回设备' });
  await reclaimDialog.getByRole('checkbox', {
    name: '我确认设备实物已由平台收回',
  }).check();
  await reclaimDialog.getByLabel('收回原因').fill('经营结束后正常回收');
  await reclaimDialog.getByRole('button', { name: '确认收回' }).click();
  await expect(reclaimDialog.getByText(/设备服务暂时不可用，请重试/)).toBeVisible();
  await reclaimDialog.getByRole('button', { name: '确认收回' }).click();
  await expect(reclaimDialog.getByText('经营开关尚未关闭')).toBeVisible();
  await expect(reclaimDialog.getByText('设备还有待上传的可靠事件')).toBeVisible();
  expect(normalReclaimAttempts).toBe(2);
  expect(mutations[0].key).toBe(mutations[1].key);

  await reclaimDialog.locator('.ant-select-selector').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option')
    .filter({ hasText: '异常收回（进维修隔离）' })
    .click();
  await reclaimDialog.getByLabel('收回原因').fill('设备故障，平台取回检查');
  await reclaimDialog.getByRole('button', { name: '确认收回' }).click();
  await expect(drawer.getByText('维修隔离', { exact: true }).first()).toBeVisible();
  await drawer.getByRole('button', { name: '解除维修隔离' }).click();
  let clearanceDialog = page.getByRole('dialog', { name: '解除维修隔离' });
  await clearanceDialog.getByRole('checkbox', {
    name: '设备实物已在平台手中',
  }).check();
  await clearanceDialog.getByRole('checkbox', {
    name: '维修检查已通过，可重新入库',
  }).check();
  await clearanceDialog.getByLabel('解除原因').fill('更换主板后复检通过');
  await clearanceDialog.getByRole('button', { name: '确认解除' }).click();
  await expect(page.getByText('资产或分配状态已更新，已刷新详情，请重新确认')).toBeVisible();
  await expect(clearanceDialog).toHaveCount(0);
  await drawer.getByRole('button', { name: '解除维修隔离' }).click();
  clearanceDialog = page.getByRole('dialog', { name: '解除维修隔离' });
  await expect(clearanceDialog.getByRole('checkbox', {
    name: '设备实物已在平台手中',
  })).not.toBeChecked();
  await clearanceDialog.getByRole('checkbox', {
    name: '设备实物已在平台手中',
  }).check();
  await clearanceDialog.getByRole('checkbox', {
    name: '维修检查已通过，可重新入库',
  }).check();
  await clearanceDialog.getByLabel('解除原因').fill('更换主板后复检通过');
  await clearanceDialog.getByRole('button', { name: '确认解除' }).click();
  await expect(drawer.getByText('平台库存', { exact: true }).first()).toBeVisible();

  await drawer.getByRole('button', { name: '分配给租户' }).click();
  const allocationDialog = page.getByRole('dialog', { name: '分配给租户' });
  await allocationDialog.getByLabel('目标租户').click();
  await page.locator('.ant-select-dropdown:visible')
    .getByText('目标租户 · tenant-b', { exact: true })
    .click();
  await allocationDialog.getByLabel('分配原因').fill('调拨到目标租户');
  await allocationDialog.getByRole('button', { name: '提交分配' }).click();
  await expect(allocationDialog.getByText(/线下更换 Device Key/)).toBeVisible();
  await allocationDialog.getByRole('button', {
    name: '确认已轮换 Device Key',
  }).click();
  await expect(page.getByText('已记录密钥轮换确认，请重新提交租户分配')).toBeVisible();
  await allocationDialog.getByRole('button', { name: '提交分配' }).click();
  await expect(drawer.getByText(tenantB, { exact: true }).first()).toBeVisible();

  expect(mutations.every((mutation) => Boolean(mutation.key))).toBe(true);
  expect(mutations.map((mutation) => mutation.body)).toEqual([
    {
      expectedAllocationVersion: 3,
      expectedAssetVersion: 7,
      mode: 'NORMAL',
      physicalPossessionConfirmed: true,
      reason: '经营结束后正常回收',
    },
    {
      expectedAllocationVersion: 3,
      expectedAssetVersion: 7,
      mode: 'NORMAL',
      physicalPossessionConfirmed: true,
      reason: '经营结束后正常回收',
    },
    {
      expectedAllocationVersion: 3,
      expectedAssetVersion: 7,
      mode: 'EXCEPTIONAL',
      physicalPossessionConfirmed: true,
      reason: '设备故障，平台取回检查',
    },
    {
      expectedAssetVersion: 8,
      physicalPossessionConfirmed: true,
      inspectionConfirmed: true,
      reason: '更换主板后复检通过',
    },
    {
      expectedAssetVersion: 9,
      physicalPossessionConfirmed: true,
      inspectionConfirmed: true,
      reason: '更换主板后复检通过',
    },
    {
      hardwareSn,
      expectedAssetVersion: 10,
      reason: '调拨到目标租户',
    },
    {
      expectedAssetVersion: 10,
      reason: '调拨到目标租户',
    },
    {
      hardwareSn,
      expectedAssetVersion: 11,
      reason: '调拨到目标租户',
    },
  ]);
});

test('tenant closes business, returns a deployment to the pool, then creates a new deployment', async ({
  page,
}) => {
  const hardwareSn = 'SN-TRANSFER-01';
  const allocationUid = '70000000-0000-4000-8000-000000000001';
  const tenantCode = 'tenant-a';
  const orgA = 'org-a';
  const orgB = 'org-b';
  const oldDeploymentCode = 'dp-org-a-01';
  const newDeploymentCode = 'dp-org-b-01';
  let allocationVersion = 1;
  let currentOrganization: string | null = orgA;
  let currentDeployment: string | null = oldDeploymentCode;
  let businessEnabled = true;
  let oldDeploymentVersion = 3;
  let newDeploymentCreated = false;
  const mutations: Array<{ path: string; body: any; key?: string }> = [];
  const session = {
    ...tenantSession,
    accountType: 'TENANT_PRINCIPAL',
    tenantCode,
    capabilities: [
      'device.read',
      'device.configuration.manage',
      'device.allocation.manage',
      'device.business.manage',
    ],
    organizations: [
      { organizationCode: orgA, organizationName: '东门站点' },
      { organizationCode: orgB, organizationName: '城北站点' },
    ],
  };
  const allocation = () => ({
    allocationUid,
    tenantCode,
    hardwareSn,
    modelCode: 'ECOBIN-V1',
    expectedPortCount: 1,
    allocationStatus: 'ACTIVE',
    assetLifecycleStatus: currentDeployment ? 'IN_USE' : 'ALLOCATED',
    allocationSource: 'PLATFORM_ASSIGNMENT',
    currentDeploymentCode: currentDeployment,
    currentOrganizationCode: currentOrganization,
    credentialRotationRequired: false,
    allocationVersion,
    assetVersion: 5,
    allocatedAt: '2026-08-01T00:00:00.123Z',
    endedAt: null,
    endMode: null,
    endReason: null,
  });
  const deployment = (organizationCode: string, deploymentCode: string) => ({
    deploymentCode,
    tenantCode,
    organizationCode,
    asset: {
      hardwareSn,
      modelCode: 'ECOBIN-V1',
      expectedPortCount: 1,
      lifecycleStatus: 'IN_USE',
      version: 5,
    },
    lifecycleStatus: deploymentCode === oldDeploymentCode
      ? (currentDeployment === oldDeploymentCode ? 'ENABLED' : 'ENDED')
      : 'COMMISSIONING',
    businessEnabled: deploymentCode === oldDeploymentCode
      ? businessEnabled
      : false,
    portCount: 1,
    latestConfigurationVersion: null,
    appliedConfigurationVersion: null,
    configurationApplicationStatus: null,
    edgeConnectionStatus: 'ONLINE',
    oneNetConnectionStatus: 'ONLINE',
    oneNetStatusObservedAt: '2026-08-01T01:01:00.123Z',
    trustedRuntimeReceivedAt: '2026-08-01T01:01:00.123Z',
    version: deploymentCode === oldDeploymentCode ? oldDeploymentVersion : 0,
    commissionedAt: null,
    enabledAt: deploymentCode === oldDeploymentCode
      ? '2026-08-01T01:00:00.123Z'
      : null,
    createdAt: '2026-08-01T00:30:00.123Z',
    updatedAt: '2026-08-01T01:00:00.123Z',
  });
  const runtime = (organizationCode: string, deploymentCode: string) => ({
    deploymentCode,
    lifecycleStatus: deployment(organizationCode, deploymentCode).lifecycleStatus,
    businessEnabled: deployment(organizationCode, deploymentCode).businessEnabled,
    deploymentVersion: deployment(organizationCode, deploymentCode).version,
    configuration: {
      latestPublishedVersion: null,
      latestAppliedVersion: null,
      latestApplicationStatus: null,
      latestPreciselyApplied: false,
    },
    health: {
      edgeConnectionStatus: 'ONLINE',
      oneNetConnectionStatus: 'ONLINE',
      oneNetStatusObservedAt: '2026-08-01T01:01:00.123Z',
      trustedRuntimeReceivedAt: '2026-08-01T01:01:00.123Z',
      mcuLinkStatus: 'ONLINE',
      safetyStatus: 'SAFE',
      aggregateWeightHealth: 'HEALTHY',
      cameraHealth: 'HEALTHY',
      localStorageHealth: 'HEALTHY',
      clockSyncHealth: 'HEALTHY',
      edgeSoftwareVersion: '1.0.0',
      mcuFirmwareVersion: 'fixed-frame',
      uartState: 'ONLINE',
      uartProtocolMajor: 1,
      uartProtocolMinor: 0,
      capabilityBitmapHex: '01',
      lastHeartbeatAt: '2026-08-01T01:01:00.123Z',
      lastDeviceEventAt: '2026-08-01T01:01:00.123Z',
      runtimeVersion: 2,
    },
    occupied: false,
    deliveryAllowed: businessEnabled,
    cleaningAllowed: businessEnabled,
    deliveryBlockers: businessEnabled ? [] : ['BUSINESS_SWITCH_DISABLED'],
    cleaningBlockers: businessEnabled ? [] : ['BUSINESS_SWITCH_DISABLED'],
  });

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, { token: 'csrf-transfer', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (
      method === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      method === 'GET'
      && url.pathname === '/api/v1/web/device-asset-allocations'
    ) {
      await json(route, { items: [allocation()], page: 1, pageSize: 20, total: 1 });
      return;
    }
    const deploymentMatch = url.pathname.match(
      /^\/api\/v1\/web\/organizations\/([^/]+)\/device-deployments(?:\/([^/]+))?(.*)$/,
    );
    if (deploymentMatch) {
      const organizationCode = deploymentMatch[1];
      const requestedDeployment = deploymentMatch[2];
      const suffix = deploymentMatch[3];
      if (!requestedDeployment && method === 'GET') {
        const items = organizationCode === orgA
          ? [deployment(orgA, oldDeploymentCode)]
          : newDeploymentCreated
            ? [deployment(orgB, newDeploymentCode)]
            : [];
        await json(route, { items, page: 1, pageSize: 20, total: items.length });
        return;
      }
      if (!requestedDeployment && organizationCode === orgB && method === 'POST') {
        mutations.push({
          path: url.pathname,
          body: request.postDataJSON(),
          key: request.headers()['idempotency-key'],
        });
        newDeploymentCreated = true;
        currentOrganization = orgB;
        currentDeployment = newDeploymentCode;
        allocationVersion += 1;
        await json(route, deployment(orgB, newDeploymentCode), 201);
        return;
      }
      if (!suffix && method === 'GET' && requestedDeployment) {
        await json(route, deployment(organizationCode, requestedDeployment));
        return;
      }
      if (suffix === '/runtime' && method === 'GET') {
        await json(route, runtime(organizationCode, requestedDeployment));
        return;
      }
      if (suffix === '/ports' && method === 'GET') {
        await json(route, [{
          portNo: 1,
          displayName: null,
          enabled: null,
          unitPriceYuanPerKg: null,
          fullnessMode: null,
          configurationVersion: null,
        }]);
        return;
      }
      if (suffix === '/configuration-versions' && method === 'GET') {
        await json(route, { items: [], nextBeforeVersionNo: null });
        return;
      }
      if (suffix === '/business-switch/disablements' && method === 'POST') {
        mutations.push({
          path: url.pathname,
          body: request.postDataJSON(),
          key: request.headers()['idempotency-key'],
        });
        businessEnabled = false;
        oldDeploymentVersion += 1;
        await json(route, deployment(orgA, oldDeploymentCode));
        return;
      }
      if (suffix === '/returns-to-tenant-pool' && method === 'POST') {
        mutations.push({
          path: url.pathname,
          body: request.postDataJSON(),
          key: request.headers()['idempotency-key'],
        });
        currentOrganization = null;
        currentDeployment = null;
        allocationVersion += 1;
        await json(route, allocation());
        return;
      }
    }
    await route.fulfill(problem(404));
  });

  await page.goto(`/devices?organization=${orgA}`);
  await page.getByText(oldDeploymentCode, { exact: true }).click();
  const drawer = page.locator('.ant-drawer').filter({ hasText: '设备接入' });
  await expect(drawer.getByRole('button', { name: '关闭经营' })).toBeVisible();
  await drawer.getByRole('button', { name: '关闭经营' }).click();
  const disableDialog = page.getByRole('dialog', { name: '关闭经营' });
  await disableDialog.getByRole('button', { name: '确认提交' }).click();
  await expect(drawer.getByRole('button', { name: '退回租户设备池' })).toBeEnabled();

  await drawer.getByRole('button', { name: '退回租户设备池' }).click();
  const returnDialog = page.getByRole('dialog', { name: '退回租户设备池' });
  await returnDialog.getByLabel('原因').fill('调拨到城北站点');
  await returnDialog.getByRole('button', { name: '确认提交' }).click();

  await expect(page.getByRole('tab', { name: '租户设备池' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('租户池中，尚未部署', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '部署到机构' }).click();
  const deployDialog = page.getByRole('dialog', { name: '从租户设备池部署到机构' });
  await deployDialog.locator('.ant-select-selector').click();
  await page.locator('.ant-select-dropdown:visible')
    .getByText('城北站点 · org-b', { exact: true })
    .click();
  await deployDialog.getByRole('button', { name: '创建新部署' }).click();
  await expect(page.getByText(newDeploymentCode, { exact: true }).first()).toBeVisible();

  expect(mutations).toHaveLength(3);
  expect(mutations.every((mutation) => mutation.key)).toBe(true);
  expect(mutations[0].body).toMatchObject({ expectedVersion: 3 });
  expect(mutations[1].body).toEqual({
    expectedDeploymentVersion: 4,
    expectedAllocationVersion: 1,
    reason: '调拨到城北站点',
  });
  expect(mutations[2].body).toEqual({
    allocationUid,
    expectedAllocationVersion: 2,
  });
});

test('platform accepts first deployment while a same-tenant redeployment is automatically ready', async ({
  page,
}) => {
  const tenantCode = 'tenant-acceptance';
  const organizationCode = 'org-acceptance';
  const manualCode = 'dp-first-install';
  const automaticCode = 'dp-same-tenant-transfer';
  const acceptanceUid = '62000000-0000-4000-8000-000000000001';
  let accepted = false;
  let acceptanceAttempts = 0;
  const mutations: Array<{ body: any; key?: string }> = [];
  const session = {
    ...platformSession,
    capabilities: [
      'device.read',
      'device.manage',
      'device.configuration.manage',
    ],
  };
  const tenant = {
    tenantCode,
    enterpriseName: '验收测试租户',
    status: 'ENABLED',
    contactName: '验收联系人',
    contactPhone: null,
    contactAddress: null,
    version: 1,
    principalAccount: null,
    createdAt: '2026-08-01T00:00:00.123Z',
    updatedAt: '2026-08-01T00:00:00.123Z',
  };
  const organization = {
    organizationCode,
    organizationName: '验收机构',
    contactPhone: null,
    contactAddress: null,
    status: 'ENABLED',
    version: 1,
    createdAt: '2026-08-01T00:00:00.123Z',
    updatedAt: '2026-08-01T00:00:00.123Z',
  };
  const deployment = (deploymentCode: string) => ({
    deploymentCode,
    tenantCode,
    organizationCode,
    asset: {
      hardwareSn: deploymentCode === manualCode ? 'SN-FIRST-01' : 'SN-MOVE-01',
      modelCode: 'ECOBIN-V1',
      expectedPortCount: 1,
      lifecycleStatus: 'IN_USE',
      version: 4,
    },
    lifecycleStatus: deploymentCode === manualCode && accepted
      ? 'ENABLED'
      : 'COMMISSIONING',
    businessEnabled: false,
    portCount: 1,
    latestConfigurationVersion: 2,
    appliedConfigurationVersion: 2,
    configurationApplicationStatus: 'APPLIED',
    edgeConnectionStatus: 'ONLINE',
    oneNetConnectionStatus: 'ONLINE',
    oneNetStatusObservedAt: '2026-08-01T02:00:00.123Z',
    trustedRuntimeReceivedAt: '2026-08-01T02:00:00.123Z',
    version: deploymentCode === manualCode && accepted ? 5 : 4,
    commissionedAt: '2026-08-01T01:00:00.123Z',
    enabledAt: deploymentCode === manualCode && accepted
      ? '2026-08-01T02:10:00.123Z'
      : null,
    createdAt: '2026-08-01T00:30:00.123Z',
    updatedAt: '2026-08-01T02:10:00.123Z',
  });
  const runtime = (deploymentCode: string) => ({
    deploymentCode,
    lifecycleStatus: deployment(deploymentCode).lifecycleStatus,
    businessEnabled: false,
    deploymentVersion: deployment(deploymentCode).version,
    configuration: {
      latestPublishedVersion: 2,
      latestAppliedVersion: 2,
      latestApplicationStatus: 'APPLIED',
      latestPreciselyApplied: true,
    },
    health: {
      edgeConnectionStatus: 'ONLINE',
      oneNetConnectionStatus: 'ONLINE',
      oneNetStatusObservedAt: '2026-08-01T02:00:00.123Z',
      trustedRuntimeReceivedAt: '2026-08-01T02:00:00.123Z',
      mcuLinkStatus: 'ONLINE',
      safetyStatus: 'SAFE',
      aggregateWeightHealth: 'HEALTHY',
      cameraHealth: 'HEALTHY',
      localStorageHealth: 'HEALTHY',
      clockSyncHealth: 'HEALTHY',
      edgeSoftwareVersion: '1.0.0',
      mcuFirmwareVersion: 'fixed-frame',
      uartState: 'ONLINE',
      uartProtocolMajor: 1,
      uartProtocolMinor: 0,
      capabilityBitmapHex: '01',
      lastHeartbeatAt: '2026-08-01T02:00:00.123Z',
      lastDeviceEventAt: '2026-08-01T02:00:00.123Z',
      runtimeVersion: 6,
    },
    occupied: false,
    deliveryAllowed: false,
    cleaningAllowed: false,
    deliveryBlockers: ['BUSINESS_SWITCH_DISABLED'],
    cleaningBlockers: ['BUSINESS_SWITCH_DISABLED'],
  });
  const readiness = (deploymentCode: string) => ({
    deploymentCode,
    readinessMode: deploymentCode === manualCode
      ? 'PLATFORM_ACCEPTANCE_REQUIRED'
      : 'AUTOMATIC_TRANSFER_READINESS',
    ready: true,
    blockers: [],
    configuration: {
      latestVersion: 2,
      appliedVersion: 2,
      applicationStatus: 'APPLIED',
      preciselyApplied: true,
    },
    runtime: {
      edgeConnectionStatus: 'ONLINE',
      mcuLinkStatus: 'ONLINE',
      uartState: 'ONLINE',
      aggregateWeightHealth: 'HEALTHY',
      cameraHealth: 'HEALTHY',
      localStorageHealth: 'HEALTHY',
      clockSyncHealth: 'HEALTHY',
      edgeSoftwareVersion: '1.0.0',
      mcuFirmwareVersion: 'fixed-frame',
      pendingReliableEventCount: 0,
      receivedAt: '2026-08-01T02:00:00.123Z',
    },
    ports: [{
      portNo: 1,
      cleanLockPowerState: 'OFF',
      cleanSolenoidHealth: 'HEALTHY',
      weightSensorHealth: 'HEALTHY',
      infraredValue: 'CLEAR',
      infraredSensorHealth: 'HEALTHY',
      fullnessSensorKind: 'DIGITAL_INFRARED',
      smokeState: 'CLEAR',
      smokeSensorHealth: 'HEALTHY',
      safetyStatus: 'SAFE',
      runtimeFaultBitmap: 0,
      runtimeEdgeEventId: 21,
      observedAt: '2026-08-01T02:00:00.123Z',
    }],
  });
  const acceptance = () => ({
    acceptanceUid,
    deploymentCode: manualCode,
    configurationVersion: 2,
    runtimeReceivedAt: '2026-08-01T02:00:00.123Z',
    deliveryDoorObservedNormal: true,
    camerasObservedNormal: true,
    cleanDoorInstallationObservedNormal: true,
    acceptedBy: '平台管理员',
    reason: '现场逐项检查通过',
    acceptedAt: '2026-08-01T02:10:00.123Z',
  });

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.pathname.endsWith('/auth/csrf-token')) {
      await json(route, { token: 'csrf-acceptance', headerName: 'X-CSRF-TOKEN' });
      return;
    }
    if (method === 'GET' && url.pathname === '/api/v1/web/auth/sessions/current') {
      await route.fulfill(problem(401));
      return;
    }
    if (
      method === 'GET'
      && url.pathname === '/api/v1/web/platform/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (method === 'GET' && url.pathname === '/api/v1/web/platform/tenants') {
      await json(route, { items: [tenant], page: 1, pageSize: 200, total: 1 });
      return;
    }
    if (
      method === 'GET'
      && url.pathname
        === `/api/v1/web/platform/tenants/${tenantCode}/organizations`
    ) {
      await json(route, { items: [organization], page: 1, pageSize: 200, total: 1 });
      return;
    }
    if (method === 'GET' && url.pathname === '/api/v1/web/platform/device-assets') {
      await json(route, { items: [], page: 1, pageSize: 20, total: 0 });
      return;
    }
    const base = `/api/v1/web/platform/tenants/${tenantCode}`
      + `/organizations/${organizationCode}/device-deployments`;
    if (url.pathname === base && method === 'GET') {
      await json(route, {
        items: [deployment(manualCode), deployment(automaticCode)],
        page: 1,
        pageSize: 20,
        total: 2,
      });
      return;
    }
    const matchedCode = [manualCode, automaticCode].find(
      (code) => url.pathname.startsWith(`${base}/${code}`),
    );
    if (matchedCode) {
      const suffix = url.pathname.slice(`${base}/${matchedCode}`.length);
      if (suffix === '' && method === 'GET') {
        await json(route, deployment(matchedCode));
        return;
      }
      if (suffix === '/runtime' && method === 'GET') {
        await json(route, runtime(matchedCode));
        return;
      }
      if (suffix === '/ports' && method === 'GET') {
        await json(route, []);
        return;
      }
      if (suffix === '/configuration-versions' && method === 'GET') {
        await json(route, { items: [], nextBeforeVersionNo: null });
        return;
      }
      if (suffix === '/acceptance-readiness' && method === 'GET') {
        await json(route, readiness(matchedCode));
        return;
      }
      if (suffix === '/acceptances' && method === 'GET') {
        await json(route, matchedCode === manualCode && accepted
          ? [acceptance()]
          : []);
        return;
      }
      if (
        matchedCode === manualCode
        && suffix === '/acceptances'
        && method === 'POST'
      ) {
        acceptanceAttempts += 1;
        mutations.push({
          body: request.postDataJSON(),
          key: request.headers()['idempotency-key'],
        });
        if (acceptanceAttempts === 1) {
          await route.fulfill(problem(
            403,
            'SECURITY.FORBIDDEN',
            '没有平台验收权限',
          ));
          return;
        }
        accepted = true;
        await json(route, acceptance(), 201);
        return;
      }
    }
    await route.fulfill(problem(404, 'COMMON.NOT_FOUND', '接口不存在'));
  });

  await page.goto('/devices');
  await page.getByRole('tab', { name: '机构部署' }).click();
  await page.getByRole('row', { name: new RegExp(manualCode) })
    .getByRole('button', { name: '接入管理' })
    .click();
  let drawer = page.locator('.ant-drawer').filter({ hasText: '设备接入' });
  await drawer.getByRole('tab', { name: '验收记录' }).click();
  await expect(drawer.getByText('当前可信事实已满足技术就绪条件')).toBeVisible();
  await expect(drawer.getByRole('button', { name: '提交平台验收' })).toBeEnabled();
  await expect(drawer.getByRole('button', { name: '开启经营' })).toHaveCount(0);
  await drawer.getByRole('button', { name: '提交平台验收' }).click();
  const acceptanceDialog = page.getByRole('dialog', {
    name: '提交平台静态硬件验收',
  });
  await acceptanceDialog.getByRole('button', { name: '记录验收' }).click();
  await expect(acceptanceDialog.getByText('该项尚未确认')).toHaveCount(3);
  for (const name of [
    '我已现场确认投递门安装与动作正常',
    '我已现场确认内外摄像头安装正常',
    '我已现场确认清运门安装正常',
  ]) {
    await acceptanceDialog.getByRole('checkbox', { name }).check();
  }
  await acceptanceDialog.getByLabel('验收说明').fill('现场逐项检查通过');
  await acceptanceDialog.getByRole('button', { name: '记录验收' }).click();
  await expect(acceptanceDialog.getByText(/没有平台验收权限/)).toBeVisible();
  await acceptanceDialog.getByRole('button', { name: '记录验收' }).click();
  await drawer.getByRole('tab', { name: '验收记录' }).click();
  await expect(drawer.getByText('平台管理员', { exact: true })).toBeVisible();

  await drawer.getByRole('button', { name: '关闭' }).click();
  await page.getByRole('row', { name: new RegExp(automaticCode) })
    .getByRole('button', { name: '接入管理' })
    .click();
  drawer = page.locator('.ant-drawer').filter({ hasText: '设备接入' });
  await drawer.getByRole('tab', { name: '验收记录' }).click();
  await expect(drawer.getByText('同租户内部调拨不重复平台验收')).toBeVisible();
  await expect(drawer.getByRole('button', { name: '提交平台验收' })).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: '开启经营' })).toHaveCount(0);

  const acceptancePayload = {
    expectedDeploymentVersion: 4,
    expectedConfigurationVersion: 2,
    deliveryDoorObservedNormal: true,
    camerasObservedNormal: true,
    cleanDoorInstallationObservedNormal: true,
    reason: '现场逐项检查通过',
  };
  expect(mutations).toHaveLength(2);
  expect(mutations[0].body).toEqual(acceptancePayload);
  expect(mutations[1].body).toEqual(acceptancePayload);
  expect(mutations[0].key).toBeTruthy();
  expect(mutations[1].key).toBeTruthy();
  expect(mutations[0].key).not.toBe(mutations[1].key);
});

test('late delivery detail responses cannot replace or review the selected order', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: [
      'organization.read',
      'delivery.read',
      'review.execute',
    ],
  };
  const orderA = 'DO-20260730-RACE-A';
  const orderB = 'DO-20260730-RACE-B';
  let orderARequested = false;
  let orderAFulfilled = false;
  let releaseOrderA: (() => void) | undefined;
  const orderAGate = new Promise<void>((resolve) => {
    releaseOrderA = resolve;
  });
  let reviewedPath: string | undefined;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, {
        token: 'delivery-race-csrf-e2e',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [{
          organizationCode: 'org-delivery',
          organizationName: '投递运营中心',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-07-01T00:00:00.123Z',
          updatedAt: '2026-07-01T00:00:00.123Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-delivery/delivery-orders'
    ) {
      await json(route, {
        items: [
          { ...deliveryItem('PENDING', 0), deliveryOrderNo: orderA },
          { ...deliveryItem('PENDING', 0), deliveryOrderNo: orderB },
        ],
        asOf: '2026-07-30T03:10:00.123Z',
        nextCursor: null,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${orderA}`
    ) {
      orderARequested = true;
      await orderAGate;
      await json(route, {
        ...deliveryDetail('PENDING', 0),
        deliveryOrderNo: orderA,
      });
      orderAFulfilled = true;
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${orderB}`
    ) {
      await json(route, {
        ...deliveryDetail('PENDING', 0),
        deliveryOrderNo: orderB,
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname.endsWith('/review-previews')
    ) {
      await json(route, {
        deliveryOrderNo: orderB,
        revisionType: 'INITIAL_REVIEW',
        expectedRevisionNo: 0,
        decision: 'ORIGINAL_APPROVED',
        finalWeightKg: '1.25',
        finalAmountYuan: '1.00',
        walletDeltaYuan: '1.00',
        walletEffect: 'APPLIED',
        previewedAt: '2026-07-30T02:59:00.123Z',
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname.endsWith('/reviews')
    ) {
      reviewedPath = url.pathname;
      await json(route, {
        deliveryOrderNo: orderB,
        revisionUid: '40000000-0000-4000-8000-000000000049',
        revisionNo: 1,
        reviewStatus: 'APPROVED',
        decision: 'ORIGINAL_APPROVED',
        finalWeightKg: '1.25',
        finalAmountYuan: '1.00',
        walletDeltaYuan: '1.00',
        walletEffect: 'APPLIED',
        reviewedAt: '2026-07-30T03:00:00.123Z',
      }, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/deliveries?organization=org-delivery');
  await page.getByText(orderA, { exact: true }).click();
  await expect.poll(() => orderARequested).toBe(true);
  await page.locator('.ant-drawer-close').click();

  await page.getByText(orderB, { exact: true }).click();
  const drawer = page.getByRole('dialog', { name: /投递订单详情/ });
  await expect(drawer.getByText(orderB, { exact: true })).toBeVisible();

  releaseOrderA?.();
  await expect.poll(() => orderAFulfilled).toBe(true);
  await expect(drawer.getByText(orderB, { exact: true })).toBeVisible();
  await expect(drawer.getByText(orderA, { exact: true })).toHaveCount(0);

  await drawer.getByRole('button', { name: '审核', exact: true }).click();
  const lateOrderConfirm = page
    .getByRole('dialog')
    .getByRole('button', { name: '确认审核' });
  await expect(lateOrderConfirm).toBeEnabled();
  await lateOrderConfirm.click();
  await expect.poll(() => reviewedPath).toBe(
    `/api/v1/web/organizations/org-delivery/delivery-orders/${orderB}/reviews`,
  );
});

test('delivery list applies deep-link filters and reviews from the evidence drawer', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: [
      'organization.read',
      'delivery.read',
      'review.execute',
      'delivery.correct',
    ],
  };
  let reviewed = false;
  let listQuery: URLSearchParams | undefined;
  let reviewRequest:
    | { headers: Record<string, string>; body: unknown }
    | undefined;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, {
        token: 'delivery-csrf-e2e',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [{
          organizationCode: 'org-delivery',
          organizationName: '投递运营中心',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-07-01T00:00:00.123Z',
          updatedAt: '2026-07-01T00:00:00.123Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-delivery/delivery-orders'
    ) {
      listQuery = new URLSearchParams(url.searchParams);
      await json(route, {
        items: [deliveryItem(reviewed ? 'APPROVED' : 'PENDING', reviewed ? 1 : 0)],
        asOf: '2026-07-30T03:10:00.123Z',
        nextCursor: null,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}`
    ) {
      await json(
        route,
        deliveryDetail(reviewed ? 'APPROVED' : 'PENDING', reviewed ? 1 : 0),
      );
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}/review-previews`
    ) {
      await json(route, {
        deliveryOrderNo,
        revisionType: 'INITIAL_REVIEW',
        expectedRevisionNo: 0,
        decision: 'ORIGINAL_APPROVED',
        finalWeightKg: '1.25',
        finalAmountYuan: '1.00',
        walletDeltaYuan: '1.00',
        walletEffect: 'APPLIED',
        previewedAt: '2026-07-30T02:59:00.123Z',
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}/reviews`
    ) {
      reviewRequest = {
        headers: request.headers(),
        body: request.postDataJSON(),
      };
      reviewed = true;
      await json(route, {
        deliveryOrderNo,
        revisionUid: '40000000-0000-4000-8000-000000000044',
        revisionNo: 1,
        reviewStatus: 'APPROVED',
        decision: 'ORIGINAL_APPROVED',
        finalWeightKg: '1.25',
        finalAmountYuan: '1.00',
        walletDeltaYuan: '1.00',
        walletEffect: 'APPLIED',
        reviewedAt: '2026-07-30T03:00:00.123Z',
      }, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto(
    `/deliveries?organization=org-delivery&organizationUserUid=${deliveryUserUid}`,
  );
  await expect(page.getByText(deliveryOrderNo, { exact: true })).toBeVisible();
  await expect.poll(() => listQuery?.get('organizationUserUid')).toBe(
    deliveryUserUid,
  );
  await page.getByText(deliveryOrderNo, { exact: true }).click();
  await expect(page.getByText('设备原始事实', { exact: true })).toBeVisible();
  await expect(page.getByText('照片证据', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '审核', exact: true }).click();
  const reviewDialog = page.getByRole('dialog');
  await expect(reviewDialog.getByText('审核会形成第一条认定版本')).toBeVisible();
  const confirmReview = reviewDialog.getByRole('button', { name: '确认审核' });
  await expect(confirmReview).toBeEnabled();
  await confirmReview.click();

  await expect.poll(() => reviewRequest?.body).toEqual({
    expectedRevisionNo: 0,
    decision: 'ORIGINAL_APPROVED',
    finalWeightKg: null,
    reason: null,
  });
  expect(reviewRequest?.headers['idempotency-key']).toMatch(
    /^[0-9a-f-]{36}$/i,
  );
  expect(reviewRequest?.headers['x-csrf-token']).toBe('delivery-csrf-e2e');
  await expect(
    page.getByText(/审核已提交：最终金额 ¥ 1.00/),
  ).toBeVisible();
  await expect(
    page.getByText('已通过', { exact: true }).first(),
  ).toBeVisible();
});

test('delivery review waits for the latest silent preview before committing', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: [
      'organization.read',
      'delivery.read',
      'review.execute',
    ],
  };
  const previewBodies: Array<{
    expectedRevisionNo: number;
    decision: 'ORIGINAL_APPROVED' | 'MODIFIED_APPROVED';
    finalWeightKg: string | null;
  }> = [];
  const previewHeaders: Array<Record<string, string>> = [];
  let releaseOneKgPreview: (() => void) | undefined;
  const oneKgPreviewGate = new Promise<void>((resolve) => {
    releaseOneKgPreview = resolve;
  });
  let reviewBody: unknown;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, {
        token: 'latest-preview-csrf-e2e',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (request.method() === 'GET'
        && url.pathname === '/api/v1/web/auth/sessions/current') {
      await json(route, session);
      return;
    }
    if (request.method() === 'GET'
        && url.pathname === '/api/v1/web/organizations') {
      await json(route, {
        items: [{
          organizationCode: 'org-delivery',
          organizationName: '投递运营中心',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-07-01T00:00:00.123Z',
          updatedAt: '2026-07-01T00:00:00.123Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (request.method() === 'GET'
        && url.pathname
          === '/api/v1/web/organizations/org-delivery/delivery-orders') {
      await json(route, {
        items: [deliveryItem('PENDING', 0)],
        asOf: '2026-07-30T03:10:00.123Z',
        nextCursor: null,
      });
      return;
    }
    if (request.method() === 'GET'
        && url.pathname
          === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}`) {
      await json(route, deliveryDetail('PENDING', 0));
      return;
    }
    if (request.method() === 'POST'
        && url.pathname.endsWith('/review-previews')) {
      const body = request.postDataJSON() as {
        expectedRevisionNo: number;
        decision: 'ORIGINAL_APPROVED' | 'MODIFIED_APPROVED';
        finalWeightKg: string | null;
      };
      previewBodies.push(body);
      previewHeaders.push(request.headers());
      if (body.finalWeightKg === '1.00') {
        await oneKgPreviewGate;
      }
      const weight = body.finalWeightKg ?? '1.25';
      const amounts: Record<string, string> = {
        '1.00': '0.80',
        '1.25': '1.00',
        '2.00': '1.60',
      };
      const amount = amounts[weight] ?? '0.00';
      await json(route, {
        deliveryOrderNo,
        revisionType: 'INITIAL_REVIEW',
        expectedRevisionNo: 0,
        decision: body.decision,
        finalWeightKg: weight,
        finalAmountYuan: amount,
        walletDeltaYuan: amount,
        walletEffect: amount === '0.00' ? 'NO_CHANGE' : 'APPLIED',
        previewedAt: '2026-07-30T02:59:00.123Z',
      });
      return;
    }
    if (request.method() === 'POST'
        && url.pathname.endsWith('/reviews')) {
      reviewBody = request.postDataJSON();
      await json(route, {
        deliveryOrderNo,
        revisionUid: '40000000-0000-4000-8000-000000000059',
        revisionNo: 1,
        reviewStatus: 'APPROVED',
        decision: 'MODIFIED_APPROVED',
        finalWeightKg: '2.00',
        finalAmountYuan: '1.60',
        walletDeltaYuan: '1.60',
        walletEffect: 'APPLIED',
        reviewedAt: '2026-07-30T03:00:00.123Z',
      }, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/deliveries?organization=org-delivery');
  await page.getByText(deliveryOrderNo, { exact: true }).click();
  await page.getByRole('button', { name: '审核', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: '确认审核' }))
    .toBeEnabled();

  await dialog.getByText('修改重量后通过', { exact: true }).click();
  await dialog.getByLabel('最终认定重量（千克）').fill('1.00');
  await expect.poll(() => previewBodies.some(
    (body) => body.finalWeightKg === '1.00',
  )).toBe(true);
  await expect(dialog.getByRole('button', { name: '确认审核' }))
    .toBeDisabled();

  await dialog.getByLabel('最终认定重量（千克）').fill('2.00');
  await expect.poll(() => previewBodies.some(
    (body) => body.finalWeightKg === '2.00',
  )).toBe(true);
  await expect(dialog.getByText(
    '客户端换算与服务端预览一致，可以确认',
    { exact: true },
  )).toBeVisible();
  await expect(dialog.getByText(/最终金额 ¥ 1\.60/)).toBeVisible();
  await expect(dialog.getByRole('button', { name: '确认审核' }))
    .toBeEnabled();

  releaseOneKgPreview?.();
  await page.waitForTimeout(100);
  await expect(dialog.getByText(/最终金额 ¥ 1\.60/)).toBeVisible();
  await expect(dialog.getByText(/最终金额 ¥ 0\.80/)).toHaveCount(0);

  const previewCountBeforeReason = previewBodies.length;
  await dialog.getByLabel('说明').fill('只修改说明不应使预览失效');
  await page.waitForTimeout(400);
  expect(previewBodies).toHaveLength(previewCountBeforeReason);
  await expect(dialog.getByRole('button', { name: '确认审核' }))
    .toBeEnabled();

  const latestPreviewHeaders = previewHeaders.at(-1);
  expect(latestPreviewHeaders?.['idempotency-key']).toBeUndefined();
  expect(latestPreviewHeaders?.['x-csrf-token'])
    .toBe('latest-preview-csrf-e2e');
  expect(latestPreviewHeaders?.['cache-control']).toBe('no-store');

  await dialog.getByRole('button', { name: '确认审核' }).click();
  await expect.poll(() => reviewBody).toEqual({
    expectedRevisionNo: 0,
    decision: 'MODIFIED_APPROVED',
    finalWeightKg: '2.00',
    reason: '只修改说明不应使预览失效',
  });
});

test('delivery preview revision conflict closes the modal and reloads evidence', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: [
      'organization.read',
      'delivery.read',
      'review.execute',
    ],
  };
  let detailReads = 0;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, {
        token: 'preview-conflict-csrf-e2e',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (request.method() === 'GET'
        && url.pathname === '/api/v1/web/auth/sessions/current') {
      await json(route, session);
      return;
    }
    if (request.method() === 'GET'
        && url.pathname === '/api/v1/web/organizations') {
      await json(route, {
        items: [{
          organizationCode: 'org-delivery',
          organizationName: '投递运营中心',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-07-01T00:00:00.123Z',
          updatedAt: '2026-07-01T00:00:00.123Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (request.method() === 'GET'
        && url.pathname
          === '/api/v1/web/organizations/org-delivery/delivery-orders') {
      await json(route, {
        items: [deliveryItem(detailReads > 1 ? 'APPROVED' : 'PENDING',
          detailReads > 1 ? 1 : 0)],
        asOf: '2026-07-30T03:10:00.123Z',
        nextCursor: null,
      });
      return;
    }
    if (request.method() === 'GET'
        && url.pathname
          === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}`) {
      detailReads += 1;
      await json(route, deliveryDetail(
        detailReads > 1 ? 'APPROVED' : 'PENDING',
        detailReads > 1 ? 1 : 0,
      ));
      return;
    }
    if (request.method() === 'POST'
        && url.pathname.endsWith('/review-previews')) {
      await route.fulfill(problem(
        409,
        'DELIVERY.REVISION_VERSION_CONFLICT',
        '投递订单已被其他审核操作更新，请刷新后重试',
      ));
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/deliveries?organization=org-delivery');
  await page.getByText(deliveryOrderNo, { exact: true }).click();
  await page.getByRole('button', { name: '审核', exact: true }).click();
  await expect(page.getByText(
    /订单版本已经变化，已关闭审核窗口并载入最新记录/,
  )).toBeVisible();
  await expect.poll(() => detailReads).toBeGreaterThan(1);
  await expect(page.getByText(
    `审核投递订单 · ${deliveryOrderNo}`,
    { exact: true },
  )).toHaveCount(0);
});

test('approved delivery can append a correction with the observed revision', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: [
      'organization.read',
      'delivery.read',
      'delivery.correct',
    ],
  };
  let corrected = false;
  let correctionBody: unknown;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/web/auth/csrf-token') {
      await json(route, {
        token: 'correction-csrf-e2e',
        headerName: 'X-CSRF-TOKEN',
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [{
          organizationCode: 'org-delivery',
          organizationName: '投递运营中心',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-07-01T00:00:00.123Z',
          updatedAt: '2026-07-01T00:00:00.123Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-delivery/delivery-orders'
    ) {
      await json(route, {
        items: [{
          ...deliveryItem('APPROVED', corrected ? 2 : 1),
          finalWeightKg: corrected ? '1.50' : '1.25',
          finalAmountYuan: corrected ? '1.20' : '1.00',
        }],
        asOf: '2026-07-30T03:10:00.123Z',
        nextCursor: null,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}`
    ) {
      const base = deliveryDetail('APPROVED', corrected ? 2 : 1);
      await json(route, corrected
        ? {
            ...base,
            review: {
              ...base.review,
              currentRevisionNo: 2,
              finalWeightKg: '1.50',
              finalAmountYuan: '1.20',
            },
          }
        : base);
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}/review-previews`
    ) {
      const previewBody = request.postDataJSON() as {
        expectedRevisionNo: number;
        decision: 'ORIGINAL_APPROVED' | 'MODIFIED_APPROVED';
        finalWeightKg: string | null;
      };
      const weight = previewBody.finalWeightKg ?? '1.25';
      const amountByWeight: Record<string, string> = {
        '1.25': '1.00',
        '1.50': '1.20',
      };
      const amount = amountByWeight[weight] ?? '1.00';
      const deltaByAmount: Record<string, string> = {
        '1.00': '0.00',
        '1.20': '0.20',
      };
      await json(route, {
        deliveryOrderNo,
        revisionType: 'CORRECTION',
        expectedRevisionNo: previewBody.expectedRevisionNo,
        decision: previewBody.decision,
        finalWeightKg: weight,
        finalAmountYuan: amount,
        walletDeltaYuan: deltaByAmount[amount] ?? '0.00',
        walletEffect: amount === '1.00' ? 'NO_CHANGE' : 'APPLIED',
        previewedAt: '2026-07-30T03:19:00.123Z',
      });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname
        === `/api/v1/web/organizations/org-delivery/delivery-orders/${deliveryOrderNo}/corrections`
    ) {
      correctionBody = request.postDataJSON();
      corrected = true;
      await json(route, {
        deliveryOrderNo,
        revisionUid: '40000000-0000-4000-8000-000000000046',
        revisionNo: 2,
        reviewStatus: 'APPROVED',
        decision: 'MODIFIED_APPROVED',
        finalWeightKg: '1.50',
        finalAmountYuan: '1.20',
        walletDeltaYuan: '0.20',
        walletEffect: 'APPLIED',
        reviewedAt: '2026-07-30T03:20:00.123Z',
      }, 201);
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/deliveries?organization=org-delivery');
  await page.getByText(deliveryOrderNo, { exact: true }).click();
  await page.getByRole('button', { name: '纠正', exact: true }).click();
  const correctionDialog = page.getByRole('dialog');
  await correctionDialog
    .getByLabel('最终认定重量（千克）')
    .fill('1.50');
  await correctionDialog
    .getByLabel('说明')
    .fill('现场复核后重新认定');
  const confirmCorrection = correctionDialog.getByRole('button', {
    name: '确认纠正',
  });
  await expect(confirmCorrection).toBeEnabled();
  await confirmCorrection.click();

  await expect.poll(() => correctionBody).toEqual({
    expectedRevisionNo: 1,
    decision: 'MODIFIED_APPROVED',
    finalWeightKg: '1.50',
    reason: '现场复核后重新认定',
  });
  await expect(
    page.getByText(/纠正已提交：最终金额 ¥ 1.20/),
  ).toBeVisible();
  await expect(
    page
      .getByRole('dialog', { name: /投递订单详情/ })
      .getByText('1.50 千克', { exact: true }),
  ).toBeVisible();
});

test('delivery pagination forwards only the opaque server cursor', async ({
  page,
}) => {
  const session = {
    ...tenantSession,
    capabilities: ['organization.read', 'delivery.read'],
  };
  const observedCursors: Array<string | null> = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/organizations'
    ) {
      await json(route, {
        items: [{
          organizationCode: 'org-delivery',
          organizationName: '投递运营中心',
          status: 'ENABLED',
          version: 1,
          createdAt: '2026-07-01T00:00:00.123Z',
          updatedAt: '2026-07-01T00:00:00.123Z',
        }],
        page: 1,
        pageSize: 200,
        total: 1,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-delivery/delivery-orders'
    ) {
      const cursor = url.searchParams.get('cursor');
      observedCursors.push(cursor);
      await json(route, cursor
        ? {
            items: [{
              ...deliveryItem('APPROVED', 1),
              deliveryOrderNo: 'DO-20260730-000002',
            }],
            asOf: '2026-07-30T03:10:00.123Z',
            nextCursor: null,
          }
        : {
            items: [deliveryItem('PENDING', 0)],
            asOf: '2026-07-30T03:10:00.123Z',
            nextCursor: 'opaque-page-two',
          });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/deliveries?organization=org-delivery');
  await expect(page.getByText(deliveryOrderNo, { exact: true })).toBeVisible();
  await page.locator('.ant-pagination-next button').click();
  await expect(
    page.getByText('DO-20260730-000002', { exact: true }),
  ).toBeVisible();
  await expect.poll(() => observedCursors).toEqual([
    null,
    'opaque-page-two',
  ]);
});

test('wallet ledger preserves its opaque cursor and deep-links a delivery detail', async ({
  page,
}) => {
  const organizationUserUid =
    '30000000-0000-4000-8000-000000000021';
  const deepLinkedOrderNo = 'DO-20260731-000002';
  const session = {
    ...tenantSession,
    capabilities: ['wallet.read', 'delivery.read'],
    organizations: [{
      organizationCode: 'org-wallet',
      organizationName: '钱包运营中心',
    }],
  };
  const observedWalletQueries: Array<{
    cursor: string | null;
    organizationUserUid: string | null;
  }> = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === 'GET'
      && url.pathname === '/api/v1/web/auth/sessions/current'
    ) {
      await json(route, session);
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-wallet/wallet-entries'
    ) {
      const cursor = url.searchParams.get('cursor');
      observedWalletQueries.push({
        cursor,
        organizationUserUid:
          url.searchParams.get('organizationUserUid'),
      });
      await json(route, cursor
        ? {
            items: [{
              entryUid: '30000000-0000-4000-8000-000000000023',
              organizationUserUid,
              entrySequenceNo: 13,
              entryType: 'DELIVERY_CORRECTION',
              availableDeltaYuan: '-0.20',
              processingDeltaYuan: '0.00',
              availableBalanceAfterYuan: '0.80',
              withdrawalProcessingAfterYuan: '0.00',
              sourceType: 'DELIVERY_ORDER',
              sourceNo: deepLinkedOrderNo,
              occurredAt: '2026-07-31T02:00:00.123Z',
            }],
            asOf: '2026-07-31T02:10:00.123Z',
            nextCursor: null,
          }
        : {
            items: [{
              entryUid: '30000000-0000-4000-8000-000000000022',
              organizationUserUid,
              entrySequenceNo: 12,
              entryType: 'DELIVERY_INITIAL_REVIEW',
              availableDeltaYuan: '1.00',
              processingDeltaYuan: '0.00',
              availableBalanceAfterYuan: '1.00',
              withdrawalProcessingAfterYuan: '0.00',
              sourceType: 'DELIVERY_ORDER',
              sourceNo: deliveryOrderNo,
              occurredAt: '2026-07-31T01:00:00.123Z',
            }],
            asOf: '2026-07-31T02:10:00.123Z',
            nextCursor: 'opaque-wallet-page-two',
          });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === '/api/v1/web/organizations/org-wallet/delivery-orders'
    ) {
      await json(route, {
        items: [{
          ...deliveryItem('APPROVED', 2),
          deliveryOrderNo: deepLinkedOrderNo,
        }],
        asOf: '2026-07-31T02:10:00.123Z',
        nextCursor: null,
      });
      return;
    }
    if (
      request.method() === 'GET'
      && url.pathname
        === `/api/v1/web/organizations/org-wallet/delivery-orders/${deepLinkedOrderNo}`
    ) {
      await json(route, {
        ...deliveryDetail('APPROVED', 2),
        deliveryOrderNo: deepLinkedOrderNo,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto(
    `/wallet-entries?organization=org-wallet&organizationUserUid=${organizationUserUid}`,
  );
  await expect(
    page.getByText(deliveryOrderNo, { exact: true }),
  ).toBeVisible();
  await expect.poll(() => observedWalletQueries[0]).toEqual({
    cursor: null,
    organizationUserUid,
  });

  await page.locator('.ant-pagination-next button').click();
  await expect(
    page.getByText(deepLinkedOrderNo, { exact: true }),
  ).toBeVisible();
  await expect.poll(() => observedWalletQueries.at(-1)?.cursor).toBe(
    'opaque-wallet-page-two',
  );

  await page.getByText(deepLinkedOrderNo, { exact: true }).click();
  await expect(page).toHaveURL(
    new RegExp(
      `/deliveries\\?organization=org-wallet&deliveryOrderNo=${deepLinkedOrderNo}`,
    ),
  );
  const drawer = page.getByRole('dialog', { name: /投递订单详情/ });
  await expect(
    drawer.getByText(deepLinkedOrderNo, { exact: true }),
  ).toBeVisible();
});
