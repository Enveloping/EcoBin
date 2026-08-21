import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { normalizeDeviceCode } from '../miniprogram/miniprogram/utils/device-code.ts';
import { isWechatPhoneGrantCancelled } from '../miniprogram/miniprogram/utils/wechat-phone-grant.ts';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('every registered miniapp page has its source files on disk', () => {
  const appJson = JSON.parse(source(
    '../miniprogram/miniprogram/app.json',
  ));

  for (const pagePath of appJson.pages) {
    for (const extension of ['.json', '.ts', '.wxml', '.wxss']) {
      assert.equal(
        existsSync(new URL(
          `../miniprogram/miniprogram/${pagePath}${extension}`,
          import.meta.url,
        )),
        true,
        `${pagePath}${extension} must exist`,
      );
    }
  }
});

test('guest startup has no login page and scanned identity wins login races', () => {
  const appJson = JSON.parse(source(
    '../miniprogram/miniprogram/app.json',
  ));
  const appSource = source('../miniprogram/miniprogram/app.ts');
  const authSource = source(
    '../miniprogram/miniprogram/utils/auth.ts',
  );

  assert.equal(appJson.pages[0], 'pages/home/home');
  assert.equal(appJson.pages.includes('pages/login/login'), false);
  assert.equal(
    normalizeDeviceCode(' Dv_0123456789abcdefghijklmn '),
    'Dv_0123456789abcdefghijklmn',
  );
  assert.equal(normalizeDeviceCode('not-a-device-code'), undefined);
  assert.match(
    appSource,
    /ensureLoggedIn\(\{ deviceCode: pending\.deviceCode \}\)/,
  );
  assert.match(
    authSource,
    /activeLogin\.sourceDeviceCode === sourceDeviceCode/,
  );
  assert.match(authSource, /const sequence = \+\+loginSequence/);
});

test('only an explicit WeChat phone authorization cancellation is treated as skip', () => {
  for (const errno of [1, 103, 104]) {
    assert.equal(isWechatPhoneGrantCancelled({ errno }), true);
  }
  for (const errMsg of [
    'getPhoneNumber:fail user deny',
    'getPhoneNumber:fail cancel',
    'getPhoneNumber:fail canceled',
    'getPhoneNumber:fail cancelled',
  ]) {
    assert.equal(isWechatPhoneGrantCancelled({ errMsg }), true);
  }
  for (const detail of [
    { code: 'phone-code', errMsg: 'getPhoneNumber:ok' },
    { code: 'phone-code', errno: 104, errMsg: 'getPhoneNumber:fail cancel' },
    { errno: 102, errMsg: 'getPhoneNumber:fail system error' },
    { errno: 112, errMsg: 'getPhoneNumber:fail account error' },
    { errno: 1400001, errMsg: 'getPhoneNumber:fail frequency limit' },
    { errMsg: 'anotherApi:fail cancel' },
  ]) {
    assert.equal(isWechatPhoneGrantCancelled(detail), false);
  }
});

test('phone authorization is action-driven and stops when it is declined', () => {
  const authSource = source(
    '../miniprogram/miniprogram/utils/auth.ts',
  );
  const homeMarkup = source(
    '../miniprogram/miniprogram/pages/home/home.wxml',
  );
  const cleanMarkup = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.wxml',
  );
  const profileMarkup = source(
    '../miniprogram/miniprogram/pages/profile/profile.wxml',
  );
  const homeSource = source(
    '../miniprogram/miniprogram/pages/home/home.ts',
  );
  const cleanSource = source(
    '../miniprogram/miniprogram/pages/clean-operation/clean-operation.ts',
  );
  const profileSource = source(
    '../miniprogram/miniprogram/pages/profile/profile.ts',
  );
  const doorEntrySource = source(
    '../miniprogram/miniprogram/utils/door-entry.ts',
  );
  const deliveryEntrySource = source(
    '../miniprogram/miniprogram/pages/delivery-entry/delivery-entry.ts',
  );
  const promptSource = source(
    '../miniprogram/miniprogram/utils/phone-binding-prompt.ts',
  );
  const phoneGrantSource = source(
    '../miniprogram/miniprogram/utils/phone-grant.ts',
  );
  const wechatPhoneGrantSource = source(
    '../miniprogram/miniprogram/utils/wechat-phone-grant.ts',
  );

  assert.doesNotMatch(authSource, /routeAfterLogin/);
  assert.match(authSource, /resetPhoneBindingPromptState\(\)/);
  assert.doesNotMatch(homeMarkup, /可选：验证手机号/);
  assert.doesNotMatch(homeMarkup, /phone-notice/);
  assert.match(homeMarkup, /open-type="getPhoneNumber"/);
  assert.match(profileMarkup, /open-type="getPhoneNumber"/);
  assert.match(homeMarkup, /bindgetphonenumber="onGetPhoneNumber"/);
  assert.match(profileMarkup, /bindgetphonenumber="onGetPhoneNumber"/);
  assert.match(homeMarkup, /暂不授权仍可浏览页面，但本次操作不会继续/);
  assert.match(cleanMarkup, /open-type="getPhoneNumber"/);
  assert.match(homeMarkup, /phone-sheet-mask/);
  assert.match(profileMarkup, /phone-sheet-mask/);
  assert.match(cleanMarkup, /phone-sheet-mask/);
  assert.match(homeMarkup, /继续获取手机号/);
  assert.match(homeSource, /reportWechatPhoneGrantError\(event\.detail\)/);
  assert.match(profileSource, /reportWechatPhoneGrantError\(event\.detail\)/);
  assert.match(homeSource, /markPhoneBound\(\)/);
  assert.match(profileSource, /markPhoneBound\(\)/);
  assert.match(cleanSource, /reportWechatPhoneGrantError\(event\.detail\)/);
  assert.doesNotMatch(homeSource, /consumePhoneBindingAutoPrompt/);
  assert.match(homeSource, /consumePendingPhoneBindingPrompt\(\)/);
  assert.doesNotMatch(promptSource, /phoneAutoPrompted/);
  assert.match(promptSource, /requestPhoneBindingBeforeAction/);
  assert.match(promptSource, /continueAfterPhoneBindingPrompt/);
  assert.match(promptSource, /cancelAfterPhoneBindingPrompt/);
  assert.match(profileSource, /requestPhoneBindingBeforeAction/);
  assert.match(profileSource, /continueAfterPhoneBindingPrompt\(\)/);
  assert.match(profileSource, /cancelAfterPhoneBindingPrompt\(\)/);
  assert.match(deliveryEntrySource, /requestPhoneBindingBeforeAction/);
  assert.match(deliveryEntrySource, /IDENTITY\.PHONE_BINDING_REQUIRED/);
  assert.match(deliveryEntrySource, /dismissPendingDeviceEntry/);
  assert.doesNotMatch(doorEntrySource, /\/api\/app\/delivery\/open/);
  assert.match(profileSource, /canWithdraw:\s*wallet\.canWithdraw,/);
  assert.doesNotMatch(profileSource, /wallet\.canWithdraw\s*&&\s*this\.data\.phoneBound/);
  assert.match(wechatPhoneGrantSource, /isWechatPhoneGrantCancelled/);
  assert.match(wechatPhoneGrantSource, /detail\.errno === 104/);
  assert.match(phoneGrantSource, /title: '微信获取手机号异常'/);
  assert.match(phoneGrantSource, /console\.error\('\[phone-auth\] 微信获取手机号异常'/);
  assert.match(phoneGrantSource, /错误码：\$\{errno\}/);
  assert.match(phoneGrantSource, /错误信息：\$\{errMsg\}/);
});

test('ordinary-link QR entry trusts WeChat routing and extracts only the device code', () => {
  const registrationSource = source(
    '../miniprogram/miniprogram/utils/registration-source.ts',
  );
  const authApiSource = source(
    '../miniprogram/miniprogram/api/auth.ts',
  );
  const ordinaryLinkSource = source(
    '../miniprogram/miniprogram/utils/ordinary-device-link.ts',
  );
  const intentSource = source(
    '../miniprogram/miniprogram/utils/device-entry-intent.ts',
  );
  const doorEntrySource = source(
    '../miniprogram/miniprogram/utils/door-entry.ts',
  );
  const deliveryApiSource = source(
    '../miniprogram/miniprogram/api/delivery.ts',
  );

  assert.match(registrationSource, /parseOrdinaryDeviceLink\(options\.q/);
  assert.match(ordinaryLinkSource, /key !== 'deviceCode'/);
  assert.match(ordinaryLinkSource, /DEVICE_CODE_PATTERN/);
  assert.doesNotMatch(ordinaryLinkSource, /jinshoubao\.com|ecobin\.com/);
  assert.doesNotMatch(ordinaryLinkSource, /APP_ID_PATTERN|currentMiniProgramAppId/);
  assert.doesNotMatch(ordinaryLinkSource, /tenantCode|tenantId/);
  assert.doesNotMatch(intentSource, /link\.appId|link\.tenantCode/);
  assert.match(authApiSource, /export interface RegistrationSource\s*\{\s*deviceCode: string/);
  assert.doesNotMatch(authApiSource, /tenantCode|tenantId/);
  assert.match(
    authApiSource,
    /registrationSource:\s*registrationSource \?\? null/,
  );
  assert.match(intentSource, /captureOrdinaryDeviceEntry/);
  assert.match(intentSource, /captureScannedDeviceEntry/);
  assert.match(intentSource, /pendingDeviceEntry/);
  assert.match(doorEntrySource, /captureScannedDeviceEntry/);
  assert.match(
    doorEntrySource,
    /export function startDoorEntry\(\): void \{\s*scanDevice\(\)\s*\}/,
  );
  assert.match(doorEntrySource, /wx\.scanCode\(/);
  assert.doesNotMatch(
    doorEntrySource,
    /showActionSheet|showModal|填写设备链接|enterDeviceLink/,
  );
  assert.match(deliveryApiSource, /\/api\/v1\/miniapp\/devices/);
  assert.match(deliveryApiSource, /requestAccepted/);
});

test('miniapp orders and wallet use the target read contracts', () => {
  const deliveryApiSource = source(
    '../miniprogram/miniprogram/api/delivery.ts',
  );
  const walletApiSource = source(
    '../miniprogram/miniprogram/api/wallet.ts',
  );
  const homeSource = source(
    '../miniprogram/miniprogram/pages/home/home.ts',
  );
  const homeMarkup = source(
    '../miniprogram/miniprogram/pages/home/home.wxml',
  );
  const homeStyles = source(
    '../miniprogram/miniprogram/pages/home/home.wxss',
  );
  const ordersSource = source(
    '../miniprogram/miniprogram/pages/orders/orders.ts',
  );
  const detailSource = source(
    '../miniprogram/miniprogram/pages/order-detail/order-detail.ts',
  );
  const detailMarkup = source(
    '../miniprogram/miniprogram/pages/order-detail/order-detail.wxml',
  );
  const miniappApiTypes = source(
    '../miniprogram/miniprogram/types/api.d.ts',
  );
  const deliveryEntrySource = source(
    '../miniprogram/miniprogram/pages/delivery-entry/delivery-entry.ts',
  );
  const configSource = source(
    '../miniprogram/miniprogram/config/index.ts',
  );
  const profileSource = source(
    '../miniprogram/miniprogram/pages/profile/profile.ts',
  );
  const profileMarkup = source(
    '../miniprogram/miniprogram/pages/profile/profile.wxml',
  );
  const walletPageSource = source(
    '../miniprogram/miniprogram/pages/wallet/wallet.ts',
  );
  const walletMarkup = source(
    '../miniprogram/miniprogram/pages/wallet/wallet.wxml',
  );
  const walletStyles = source(
    '../miniprogram/miniprogram/pages/wallet/wallet.wxss',
  );
  const appJsonSource = source(
    '../miniprogram/miniprogram/app.json',
  );

  assert.match(
    deliveryApiSource,
    /\/api\/v1\/miniapp\/me\/delivery-orders/,
  );
  assert.match(deliveryApiSource, /CursorPage<MiniappDeliveryOrderItem>/);
  assert.match(deliveryApiSource, /encodeURIComponent\(deliveryOrderNo\)/);
  assert.doesNotMatch(deliveryApiSource, /\/api\/app\/delivery\/my/);
  assert.match(ordersSource, /nextCursor/);
  assert.match(ordersSource, /reviewStatus:\s*reviewStatus\(this\.data\.active\)/);
  assert.match(ordersSource, /initialFilter\(options\.reviewStatus\)/);
  assert.match(
    ordersSource,
    /reviewStatusValue === 'PENDING' \? 'PENDING' : 'ALL'/,
  );
  assert.match(ordersSource, /COMMON\.INVALID_CURSOR/);
  assert.match(detailSource, /deliveryDetail\(this\.deliveryOrderNo,\s*false\)/);
  assert.match(detailMarkup, /<text>投递时间<\/text>/);
  assert.doesNotMatch(
    detailMarkup,
    /设备公开码|审核版本|设备照片|onPreviewPhoto/,
  );
  assert.doesNotMatch(
    detailSource,
    /detail\.photos|source\.deviceCode|currentRevisionNo|onPreviewPhoto/,
  );
  const miniappOrderItemType = miniappApiTypes.match(
    /export interface MiniappDeliveryOrderItem \{([\s\S]*?)\n\}/,
  )?.[1] ?? '';
  const miniappOrderDetailType = miniappApiTypes.match(
    /export interface MiniappDeliveryOrderDetail \{([\s\S]*?)\n\}/,
  )?.[1] ?? '';
  assert.doesNotMatch(
    miniappOrderItemType,
    /deviceCode|currentRevisionNo|photoCompleteness/,
  );
  assert.doesNotMatch(miniappOrderDetailType, /photos/);
  assert.match(
    deliveryEntrySource,
    /\/pages\/order-detail\/order-detail\?deliveryOrderNo=/,
  );

  assert.match(walletApiSource, /\/api\/v1\/miniapp\/me\/wallet/);
  assert.match(
    walletApiSource,
    /\/api\/v1\/miniapp\/me\/wallet\/entries/,
  );
  assert.doesNotMatch(walletApiSource, /\/api\/app\/wallet/);
  assert.match(
    homeSource,
    /wx\.navigateTo\(\{ url: '\/pages\/wallet\/wallet' \}\)/,
  );
  assert.match(homeMarkup, /class="card-icon wallet-icon"/);
  assert.match(homeMarkup, /class="wallet-value-text"/);
  assert.match(homeStyles, /\.wallet-card\s*\{[^}]*display:\s*grid;/);
  assert.match(homeStyles, /\.wallet-icon\s*\{[^}]*grid-row:\s*2;/);
  assert.match(homeStyles, /\.wallet-value\s*\{[^}]*grid-row:\s*2;/);
  assert.match(homeStyles, /\.wallet-value\s*\{[^}]*height:\s*96rpx;/);
  assert.match(homeStyles, /\.wallet-value\s*\{[^}]*align-items:\s*center;/);
  assert.match(
    homeStyles,
    /\.wallet-value\s*\{[^}]*transform:\s*translateY\(-24rpx\);/,
  );
  assert.match(homeStyles, /\.wallet-value-text\s*\{[^}]*line-height:\s*1;/);
  assert.match(profileSource, /\/pages\/wallet\/wallet/);
  assert.match(
    profileSource,
    /\/pages\/orders\/orders\?reviewStatus=PENDING/,
  );
  assert.match(profileMarkup, /bindtap="onPendingReviewTap"/);
  assert.match(profileMarkup, /<text>待审核<\/text>/);
  assert.doesNotMatch(profileMarkup, /待审核返现/);
  assert.doesNotMatch(
    profileSource,
    /placeholder\/placeholder\?title=钱包明细/,
  );
  assert.match(appJsonSource, /pages\/wallet\/wallet/);
  assert.match(walletPageSource, /myWalletEntries/);
  assert.match(walletPageSource, /nextCursor/);
  assert.match(walletPageSource, /COMMON\.INVALID_CURSOR/);
  assert.doesNotMatch(
    walletPageSource,
    /WITHDRAWAL_FREEZE|提现申请冻结/,
  );
  const miniappWalletEntryType = miniappApiTypes.match(
    /export type MiniappWalletEntryType\s*=([\s\S]*?)\n\n/,
  )?.[1] ?? '';
  assert.doesNotMatch(miniappWalletEntryType, /WITHDRAWAL_FREEZE/);
  assert.match(walletApiSource, /CursorPage<MiniappWalletEntry>/);
  assert.match(walletMarkup, /<text>待审核<\/text>/);
  assert.match(walletMarkup, /class="withdrawal-button"/);
  assert.match(walletMarkup, /bindtap="onWithdrawals"/);
  assert.match(walletMarkup, /<text>提现<\/text>/);
  assert.match(
    walletPageSource,
    /onWithdrawals\(\)\s*\{\s*wx\.navigateTo\(\{ url: '\/pages\/withdrawals\/withdrawals' \}\)/,
  );
  assert.match(walletStyles, /\.withdrawal-button\s*\{/);
  assert.doesNotMatch(walletMarkup, /待审核返现|申请提现|withdrawal-entry/);
  assert.match(configSource, /targetDeliveryOrderApi:\s*true/);
  assert.match(configSource, /targetWalletApi:\s*true/);
  assert.match(configSource, /targetWithdrawalApi:\s*true/);
  assert.match(profileSource, /\/pages\/withdrawals\/withdrawals/);
  assert.doesNotMatch(
    profileSource,
    /placeholder\/placeholder\?title=(?:账户提现|提现记录)/,
  );
  assert.doesNotMatch(configSource, /targetUserDataApi/);
});

test('home and profile refresh once automatically per organization account', () => {
  const homeSource = source(
    '../miniprogram/miniprogram/pages/home/home.ts',
  );
  const homeMarkup = source(
    '../miniprogram/miniprogram/pages/home/home.wxml',
  );
  const homeConfig = JSON.parse(source(
    '../miniprogram/miniprogram/pages/home/home.json',
  ));
  const profileSource = source(
    '../miniprogram/miniprogram/pages/profile/profile.ts',
  );
  const profileConfig = JSON.parse(source(
    '../miniprogram/miniprogram/pages/profile/profile.json',
  ));

  assert.equal(homeConfig.enablePullDownRefresh, true);
  assert.equal(profileConfig.enablePullDownRefresh, true);
  assert.match(
    homeSource,
    /const contextKey = session\.organizationUserUid[\s\S]*const shouldAutoLoad = this\.overviewContextKey !== contextKey/,
  );
  assert.match(
    homeSource,
    /if \(shouldAutoLoad\) void this\.refreshOverview\(contextKey\)/,
  );
  assert.match(homeSource, /overviewLoadPromiseContextKey === contextKey/);
  assert.match(homeSource, /async onPullDownRefresh\(\)/);
  assert.match(homeSource, /await this\.refreshOverview\(contextKey\)/);
  assert.match(homeSource, /wx\.stopPullDownRefresh\(\)/);
  assert.doesNotMatch(homeSource, /onRetryRecent/);
  assert.doesNotMatch(homeMarkup, /bindtap="onRetryRecent"|点击重试/);
  assert.match(homeMarkup, /下拉刷新重试/);

  assert.match(
    profileSource,
    /const contextKey = session\.organizationUserUid[\s\S]*const shouldAutoLoad = this\.walletContextKey !== contextKey/,
  );
  assert.match(
    profileSource,
    /if \(shouldAutoLoad\) void this\.refreshWallet\(contextKey\)/,
  );
  assert.match(profileSource, /walletLoadPromiseContextKey === contextKey/);
  assert.match(profileSource, /async onPullDownRefresh\(\)/);
  assert.match(profileSource, /await this\.refreshWallet\(contextKey\)/);
  assert.match(profileSource, /wx\.stopPullDownRefresh\(\)/);
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
  assert.match(profileSource, /getSession,\s*logout/);
  assert.match(profileSource, /if \(res\.confirm\) void logout\(\)/);
  assert.doesNotMatch(profileSource, /静态演示模式/);
});

test('profile keeps the accepted full-width artwork and screen proportions', () => {
  const profileMarkup = source(
    '../miniprogram/miniprogram/pages/profile/profile.wxml',
  );
  const profileStyle = source(
    '../miniprogram/miniprogram/pages/profile/profile.wxss',
  );

  assert.match(
    profileMarkup,
    /src="\/assets\/profile\/profile-hero-bg\.png"[\s\S]*mode="widthFix"/,
  );
  assert.doesNotMatch(profileMarkup, /profile-(?:landscape|hill|tree)/);
  assert.doesNotMatch(profileMarkup, /identity-organization/);
  assert.match(
    profileStyle,
    /\.profile-hero\s*\{[\s\S]*height:\s*502rpx;[\s\S]*margin-right:\s*-25rpx;[\s\S]*margin-left:\s*-25rpx;/,
  );
  assert.match(
    profileStyle,
    /\.profile-hero-bg\s*\{[\s\S]*bottom:\s*0;[\s\S]*width:\s*calc\(100% \+ 2rpx\);/,
  );
  assert.match(profileStyle, /\.wallet-card\s*\{[\s\S]*height:\s*362rpx;/);
  assert.match(profileStyle, /\.shortcut-card\s*\{[\s\S]*height:\s*218rpx;/);
  assert.match(profileStyle, /\.menu-card\s*\{[\s\S]*height:\s*367rpx;/);
});

test('organization account sheet sits flush with the custom tab bar surface', () => {
  const profileStyle = source(
    '../miniprogram/miniprogram/pages/profile/profile.wxss',
  );
  const tabBarStyle = source(
    '../miniprogram/miniprogram/custom-tab-bar/index.wxss',
  );
  const tabBarSurfaceRule = tabBarStyle.match(
    /\.tab-bar::before\s*\{([\s\S]*?)\}/,
  )?.[1];
  const maskRule = profileStyle.match(
    /\.account-sheet-mask\s*\{([\s\S]*?)\}/,
  )?.[1];
  const sheetRule = profileStyle.match(
    /\.account-sheet\s*\{([\s\S]*?)\}/,
  )?.[1];
  const sheetExtensionRule = profileStyle.match(
    /\.account-sheet::after\s*\{([\s\S]*?)\}/,
  )?.[1];

  assert.ok(tabBarSurfaceRule, 'custom tab bar surface rule must exist');
  assert.ok(maskRule, 'account sheet mask rule must exist');
  assert.ok(sheetRule, 'account sheet rule must exist');
  assert.ok(
    sheetExtensionRule,
    'account sheet background must extend behind the custom tab bar',
  );
  const tabBarSurfaceHeight = tabBarSurfaceRule.match(
    /height:\s*([^;]+);/,
  )?.[1].trim();
  const sheetBottomClearance = maskRule.match(
    /padding-bottom:\s*([^;]+);/,
  )?.[1].trim();
  assert.equal(sheetBottomClearance, tabBarSurfaceHeight);
  const sheetExtensionHeight = sheetExtensionRule.match(
    /height:\s*([^;]+);/,
  )?.[1].trim();
  assert.equal(sheetExtensionHeight, tabBarSurfaceHeight);
  assert.match(maskRule, /box-sizing:\s*border-box;/);
  assert.match(sheetRule, /padding:\s*18rpx 30rpx 54rpx;/);
  assert.match(sheetExtensionRule, /top:\s*100%;/);
  assert.match(sheetExtensionRule, /background:\s*#fff;/);
});
