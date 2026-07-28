package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Objects;
import java.util.Optional;

@Service
public class ReliableTaskClaimService {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableTaskProperties properties;

    public ReliableTaskClaimService(
            ReliableOperationsJdbcRepository repository,
            ReliableTaskProperties properties) {
        this.repository = repository;
        this.properties = properties;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public Optional<ClaimedInboxTask> claimNext(
            ReliableTaskChannel channel, String workerId) {
        validate(channel, workerId);
        properties.validate();
        ReliableTaskProperties.Channel policy = channelProperties(channel);
        return repository.claimInboxTasks(
                channel, workerId, 1, policy.getLeaseDuration())
                .stream()
                .findFirst();
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public Optional<ClaimedDeviceCommandTask> claimNextDeviceCommand(
            String workerId) {
        validate(ReliableTaskChannel.IOT_DEVICE, workerId);
        properties.validate();
        ReliableTaskProperties.Channel policy =
                properties.getIotDevice();
        return repository.claimDeviceCommandTasks(
                        workerId, 1, policy.getLeaseDuration())
                .stream()
                .findFirst();
    }

    public int batchBudget(ReliableTaskChannel channel) {
        Objects.requireNonNull(channel, "channel");
        properties.validate();
        return channelProperties(channel).getBatchSize();
    }

    private static void validate(
            ReliableTaskChannel channel, String workerId) {
        Objects.requireNonNull(channel, "channel");
        if (workerId == null
                || !workerId.matches("[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")) {
            throw new IllegalArgumentException("workerId has an invalid format");
        }
    }

    ReliableTaskProperties.Channel channelProperties(
            ReliableTaskChannel channel) {
        return switch (channel) {
            case IOT_DEVICE -> properties.getIotDevice();
            case FUNDS_WECHAT -> properties.getFundsWechat();
            case MAINTENANCE -> properties.getMaintenance();
        };
    }
}
