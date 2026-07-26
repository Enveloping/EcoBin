package org.enveloping.ecobin.framework.reliability;

/**
 * 让 inbox 完成事实加入调用方已经开启的权威业务事务。
 */
public interface InboxTaskCompletionPort {

    void complete(InboxTaskCompletion completion);
}
