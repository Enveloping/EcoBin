package org.enveloping.ecobin.framework.reliability;

/**
 * 权威业务事务对一条 inbox 的幂等处理结论。
 */
public enum InboxTaskCompletionOutcome {
    APPLIED,
    NO_ACTION_REQUIRED
}
