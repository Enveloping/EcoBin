import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
      { audience: 'miniapp', entryMode: 'USER' },
      { audience: 'miniapp', entryMode: 'CLEANING' },
    ),
    true,
  );
  assert.equal(
    sessionEntryChanged(
      { audience: 'miniapp', entryMode: 'USER' },
      { audience: 'miniapp-staff', entryMode: 'MANAGEMENT' },
    ),
    true,
  );
  assert.equal(
    sessionEntryChanged(
      { audience: 'miniapp', entryMode: 'USER' },
      { audience: 'miniapp', entryMode: 'USER' },
    ),
    false,
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
});
