import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('clean workbench routes and its two native tab roots are registered', () => {
  const appConfig = JSON.parse(
    source('../miniprogram/miniprogram/app.json'),
  );
  const expectedPages = [
    'pages/clean/clean',
    'pages/clean-profile/clean-profile',
    'pages/clean-operation/clean-operation',
    'pages/clean-devices/clean-devices',
    'pages/clean-records/clean-records',
    'pages/clean-record-detail/clean-record-detail',
    'pages/bag-trace/bag-trace',
    'pages/bag-trace-orders/bag-trace-orders',
    'pages/bag-trace-detail/bag-trace-detail',
  ];

  for (const page of expectedPages) {
    assert.ok(appConfig.pages.includes(page), `${page} should be registered`);
  }

  const tabPages = appConfig.tabBar.list.map(({ pagePath }) => pagePath);
  assert.ok(tabPages.includes('pages/clean/clean'));
  assert.ok(tabPages.includes('pages/clean-profile/clean-profile'));
});

test('clean workbench exposes the agreed function cards including bag trace', () => {
  const cleanMarkup = source(
    '../miniprogram/miniprogram/pages/clean/clean.wxml',
  );
  const cardLabels = [
    '全部设备',
    '在线设备',
    '一天未投递设备',
    '一天未清运设备',
    '满溢设备',
    '满溢超时2h',
    '清运记录',
    '袋码溯源',
  ];

  for (const label of cardLabels) {
    const occurrences = cleanMarkup.split(label).length - 1;
    assert.equal(occurrences, 1, `${label} should appear on one card`);
  }

  for (const rejectedCopy of [
    '开清运门',
    '青禾环境',
    '扫描设备二维码',
    '现场门控由设备屏幕操作',
    '待恢复操作',
    '今日作业',
    '最近清运',
    '按重量',
  ]) {
    assert.doesNotMatch(cleanMarkup, new RegExp(rejectedCopy));
  }
});

test('clean workbench maps all six device filters and both destination pages', () => {
  const cleanSource = source(
    '../miniprogram/miniprogram/pages/clean/clean.ts',
  );
  const cleanMarkup = source(
    '../miniprogram/miniprogram/pages/clean/clean.wxml',
  );
  const deviceFilters = [
    'ALL',
    'ONLINE',
    'NO_DELIVERY_24H',
    'NO_CLEAN_24H',
    'FULL',
    'FULL_TIMEOUT_2H',
  ];

  for (const filter of deviceFilters) {
    assert.match(
      cleanSource,
      new RegExp(`(?:^|\\s)(?:['"])?${filter}(?:['"])?\\s*:`, 'm'),
      `${filter} should exist in the filter title mapping`,
    );
    assert.match(
      cleanMarkup,
      new RegExp(`data-filter=['"]${filter}['"]`),
      `${filter} should be connected to a workbench card`,
    );
  }
  assert.match(cleanSource, /\/pages\/clean-devices\/clean-devices/);
  assert.match(cleanSource, /\/pages\/clean-records\/clean-records/);
  assert.match(cleanSource, /\/pages\/bag-trace\/bag-trace\?scan=1/);
});

test('bag trace stays in cleaning mode and drills into cycles and orders', () => {
  const apiSource = source(
    '../miniprogram/miniprogram/api/bag-trace.ts',
  );
  const traceSource = source(
    '../miniprogram/miniprogram/pages/bag-trace/bag-trace.ts',
  );
  const cycleSource = source(
    '../miniprogram/miniprogram/pages/bag-trace-orders/bag-trace-orders.ts',
  );
  const detailSource = source(
    '../miniprogram/miniprogram/pages/bag-trace-detail/bag-trace-detail.ts',
  );

  assert.match(traceSource, /requireEntryMode\(\[['"]CLEANING['"]\]\)/);
  assert.match(traceSource, /wx\.scanCode/);
  assert.match(traceSource, /\/pages\/bag-trace-orders\/bag-trace-orders/);
  assert.match(cycleSource, /\/pages\/bag-trace-detail\/bag-trace-detail/);
  assert.match(detailSource, /bagCycleOrderDetail/);
  assert.match(detailSource, /reason:\s*order\.reason/);
  assert.match(apiSource, /requireRealCleaningSession/);
  assert.match(apiSource, /session\?\.audience\s*!==\s*['"]miniapp['"]/);
  assert.match(apiSource, /session\.entryMode\s*!==\s*['"]CLEANING['"]/);
});

test('custom tab bar projects user and cleaning roots and scan actions', () => {
  const tabSource = source(
    '../miniprogram/miniprogram/custom-tab-bar/index.ts',
  );

  assert.match(tabSource, /getEntryMode/);
  assert.doesNotMatch(tabSource, /getDisplayedEntryMode|test-entry-preview/);
  assert.match(tabSource, /['"]USER['"]/);
  assert.match(tabSource, /['"]CLEANING['"]/);
  assert.match(tabSource, /\/pages\/home\/home/);
  assert.match(tabSource, /\/pages\/profile\/profile/);
  assert.match(tabSource, /\/pages\/clean\/clean/);
  assert.match(tabSource, /\/pages\/clean-profile\/clean-profile/);
  assert.match(tabSource, /\bstartDoorEntry\(\)/);
  assert.match(tabSource, /\bstartCleaningEntry\(\)/);
});

test('user and cleaning entry preview switching is completely removed', () => {
  const previewPath = new URL(
    '../miniprogram/miniprogram/utils/test-entry-preview.ts',
    import.meta.url,
  );
  const appSource = source('../miniprogram/miniprogram/app.ts');
  const configSource = source('../miniprogram/miniprogram/config/index.ts');
  const typingsSource = source('../miniprogram/typings/index.d.ts');
  const userProfileSource = source(
    '../miniprogram/miniprogram/pages/profile/profile.ts',
  );
  const userProfileMarkup = source(
    '../miniprogram/miniprogram/pages/profile/profile.wxml',
  );
  const cleanProfileSource = source(
    '../miniprogram/miniprogram/pages/clean-profile/clean-profile.ts',
  );
  const cleanProfileMarkup = source(
    '../miniprogram/miniprogram/pages/clean-profile/clean-profile.wxml',
  );

  assert.equal(existsSync(previewPath), false);
  for (const currentSource of [
    appSource,
    configSource,
    typingsSource,
    userProfileSource,
    userProfileMarkup,
    cleanProfileSource,
    cleanProfileMarkup,
  ]) {
    assert.doesNotMatch(
      currentSource,
      /testViewMode|entryPreview|test-entry-preview|切换端|测试界面预览/,
    );
  }
});

test('entry guard authorizes pages only against the signed session mode', () => {
  const guardSource = source(
    '../miniprogram/miniprogram/utils/guard.ts',
  );

  assert.match(guardSource, /const entryMode = getEntryMode\(\)/);
  assert.doesNotMatch(guardSource, /getDisplayedEntryMode|test-entry-preview/);
});

test('cleaning scan rejects non-cleaning sessions before opening the scanner', () => {
  const cleaningEntrySource = source(
    '../miniprogram/miniprogram/utils/cleaning-entry.ts',
  );
  const crossIdentityGuard =
    /if\s*\(\s*(?:getEntryMode\(\)|getSession\(\)\?\.entryMode)\s*!==\s*['"]CLEANING['"]\s*\)\s*\{[\s\S]*?wx\.showToast\([\s\S]*?\breturn\b[\s\S]*?\}/;

  assert.match(cleaningEntrySource, crossIdentityGuard);
  assert.match(cleaningEntrySource, /当前账号没有清运权限/);
  assert.doesNotMatch(cleaningEntrySource, /预览|切换端/);

  const guardIndex = cleaningEntrySource.search(crossIdentityGuard);
  const scanIndex = cleaningEntrySource.indexOf('wx.scanCode');
  const operationIndex = cleaningEntrySource.indexOf(
    '/pages/clean-operation/clean-operation',
  );
  assert.ok(guardIndex >= 0 && guardIndex < scanIndex);
  assert.ok(scanIndex >= 0 && scanIndex < operationIndex);
});
