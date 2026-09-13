import { expect, test, type Page, type Route } from '@playwright/test';

const session = {
  sessionUid: '10000000-0000-4000-8000-000000000031',
  accountType: 'PLATFORM_ADMIN',
  subjectUid: '20000000-0000-4000-8000-000000000031',
  displayName: '页面验收管理员',
  tenantCode: null,
  capabilities: ['tenant.read', 'tenant.manage', 'platform-admin.manage'],
  organizations: [],
  expiresAt: '2030-01-01T00:00:00Z',
  version: 1,
  authVersion: 1,
};

async function json(route: Route, data: unknown) {
  await route.fulfill({ json: { code: 'OK', data, requestId: 'guidance-test' } });
}

async function platform(page: Page, handle: (route: Route, path: string) => Promise<boolean>) {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/web/platform/auth/sessions/current') return json(route, session);
    if (path.endsWith('/csrf-token')) return json(route, { token: 'guidance-test', headerName: 'X-CSRF-TOKEN' });
    if (await handle(route, path)) return;
    await route.fulfill({ status: 401, contentType: 'application/problem+json', body: JSON.stringify({ status: 401, code: 'SECURITY.UNAUTHENTICATED', message: '请登录', details: {} }) });
  });
}

test('maintenance registration is on demand and help works with keyboard and click', async ({ page }) => {
  const keys: object[] = [];
  let registration: Record<string, unknown> | undefined;
  await platform(page, async (route, path) => {
    if (path !== '/api/v1/web/platform/maintenance-ssh-keys') return false;
    if (route.request().method() === 'POST') {
      registration = route.request().postDataJSON();
      keys.push({ ...registration, maintenanceSshKeyUid: '30000000-0000-4000-8000-000000000031', fingerprintSha256: 'SHA256:public-test-key', status: 'ACTIVE', version: 1, createdAt: '2026-09-12T01:00:00.000Z' });
      await json(route, keys[0]);
    } else await json(route, keys);
    return true;
  });
  await page.goto('/account');
  await expect(page.getByText('修改后需重新登录')).toBeVisible();
  await expect(page.getByLabel('密钥名称')).toHaveCount(0);
  await page.getByRole('button', { name: /登记公钥/ }).click();
  const dialog = page.getByRole('dialog', { name: '登记维护公钥' });
  await expect(dialog.getByText('只提交公钥，不要上传私钥')).toBeVisible();
  const help = dialog.getByRole('button', { name: '生成维护公钥说明' });
  await help.focus();
  await expect(page.getByText('ssh-keygen -t ed25519', { exact: true })).toBeVisible();
  await help.press('Escape');
  await expect(help).toHaveAttribute('aria-expanded', 'false');
  await help.click();
  await expect(help).toHaveAttribute('aria-expanded', 'true');
  await help.press('Escape');
  await dialog.getByRole('textbox', { name: '密钥名称' }).fill('验收电脑');
  const publicKey = `ssh-ed25519 ${'A'.repeat(68)}`;
  await dialog.getByRole('textbox', { name: /Ed25519 公钥/ }).fill(publicKey);
  await dialog.getByRole('button', { name: /登记公钥/ }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText('验收电脑', { exact: true })).toBeVisible();
  expect(registration).toEqual({ label: '验收电脑', publicKey });
  await page.screenshot({ path: test.info().outputPath('account-keys.png'), fullPage: true });
});

test('creating a tenant continues to principal registration without enabling it automatically', async ({ page }) => {
  let created = false;
  let principal: Record<string, unknown> | undefined;
  let activationRequests = 0;
  const tenant = { tenantCode: 'guidance-tenant', enterpriseName: '页面验收租户', status: 'DISABLED', version: 1, principalAccount: null as object | null, createdAt: '2026-09-12T01:00:00.000Z' };
  await platform(page, async (route, path) => {
    const base = '/api/v1/web/platform/tenants';
    const method = route.request().method();
    if (path === base) {
      if (method === 'POST') { created = true; await json(route, tenant); }
      else await json(route, { items: created ? [tenant] : [], total: created ? 1 : 0, page: 1, pageSize: 20 });
      return true;
    }
    if (path === `${base}/${tenant.tenantCode}/principal-account`) {
      principal = route.request().postDataJSON();
      tenant.principalAccount = { staffAccountUid: '40000000-0000-4000-8000-000000000031', version: 1, authVersion: 1 };
      tenant.version = 2;
      await json(route, tenant.principalAccount);
      return true;
    }
    if (path === `${base}/${tenant.tenantCode}`) { await json(route, tenant); return true; }
    if (path.startsWith(`${base}/${tenant.tenantCode}/`) && method !== 'GET') {
      activationRequests += 1;
      await json(route, tenant);
      return true;
    }
    return false;
  });
  await page.goto('/tenant');
  await page.getByRole('button', { name: /创建租户/ }).click();
  const create = page.getByRole('dialog', { name: '创建租户' });
  await create.getByLabel('租户编码').fill(tenant.tenantCode);
  await create.getByLabel('企业名称').fill(tenant.enterpriseName);
  await create.getByRole('button', { name: '创建租户' }).click();
  const next = page.getByRole('dialog', { name: /建立主体账号/ });
  await expect(next).toBeVisible();
  await next.getByLabel('全局登录名').fill('guidance.principal');
  await next.getByLabel('初始密码').fill('test-password-2026');
  await next.getByLabel('展示名').fill('主体验收人员');
  await next.getByRole('button', { name: '建立主体账号' }).click();
  await expect(next).not.toBeVisible();
  await expect(page.getByText('启用租户', { exact: true })).toBeVisible();
  expect(principal?.expectedVersion).toBe(1);
  expect(activationRequests).toBe(0);
});
