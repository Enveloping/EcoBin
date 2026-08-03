package org.enveloping.ecobin.funds.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import org.enveloping.ecobin.funds.application.withdrawal.WithdrawalApplicationService;
import org.springframework.stereotype.Service;

import java.util.Set;

@Service
public class FundsReliableTaskExecutorService
        implements ReliableFundsTaskExecutorPort {

    private static final Set<String> RECHARGE_TASKS = Set.of(
            "CREATE_NATIVE_PAYMENT",
            "QUERY_NATIVE_PAYMENT",
            "POST_RECHARGE_NET_AMOUNT");

    private final RechargeApplicationService recharge;
    private final WithdrawalApplicationService withdrawal;

    public FundsReliableTaskExecutorService(
            RechargeApplicationService recharge,
            WithdrawalApplicationService withdrawal) {
        this.recharge = recharge;
        this.withdrawal = withdrawal;
    }

    @Override
    public Result execute(Command command) {
        return RECHARGE_TASKS.contains(command.taskType())
                ? recharge.executeTask(command)
                : withdrawal.executeTask(command);
    }
}
