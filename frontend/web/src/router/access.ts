export interface RouteAccessRule {
  allOf?: readonly string[];
  anyOf?: readonly string[];
  accountTypes?: readonly string[];
}

export interface RouteAccessSession {
  accountType: string;
  capabilities: readonly string[];
}

export function hasRouteAccess(
  session: RouteAccessSession | null,
  rule: RouteAccessRule,
): boolean {
  if (!session) return false;
  if (
    rule.accountTypes
    && !rule.accountTypes.includes(session.accountType)
  ) {
    return false;
  }
  if (
    rule.allOf
    && !rule.allOf.every((capability) =>
      session.capabilities.includes(capability))
  ) {
    return false;
  }
  if (
    rule.anyOf?.length
    && !rule.anyOf.some((capability) =>
      session.capabilities.includes(capability))
  ) {
    return false;
  }
  return true;
}
