package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.api.reliability.ReliableWorkAvailableEvent;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;

@Component
public class ReliableWorkSignal {

    private final ApplicationEventPublisher publisher;

    public ReliableWorkSignal(ApplicationEventPublisher publisher) {
        this.publisher = publisher;
    }

    public void deviceInbox() {
        publisher.publishEvent(new ReliableWorkAvailableEvent(
                ReliableWorkAvailableEvent.Kind.DEVICE_INBOX));
    }

    public void deviceCommand() {
        publisher.publishEvent(new ReliableWorkAvailableEvent(
                ReliableWorkAvailableEvent.Kind.DEVICE_COMMAND));
    }
}
