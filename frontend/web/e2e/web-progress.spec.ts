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
    let loginRequest:
      | { path: string; headers: Record<string, string>; body: unknown }
      | undefined;
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.endsWith('/auth/csrf-token')) {
        await json(route, { token: 'csrf-e2e', headerName: 'X-CSRF-TOKEN' });
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
        await json(route, domain === 'tenant' ? tenantSession : platformSession);
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
    expect(loginRequest!.headers['x-csrf-token']).toBe('csrf-e2e');
    expect(loginRequest!.body).toEqual({
      loginName: 'operator',
      password: 'not-a-real-secret',
    });
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter((key) =>
          /token|session|auth/i.test(key))),
    ).toEqual([]);
  });
}

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
    capabilities: ['user.read', 'user.freeze', 'cleaner.manage'],
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
  let grantAttempts = 0;

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
    if (
      url.pathname.endsWith('/capabilities/clean-operation/grants')
      && request.method() === 'POST'
    ) {
      grantAttempts += 1;
      currentUser = {
        ...currentUser,
        version: 9,
        authVersion: 6,
      };
      await route.fulfill(
        problem(409, 'IDENTITY.VERSION_CONFLICT', '数据版本冲突'),
      );
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

  await page.getByRole('button', { name: '授予清运' }).click();
  await page.getByRole('button', { name: '确 定' }).last().click();
  await expect.poll(() => grantAttempts).toBe(1);
  await expect(
    page.getByText('数据版本已经变化，已载入最新状态；请核对后重新确认'),
  ).toBeVisible();
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
    await route.fulfill(problem(404));
  });

  await page.goto('/tenant');
  await page.getByText('重置主体密码', { exact: true }).click();
  await page.locator('input#newPassword').fill('new-password-2026');
  await page.locator('input#confirmPassword').fill('new-password-2026');
  await page.getByRole('button', { name: '确 定' }).click();

  await expect.poll(() => resetPayload).toEqual({
    newPassword: 'new-password-2026',
    expectedVersion: 11,
    expectedAuthVersion: 9,
  });
  await expect(
    page.getByText('主体密码已重置，原有主体会话已撤销'),
  ).toBeVisible();
});
