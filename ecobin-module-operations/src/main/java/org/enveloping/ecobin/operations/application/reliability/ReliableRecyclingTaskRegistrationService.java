package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableFundsTaskJdbcRepository;
import org.enveloping.ecobin.recycling.api.port.ReliableRecyclingTaskRegistrationPort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Service
public class ReliableRecyclingTaskRegistrationService
        implements ReliableRecyclingTaskRegistrationPort {

    private final ReliableFundsTaskJdbcRepository repository;

    public ReliableRecyclingTaskRegistrationService(
            ReliableFundsTaskJdbcRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public UUID register(Registration registration) {
        return repository.insert(registration);
    }
}
