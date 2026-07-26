import type { EntryMode, MiniappAudience } from '../types/api'

export interface SessionEntry {
  audience: MiniappAudience
  entryMode: EntryMode
}

/** audience 或入口任一变化都禁止在原页面重放请求。 */
export function sessionEntryChanged(
  previous: SessionEntry | undefined,
  current: SessionEntry,
): boolean {
  return !!previous
    && (
      previous.audience !== current.audience
      || previous.entryMode !== current.entryMode
    )
}
