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

test('an unbound user gets a custom preflight sheet before native WeChat phone authorization', () => {
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
  const homeSource = source(
    '../miniprogram/miniprogram/pages/home/home.ts',
  );
  const cleanSource = source(
    '../miniprogram/miniprogram/pages/clean/clean.ts',
  );
  const phoneGrantSource = source(
    '../miniprogram/miniprogram/utils/phone-grant.ts',
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
  assert.match(homeMarkup, /phone-sheet-mask/);
  assert.match(cleanMarkup, /phone-sheet-mask/);
  assert.match(homeMarkup, /继续获取手机号/);
  assert.match(homeSource, /reportWechatPhoneGrantError\(event\.detail\)/);
  assert.match(cleanSource, /reportWechatPhoneGrantError\(event\.detail\)/);
  assert.match(phoneGrantSource, /title: '微信获取手机号异常'/);
  assert.match(phoneGrantSource, /console\.error\('\[phone-auth\] 微信获取手机号异常'/);
  assert.match(phoneGrantSource, /错误码：\$\{errno\}/);
  assert.match(phoneGrantSource, /错误信息：\$\{errMsg\}/);
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
