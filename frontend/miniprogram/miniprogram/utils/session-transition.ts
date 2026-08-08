import type { EntryMode, MiniappAudience } from '../types/api'

export interface SessionEntry {
  audience: MiniappAudience
  entryMode: EntryMode
  organizationUserUid: string
}

export interface SessionInstance {
  accessToken: string
}

/** 请求等待期间若 Token 已变化，说明当前会话已被退出、登录或账号切换替代。 */
export function sessionInstanceChanged(
  previous: SessionInstance | undefined,
  current: SessionInstance | undefined,
): boolean {
  return previous?.accessToken !== current?.accessToken
}

/** 机构账号、audience 或入口任一变化都禁止在原页面重放请求。 */
export function sessionEntryChanged(
  previous: SessionEntry | undefined,
  current: SessionEntry,
): boolean {
  return !!previous
    && (
      previous.audience !== current.audience
      || previous.entryMode !== current.entryMode
      || previous.organizationUserUid !== current.organizationUserUid
    )
}
