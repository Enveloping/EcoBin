package org.enveloping.ecobin.funds.api.query;

/**
 * Selects which immutable personal wallet entries are visible to a caller.
 */
public enum PersonalWalletEntryAudience {

    /** Ordinary users do not see the internal transfer into withdrawal processing. */
    ORDINARY_USER,

    /** Authorized audit readers see the complete immutable wallet ledger. */
    AUDIT
}
