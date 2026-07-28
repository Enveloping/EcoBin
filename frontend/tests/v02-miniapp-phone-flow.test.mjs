import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('device registration source survives a login retry', () => {
  const loginSource = source(
    '../miniprogram/miniprogram/pages/login/login.ts',
  );

  assert.match(
    loginSource,
    /this\.registrationDeploymentCode\s*=\s*registrationDeploymentCode\(options\)/,
  );
  assert.match(
    loginSource,
    /deploymentCode:\s*this\.registrationDeploymentCode/,
  );
  assert.match(loginSource, /onRetry\(\)\s*\{\s*this\.doLogin\(\)/);
});

test('an unbound user gets an optional native WeChat phone-number button', () => {
  const authSource = source(
    '../miniprogram/miniprogram/utils/auth.ts',
  );
  const homeMarkup = source(
    '../miniprogram/miniprogram/pages/home/home.wxml',
  );
  const cleanMarkup = source(
    '../miniprogram/miniprogram/pages/clean/clean.wxml',
  );
  const loginSource = source(
    '../miniprogram/miniprogram/pages/login/login.ts',
  );
  const appConfig = source(
    '../miniprogram/miniprogram/app.json',
  );

  assert.match(loginSource, /routeToEntry\(session\)/);
  assert.doesNotMatch(authSource, /routeAfterLogin/);
  assert.match(homeMarkup, /open-type="getPhoneNumber"/);
  assert.match(homeMarkup, /bindgetphonenumber="onGetPhoneNumber"/);
  assert.match(homeMarkup, /可选：验证手机号/);
  assert.match(homeMarkup, /不影响当前浏览/);
  assert.match(cleanMarkup, /open-type="getPhoneNumber"/);
  assert.doesNotMatch(homeMarkup, /phone-sheet-mask/);
  assert.doesNotMatch(cleanMarkup, /phone-sheet-mask/);
  assert.doesNotMatch(appConfig, /phone-grant-sheet/);
});

test('cached sessions are validated and ordinary users can really log out', () => {
  const authSource = source(
    '../miniprogram/miniprogram/utils/auth.ts',
  );
  const profileSource = source(
    '../miniprogram/miniprogram/pages/profile/profile.ts',
  );

  assert.match(
    authSource,
    /await getCurrentSession\(session\.audience\)/,
  );
  assert.match(profileSource, /import \{ getSession, logout \}/);
  assert.match(profileSource, /if \(res\.confirm\) void logout\(\)/);
  assert.doesNotMatch(profileSource, /静态演示模式/);
});
