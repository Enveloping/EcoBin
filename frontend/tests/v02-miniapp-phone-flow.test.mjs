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

test('an unbound user sees an optional native WeChat phone grant sheet', () => {
  const authSource = source(
    '../miniprogram/miniprogram/utils/auth.ts',
  );
  const bindingMarkup = source(
    '../miniprogram/miniprogram/components/phone-grant-sheet/index.wxml',
  );
  const homeMarkup = source(
    '../miniprogram/miniprogram/pages/home/home.wxml',
  );
  const loginSource = source(
    '../miniprogram/miniprogram/pages/login/login.ts',
  );

  assert.match(loginSource, /routeToEntry\(session\)/);
  assert.doesNotMatch(authSource, /routeAfterLogin/);
  assert.match(bindingMarkup, /open-type="getPhoneNumber"/);
  assert.match(bindingMarkup, /微信授权手机号/);
  assert.match(bindingMarkup, /你可以先浏览小程序/);
  assert.match(bindingMarkup, /暂不授权/);
  assert.match(homeMarkup, /可选：验证手机号/);
  assert.match(homeMarkup, /不影响当前浏览/);
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
