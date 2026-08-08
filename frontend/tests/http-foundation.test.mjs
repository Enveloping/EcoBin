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

test('the WeChat ordinary-link validation file is published byte-for-byte', () => {
  const validation = readFileSync(new URL(
    '../web/public/device-entry/5JOZivQ9vw.txt',
    import.meta.url,
  ));
  assert.equal(validation.length, 32);
  assert.equal(
    validation.toString('utf8'),
    'cd850e2c9d0e24abfd68ed19dc5afc79',
  );
});

test('the device entry directory falls back to the SPA instead of 403', () => {
  const nginx = readFileSync(
    new URL('../web/nginx.conf', import.meta.url),
    'utf8',
  );
  assert.match(
    nginx,
    /location = \/device-entry\/ \{\s*try_files \/index\.html =404;\s*\}/,
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
