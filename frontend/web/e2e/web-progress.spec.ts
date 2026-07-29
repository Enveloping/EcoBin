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

test('allOf capability rules hide the menu and protect direct navigation', async ({
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
  await expect(
    page.getByText('当前会话没有访问此页面所需的实时能力。'),
  ).toBeVisible();
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

  for (let attempt = 0; attempt < 2; attempt += 1) {
    await page.getByRole('button', { name: '冻结' }).click();
    await page.getByRole('button', { name: '确 定' }).click();
    await expect.poll(() => freezeAttempts).toBe(attempt + 1);
  }

  expect(idempotencyKeys[0]).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
  await expect(page.getByText('已冻结', { exact: true }).first()).toBeVisible();
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

test('user binding recovers stale CSRF and explains an unmatched organization phone', async ({
  page,
}) => {
  const session = {
    ...platformSession,
    capabilities: [
      'tenant.read',
      'organization.read',
      'staff.read',
      'staff.bind',
      'user.read',
    ],
  };
  let csrfRequestCount = 0;
  let lookupAttemptCount = 0;
  const lookupHeaders: Array<string | undefined> = [];
  let staleLookupStarted = false;
  let staleLookupCompleted = false;
  let releaseStaleLookup = () => {};
  const staleLookupGate = new Promise<void>((resolve) => {
    releaseStaleLookup = resolve;
  });

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
        items: [{
          tenantCode: 'v02-local-dev',
          enterpriseName: '本地开发租户',
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
      && url.pathname
        === '/api/v1/web/platform/tenants/v02-local-dev/organizations'
    ) {
      await json(route, {
        items: [{
          organizationCode: 'v02-local',
          organizationName: '本地机构',
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
      && url.pathname
        === '/api/v1/web/platform/tenants/v02-local-dev/staff-accounts'
    ) {
      await json(route, {
        items: [{
          staffAccountUid: '3102b64d-433d-4015-9a29-b21f309ddf8a',
          accountKind: 'STAFF',
          loginName: 'operator',
          displayName: '现场工作人员',
          status: 'ENABLED',
          version: 2,
          authVersion: 3,
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
      && url.pathname.endsWith(
        '/staff-accounts/3102b64d-433d-4015-9a29-b21f309ddf8a/miniapp-binding',
      )
    ) {
      await json(route, { currentMiniappBinding: null });
      return;
    }
    if (
      request.method() === 'POST'
      && url.pathname.endsWith('/organization-users/phone-lookups')
    ) {
      lookupAttemptCount += 1;
      lookupHeaders.push(request.headers()['x-csrf-token']);
      const { phoneNumber } = request.postDataJSON() as {
        phoneNumber: string;
      };
      if (phoneNumber === '13800138000' && lookupAttemptCount === 1) {
        await route.fulfill(
          problem(403, 'SECURITY.CSRF_INVALID', 'CSRF 校验失败'),
        );
      } else if (phoneNumber === '13800138000') {
        await route.fulfill(
          problem(404, 'COMMON.NOT_FOUND', '未找到指定资源'),
        );
      } else if (phoneNumber === '13900139000') {
        staleLookupStarted = true;
        await staleLookupGate;
        await json(route, {
          organizationUserUid: '40000000-0000-4000-8000-000000000001',
          nickname: '过期核验用户',
          maskedPhoneNumber: '139****9000',
          registeredAt: '2026-07-01T00:00:00Z',
          status: 'ACTIVE',
          currentMiniappBinding: null,
        });
        staleLookupCompleted = true;
      } else if (phoneNumber === '13700137000') {
        await json(route, {
          organizationUserUid: '40000000-0000-4000-8000-000000000002',
          nickname: '当前核验用户',
          maskedPhoneNumber: '137****7000',
          registeredAt: '2026-07-02T00:00:00Z',
          status: 'ACTIVE',
          currentMiniappBinding: null,
        });
      } else {
        await route.fulfill(problem(400));
      }
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto(
    '/user-bindings?tenant=v02-local-dev&organization=v02-local',
  );
  await page.getByRole('combobox', { name: '工作人员' }).click();
  await page.getByText('现场工作人员 · operator').click();
  await page.getByLabel('完整手机号').fill('13800138000');
  await page.getByRole('button', { name: '取得双侧快照' }).click();

  await expect.poll(() => lookupAttemptCount).toBe(2);
  expect(lookupHeaders).toEqual(['binding-csrf-1', 'binding-csrf-2']);
  await expect(
    page.getByText(
      '当前机构没有绑定此手机号的用户，请核对机构，或先让用户在该机构小程序完成手机号绑定',
    ),
  ).toBeVisible();

  await page.getByLabel('完整手机号').fill('13900139000');
  await page.getByRole('button', { name: '取得双侧快照' }).click();
  await expect.poll(() => staleLookupStarted).toBe(true);
  await page.getByLabel('完整手机号').fill('13700137000');
  await page.getByRole('button', { name: '取得双侧快照' }).click();
  await expect(page.getByText('当前核验用户', { exact: true })).toBeVisible();

  releaseStaleLookup();
  await expect.poll(() => staleLookupCompleted).toBe(true);
  await expect(
    page.getByText('过期核验用户', { exact: true }),
  ).toHaveCount(0);
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
    .filter({ hasText: '投递订单' })
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
      await json(route, [{
        permissionCode: 'device.read',
        scopeKind: 'ORGANIZATION',
        permissionName: '读取设备',
        description: '读取机构设备',
      }]);
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
    await route.fulfill(problem(404));
  });

  await page.goto('/staff');
  await expect(page.getByText('现场工作人员', { exact: true })).toBeVisible();
  await expect(page.getByText('v7 / auth 4', { exact: true })).toHaveCount(0);
  await expect(page.getByText('编辑', { exact: true })).toBeVisible();
  await expect(page.getByText('重置密码', { exact: true })).toHaveCount(0);

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
    capabilities: ['organization.read', 'device.read'],
  };
  let requestedOrganization = false;

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
          organizationCode: 'org-b',
          organizationName: '设备运营中心',
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
      && url.pathname
        === '/api/v1/web/organizations/org-b/device-deployments'
    ) {
      requestedOrganization = true;
      await json(route, {
        items: [{
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
          edgeConnectionStatus: 'ONLINE',
          version: 5,
          commissionedAt: '2026-07-02T00:00:00Z',
          enabledAt: '2026-07-03T00:00:00Z',
          createdAt: '2026-07-01T00:00:00Z',
          updatedAt: '2026-07-20T00:00:00Z',
        }],
        page: 1,
        pageSize: 20,
        total: 1,
      });
      return;
    }
    await route.fulfill(problem(404));
  });

  await page.goto('/devices?organization=org-b');
  await expect.poll(() => requestedOrganization).toBe(true);
  await expect(page).toHaveURL(/organization=org-b/);
  await expect(page.getByText('dp-hz-01', { exact: true })).toBeVisible();
  await expect(page.getByText('EC-BOX-0001', { exact: true })).toBeVisible();
  await expect(page.getByText('在线', { exact: true })).toBeVisible();
});
