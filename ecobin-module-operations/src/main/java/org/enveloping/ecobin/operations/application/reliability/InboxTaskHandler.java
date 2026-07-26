package org.enveloping.ecobin.operations.application.reliability;

@FunctionalInterface
public interface InboxTaskHandler {

    InboxTaskHandlerResult handle(ClaimedInboxTask task);
}
