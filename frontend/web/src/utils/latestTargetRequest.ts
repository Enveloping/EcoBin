export interface TargetRequestTicket {
  readonly sequence: number;
  readonly targetKey: string;
  readonly signal: AbortSignal;
}

interface ActiveTargetRequest extends TargetRequestTicket {
  readonly controller: AbortController;
}

/**
 * Keeps one read request authoritative for a mutable UI selection.
 * Starting or invalidating a request aborts the previous transport and also
 * advances a sequence fence, so a late response cannot become current even
 * when the transport ignores cancellation.
 */
export class LatestTargetRequestGuard {
  private sequence = 0;
  private active: ActiveTargetRequest | null = null;

  begin(targetKey: string): TargetRequestTicket {
    if (!targetKey) throw new TypeError('targetKey is required');
    this.active?.controller.abort();
    const controller = new AbortController();
    const active: ActiveTargetRequest = {
      sequence: ++this.sequence,
      targetKey,
      signal: controller.signal,
      controller,
    };
    this.active = active;
    return {
      sequence: active.sequence,
      targetKey: active.targetKey,
      signal: active.signal,
    };
  }

  accepts(ticket: TargetRequestTicket, currentTargetKey: string): boolean {
    return !ticket.signal.aborted
      && this.active !== null
      && ticket.sequence === this.active.sequence
      && ticket.targetKey === this.active.targetKey
      && ticket.targetKey === currentTargetKey;
  }

  invalidate(): void {
    this.active?.controller.abort();
    this.active = null;
    this.sequence += 1;
  }
}

export function walletPreviewTargetKey(
  domain: 'platform' | 'tenant',
  tenantCode: string | undefined,
  organizationCode: string,
  organizationUserUid: string,
): string {
  if (!organizationCode || !organizationUserUid) {
    throw new TypeError('organization and user identities are required');
  }
  return JSON.stringify([
    domain,
    tenantCode ?? null,
    organizationCode,
    organizationUserUid,
  ]);
}
