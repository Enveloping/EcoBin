import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

import {
  LEGACY_WEB_CREDENTIAL_KEYS,
  migrateLegacyWebCredentials,
} from '../web/src/security/legacyCredentialMigration.ts';
import {
  LEGACY_MINIAPP_CREDENTIAL_KEYS,
  migrateLegacyMiniappCredentials,
} from '../miniprogram/miniprogram/utils/legacy-session-migration.ts';
import {
  sessionEntryChanged,
  sessionInstanceChanged,
} from '../miniprogram/miniprogram/utils/session-transition.ts';
import {
  isDeviceEntryBaseUrl,
} from '../web/src/utils/deviceEntryBaseUrl.ts';

test('web startup migration removes the legacy persisted Bearer store', () => {
  const removed = [];
  migrateLegacyWebCredentials({
    removeItem: (key) => removed.push(key),
  });
  assert.deepEqual(removed, [...LEGACY_WEB_CREDENTIAL_KEYS]);

  const mainSource = readFileSync(
    new URL('../web/src/main.tsx', import.meta.url),
    'utf8',
  );
  assert.match(
    mainSource,
    /migrateLegacyWebCredentials\(localStorage\)/,
  );
});

test('miniapp launch migration removes every legacy login record', () => {
  const removed = [];
  migrateLegacyMiniappCredentials({
    removeStorageSync: (key) => removed.push(key),
  });
  assert.deepEqual(removed, [...LEGACY_MINIAPP_CREDENTIAL_KEYS]);

  const appSource = readFileSync(
    new URL('../miniprogram/miniprogram/app.ts', import.meta.url),
    'utf8',
  );
  assert.match(appSource, /migrateLegacyMiniappCredentials\(wx\)/);
});

test('same audience with a changed entry mode is a route transition', () => {
  assert.equal(
    sessionEntryChanged(
      {
        audience: 'miniapp',
        entryMode: 'USER',
        organizationUserUid: 'organization-user-a',
      },
      {
        audience: 'miniapp',
        entryMode: 'CLEANING',
        organizationUserUid: 'organization-user-a',
      },
    ),
    true,
  );

  assert.equal(
    sessionInstanceChanged(
      { accessToken: 'request-token' },
      { accessToken: 'request-token' },
    ),
    false,
  );
  assert.equal(
    sessionInstanceChanged(
      { accessToken: 'request-token' },
      { accessToken: 'new-account-token' },
    ),
    true,
  );
  assert.equal(
    sessionInstanceChanged({ accessToken: 'request-token' }, undefined),
    true,
  );
  assert.equal(
    sessionEntryChanged(
      {
        audience: 'miniapp',
        entryMode: 'USER',
        organizationUserUid: 'organization-user-a',
      },
      {
        audience: 'miniapp-staff',
        entryMode: 'MANAGEMENT',
        organizationUserUid: 'organization-user-a',
      },
    ),
    true,
  );
  assert.equal(
    sessionEntryChanged(
      {
        audience: 'miniapp',
        entryMode: 'USER',
        organizationUserUid: 'organization-user-a',
      },
      {
        audience: 'miniapp',
        entryMode: 'USER',
        organizationUserUid: 'organization-user-a',
      },
    ),
    false,
  );
  assert.equal(
    sessionEntryChanged(
      {
        audience: 'miniapp',
        entryMode: 'USER',
        organizationUserUid: 'organization-user-a',
      },
      {
        audience: 'miniapp',
        entryMode: 'USER',
        organizationUserUid: 'organization-user-b',
      },
    ),
    true,
  );

  const requestSource = readFileSync(
    new URL(
      '../miniprogram/miniprogram/utils/request.ts',
      import.meta.url,
    ),
    'utf8',
  );
  assert.match(
    requestSource,
    /sessionEntryChanged\(previousSession,\s*renewed\)/,
  );
  assert.match(
    requestSource,
    /const previousSession = getSession\(\)[\s\S]*?await requestOnce/,
  );
  assert.match(requestSource, /!isSilentLoginSuppressed\(\)/);
  assert.match(
    requestSource,
    /sessionInstanceChanged\(previousSession, getSession\(\)\)/,
  );
  assert.match(
    requestSource,
    /sessionInstanceChanged\(renewed, activeSession\)/,
  );
  assert.match(requestSource, /clearSessionIfCurrent\(previousSession\)/);

  const authSource = readFileSync(
    new URL(
      '../miniprogram/miniprogram/utils/auth.ts',
      import.meta.url,
    ),
    'utf8',
  );
  assert.match(
    authSource,
    /function adoptSession[\s\S]*?cancelActiveLogin\(\)/,
  );
});

test('a newer QR bootstrap supersedes an in-flight ordinary bootstrap', () => {
  const appSource = readFileSync(
    new URL('../miniprogram/miniprogram/app.ts', import.meta.url),
    'utf8',
  );
  assert.match(appSource, /const sequence = \+\+bootstrapSequence/);
  assert.match(appSource, /sequence !== bootstrapSequence/);
  assert.match(
    appSource,
    /bootstrapping && pendingEntryId === activeBootstrapEntryId/,
  );
});

test('the deleted login-page mini-program-code generator is not retained', () => {
  assert.equal(
    existsSync(new URL(
      '../../tools/wechat/generate-device-registration-code.ps1',
      import.meta.url,
    )),
    false,
  );
});

test('device entry base URL cannot carry a pre-existing deviceCode', () => {
  assert.equal(
    isDeviceEntryBaseUrl('https://entry.example/device'),
    true,
  );
  assert.equal(
    isDeviceEntryBaseUrl('https://entry.example/device?source=poster'),
    true,
  );
  assert.equal(
    isDeviceEntryBaseUrl('https://entry.example/device?deviceCode=old'),
    false,
  );
  assert.equal(
    isDeviceEntryBaseUrl('https://user:secret@entry.example/device'),
    false,
  );
  assert.equal(
    isDeviceEntryBaseUrl('https:///device'),
    false,
  );
});

test('cleaning and management profiles share an organization account center', () => {
  const appJson = JSON.parse(readFileSync(
    new URL('../miniprogram/miniprogram/app.json', import.meta.url),
    'utf8',
  ));
  assert.equal(
    appJson.pages.includes('pages/account-switcher/account-switcher'),
    true,
  );
  for (const relativePath of [
    '../miniprogram/miniprogram/pages/clean-profile/clean-profile.ts',
    '../miniprogram/miniprogram/pages/management/management.ts',
  ]) {
    assert.match(
      readFileSync(new URL(relativePath, import.meta.url), 'utf8'),
      /pages\/account-switcher\/account-switcher/,
    );
  }
  const apiSource = readFileSync(
    new URL('../miniprogram/miniprogram/api/auth.ts', import.meta.url),
    'utf8',
  );
  assert.match(apiSource, /\/api\/v1\/miniapp-staff/);
});

test('trusted scan bypasses the local session without discarding it first', () => {
  const authSource = readFileSync(
    new URL(
      '../miniprogram/miniprogram/utils/auth.ts',
      import.meta.url,
    ),
    'utf8',
  );
  assert.match(
    authSource,
    /if \(registrationSource\) \{\s*[\s\S]*?return login\(registrationSource\)\s*\}/,
  );
  assert.doesNotMatch(
    authSource,
    /if \(registrationSource\) \{[\s\S]{0,400}?clearSession\(\)/,
  );
});
