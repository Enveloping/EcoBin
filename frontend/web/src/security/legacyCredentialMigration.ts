export const LEGACY_WEB_CREDENTIAL_KEYS = ['ecobin-auth'] as const;

export interface LegacyWebCredentialStorage {
  removeItem(key: string): void;
}

/**
 * F-09 cutover cleanup. The legacy Zustand store contains a JavaScript-readable
 * Bearer Token, so it must be deleted before the new application starts.
 */
export function migrateLegacyWebCredentials(
  storage: LegacyWebCredentialStorage,
): void {
  for (const key of LEGACY_WEB_CREDENTIAL_KEYS) {
    storage.removeItem(key);
  }
}
