package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableFundsTaskJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Service
public class ReliableFundsTaskRegistrationService
        implements ReliableFundsTaskRegistrationPort {

    private final ReliableFundsTaskJdbcRepository repository;

    public ReliableFundsTaskRegistrationService(
            ReliableFundsTaskJdbcRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public UUID register(ReliableFundsTaskRegistration registration) {
        return repository.insert(registration);
    }
}
