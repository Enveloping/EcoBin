export const LEGACY_MINIAPP_CREDENTIAL_KEYS = [
  'ecobin_token',
  'ecobin_role',
  'ecobin_user_info',
] as const

export interface LegacyMiniappCredentialStorage {
  removeStorageSync(key: string): void
}

/** 删除旧版可读 Bearer、数字角色和用户快照；重复执行是安全的。 */
export function migrateLegacyMiniappCredentials(
  storage: LegacyMiniappCredentialStorage,
): void {
  for (const key of LEGACY_MINIAPP_CREDENTIAL_KEYS) {
    storage.removeStorageSync(key)
  }
}
