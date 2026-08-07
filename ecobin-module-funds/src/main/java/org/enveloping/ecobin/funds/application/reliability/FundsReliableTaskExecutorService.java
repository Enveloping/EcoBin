package org.enveloping.ecobin.funds.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import org.enveloping.ecobin.funds.application.authorization.MerchantTransferAuthorizationApplicationService;
import org.enveloping.ecobin.funds.application.withdrawal.WithdrawalApplicationService;
import org.springframework.stereotype.Service;

import java.util.Set;

@Service
public class FundsReliableTaskExecutorService
        implements ReliableFundsTaskExecutorPort {

    private static final Set<String> RECHARGE_TASKS = Set.of(
            "CREATE_NATIVE_PAYMENT",
            "QUERY_NATIVE_PAYMENT",
            "CLOSE_NATIVE_PAYMENT",
            "POST_RECHARGE_NET_AMOUNT");
    private static final Set<String> AUTHORIZATION_TASKS = Set.of(
            MerchantTransferAuthorizationApplicationService.CREATE_TASK,
            MerchantTransferAuthorizationApplicationService.QUERY_TASK);

    private final RechargeApplicationService recharge;
    private final WithdrawalApplicationService withdrawal;
    private final MerchantTransferAuthorizationApplicationService authorization;

    public FundsReliableTaskExecutorService(
            RechargeApplicationService recharge,
            WithdrawalApplicationService withdrawal,
            MerchantTransferAuthorizationApplicationService authorization) {
        this.recharge = recharge;
        this.withdrawal = withdrawal;
        this.authorization = authorization;
    }

    @Override
    public Result execute(Command command) {
        if (RECHARGE_TASKS.contains(command.taskType())) {
            return recharge.executeTask(command);
        }
        if (AUTHORIZATION_TASKS.contains(command.taskType())) {
            return authorization.executeTask(command);
        }
        return withdrawal.executeTask(command);
    }
}
