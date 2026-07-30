import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
  ];

  for (const page of expectedPages) {
    assert.ok(appConfig.pages.includes(page), `${page} should be registered`);
  }

  const tabPages = appConfig.tabBar.list.map(({ pagePath }) => pagePath);
  assert.ok(tabPages.includes('pages/clean/clean'));
  assert.ok(tabPages.includes('pages/clean-profile/clean-profile'));
});

test('clean workbench exposes exactly the seven agreed function cards', () => {
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
  const deviceFilters = [
    'ALL',
    'ONLINE',
    'NO_DELIVERY_24H',
    'NO_CLEAN_24H',
    'FULL',
    'FULL_TIMEOUT_2H',
  ];

  for (const filter of deviceFilters) {
    assert.match(cleanSource, new RegExp(`['"]${filter}['"]`));
  }
  assert.match(cleanSource, /\/pages\/clean-devices\/clean-devices/);
  assert.match(cleanSource, /\/pages\/clean-records\/clean-records/);
});

test('custom tab bar projects user and cleaning roots and scan actions', () => {
  const tabSource = source(
    '../miniprogram/miniprogram/custom-tab-bar/index.ts',
  );

  assert.match(tabSource, /getDisplayedEntryMode/);
  assert.match(tabSource, /['"]USER['"]/);
  assert.match(tabSource, /['"]CLEANING['"]/);
  assert.match(tabSource, /\/pages\/home\/home/);
  assert.match(tabSource, /\/pages\/profile\/profile/);
  assert.match(tabSource, /\/pages\/clean\/clean/);
  assert.match(tabSource, /\/pages\/clean-profile\/clean-profile/);
  assert.match(tabSource, /\bstartDoorEntry\(\)/);
  assert.match(tabSource, /\bstartCleaningEntry\(\)/);
});

test('test entry preview remains an in-memory UI projection', () => {
  const previewSource = source(
    '../miniprogram/miniprogram/utils/test-entry-preview.ts',
  );

  assert.match(
    previewSource,
    /\.globalData\.testViewMode\s*=\s*(?:mode|entryMode|undefined)/,
  );
  assert.doesNotMatch(previewSource, /wx\.setStorage(?:Sync)?\s*\(/);
  assert.doesNotMatch(
    previewSource,
    /\b(?:setSession|persistSession|STORAGE_KEYS|accessToken|refreshToken)\b/,
  );
  assert.doesNotMatch(
    previewSource,
    /\.(?:session|token|capabilities)\s*=/,
  );
});

test('entry guard authorizes pages against the displayed mode', () => {
  const guardSource = source(
    '../miniprogram/miniprogram/utils/guard.ts',
  );

  assert.match(guardSource, /getDisplayedEntryMode/);
  assert.match(
    guardSource,
    /getDisplayedEntryMode\(\s*getEntryMode\(\)\s*\)/,
  );
});

test('cleaning scan blocks cross-identity previews before opening the scanner', () => {
  const cleaningEntrySource = source(
    '../miniprogram/miniprogram/utils/cleaning-entry.ts',
  );
  const crossIdentityGuard =
    /if\s*\(\s*(?:getEntryMode\(\)|getSession\(\)\?\.entryMode)\s*!==\s*['"]CLEANING['"]\s*\)\s*\{[\s\S]*?wx\.showToast\([\s\S]*?\breturn\b[\s\S]*?\}/;

  assert.match(cleaningEntrySource, crossIdentityGuard);

  const guardIndex = cleaningEntrySource.search(crossIdentityGuard);
  const scanIndex = cleaningEntrySource.indexOf('wx.scanCode');
  const operationIndex = cleaningEntrySource.indexOf(
    '/pages/clean-operation/clean-operation',
  );
  assert.ok(guardIndex >= 0 && guardIndex < scanIndex);
  assert.ok(scanIndex >= 0 && scanIndex < operationIndex);
});
