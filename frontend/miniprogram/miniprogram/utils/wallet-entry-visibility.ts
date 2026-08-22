import type {
  CursorPage,
  MiniappWalletEntry,
  MiniappWalletEntryType,
} from '../types/api'

export type WalletEntryPageFetcher = (
  cursor?: string,
) => Promise<CursorPage<MiniappWalletEntry>>

const VISIBLE_ENTRY_TYPES: Record<MiniappWalletEntryType, true> = {
  DELIVERY_INITIAL_REVIEW: true,
  DELIVERY_CORRECTION: true,
  WITHDRAWAL_SUCCEEDED: true,
  MANUAL_ADJUSTMENT: true,
}

function isVisibleEntry(item: MiniappWalletEntry): boolean {
  return Object.prototype.hasOwnProperty.call(
    VISIBLE_ENTRY_TYPES,
    String(item.entryType),
  )
}

export async function loadVisibleWalletEntryPage(
  fetchPage: WalletEntryPageFetcher,
  cursor?: string,
  shouldContinue: () => boolean = () => true,
): Promise<CursorPage<MiniappWalletEntry>> {
  let pageCursor = cursor
  let snapshotAsOf: string | undefined
  const followedCursors = new Set<string>()
  if (cursor) followedCursors.add(cursor)

  while (true) {
    const page = await fetchPage(pageCursor)
    snapshotAsOf ??= page.asOf
    if (!shouldContinue()) {
      return {
        items: [],
        asOf: snapshotAsOf,
        nextCursor: null,
      }
    }
    const visibleItems = page.items.filter(isVisibleEntry)
    if (visibleItems.length > 0 || !page.nextCursor) {
      return {
        items: visibleItems,
        asOf: snapshotAsOf,
        nextCursor: page.nextCursor,
      }
    }
    if (followedCursors.has(page.nextCursor)) {
      throw new Error('钱包明细分页游标未向后推进')
    }
    followedCursors.add(page.nextCursor)
    pageCursor = page.nextCursor
  }
}
