package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.WechatMiniProgramCodePort;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappBindingTokenService.IssuedToken;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.FactoryOperatorRow;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.MiniappChannelRow;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryBindingIntentCreated;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Map;
import java.util.UUID;

@Service
public class PlatformMiniappBindingIntentService {

    static final Duration INTENT_DURATION = Duration.ofMinutes(5);
    private static final String ACTION =
            "identity.factory-operator.binding-intent.create";
    private static final String BIND_PAGE = "factory/pages/bind/bind";

    private final PlatformMiniappRepository repository;
    private final PlatformMiniappBindingTokenService tokens;
    private final WechatMiniProgramCodePort miniProgramCodes;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;
    private final TransactionTemplate transactions;

    PlatformMiniappBindingIntentService(
            PlatformMiniappRepository repository,
            PlatformMiniappBindingTokenService tokens,
            WechatMiniProgramCodePort miniProgramCodes,
            AuditPort auditPort,
            ObjectMapper objectMapper,
            PlatformTransactionManager transactionManager) {
        this.repository = repository;
        this.tokens = tokens;
        this.miniProgramCodes = miniProgramCodes;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
        this.transactions = new TransactionTemplate(transactionManager);
    }

    public FactoryBindingIntentCreated create(UUID factoryOperatorUid) {
        TargetWebActor actor = requirePlatformActor();
        TargetWebAuditRequestContext.describe(
                ACTION,
                "factory-operator:" + factoryOperatorUid);
        PreparedIntent prepared = transactions.execute(status ->
                prepare(actor, factoryOperatorUid));
        if (prepared == null) {
            throw new IllegalStateException("binding intent was not prepared");
        }
        byte[] image = miniProgramCodes.generate(
                prepared.channel().appId(),
                prepared.channel().appSecret(),
                BIND_PAGE,
                prepared.token().value());
        if (image == null || image.length == 0) {
            throw new TargetApiException(
                    503,
                    "WECHAT.MINI_PROGRAM_CODE_UNAVAILABLE",
                    "微信小程序码生成服务暂不可用",
                    true,
                    Map.of());
        }
        return new FactoryBindingIntentCreated(
                prepared.intentUid(),
                prepared.expiresAt(),
                "data:image/png;base64,"
                        + Base64.getEncoder().encodeToString(image));
    }

    private PreparedIntent prepare(
            TargetWebActor actor,
            UUID factoryOperatorUid) {
        FactoryOperatorRow operator = repository.findFactoryOperatorByUid(
                        factoryOperatorUid, true)
                .orElseThrow(() -> new TargetApiException(
                        404,
                        "RESOURCE.NOT_FOUND",
                        "厂家操作员不存在"));
        if (!operator.enabled()) {
            throw conflict(
                    "IDENTITY.FACTORY_OPERATOR_DISABLED",
                    "厂家操作员已停用，不能生成绑定码");
        }
        if (repository.findActiveBindingByOperator(operator.id(), true)
                .isPresent()) {
            throw conflict(
                    "IDENTITY.FACTORY_OPERATOR_ALREADY_BOUND",
                    "厂家操作员已经绑定微信，请先解绑");
        }
        var enabledChannels = repository.findEnabledChannels();
        if (enabledChannels.size() != 1) {
            throw conflict(
                    "IDENTITY.FACTORY_MINIAPP_CHANNEL_AMBIGUOUS",
                    "必须且只能启用一个包含 AppSecret 的共享小程序渠道");
        }
        MiniappChannelRow channel = enabledChannels.getFirst();
        Instant createdAt = Instant.now().truncatedTo(ChronoUnit.MILLIS);
        Instant expiresAt = createdAt.plus(INTENT_DURATION);
        repository.expirePendingBindingIntents(operator.id(), createdAt);
        repository.cancelPendingBindingIntents(operator.id());
        IssuedToken token = tokens.issue();
        UUID intentUid = UUID.randomUUID();
        repository.insertBindingIntent(
                intentUid,
                operator.id(),
                channel.id(),
                actor.principalId(),
                token.sha256(),
                expiresAt,
                createdAt);
        appendAudit(actor, operator, intentUid, expiresAt, createdAt);
        return new PreparedIntent(
                intentUid, expiresAt, channel, token);
    }

    private void appendAudit(
            TargetWebActor actor,
            FactoryOperatorRow operator,
            UUID intentUid,
            Instant expiresAt,
            Instant occurredAt) {
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                UUID.randomUUID(),
                AuditScopeKind.PLATFORM,
                null,
                null,
                AuditActorKind.PLATFORM_ADMIN,
                actor.principalId(),
                null,
                null,
                null,
                actor.displayName(),
                ACTION,
                "factory-operator-binding-intent",
                intentUid.toString(),
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                null,
                objectMapper.writeValueAsString(Map.of(
                        "factoryOperatorUid", operator.uid(),
                        "expiresAt", expiresAt,
                        "bindingTokenReturned", false)),
                occurredAt));
    }

    private static TargetWebActor requirePlatformActor() {
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "仅平台管理员可以管理厂家操作员");
        }
        return actor;
    }

    private static TargetApiException conflict(
            String code,
            String message) {
        return new TargetApiException(409, code, message);
    }

    private record PreparedIntent(
            UUID intentUid,
            Instant expiresAt,
            MiniappChannelRow channel,
            IssuedToken token) {
    }
}
