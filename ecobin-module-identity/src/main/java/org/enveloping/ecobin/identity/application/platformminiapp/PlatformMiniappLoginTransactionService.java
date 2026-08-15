package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.FactoryMiniappAuthorizationPort;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.BindingIntentRow;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.FactoryMiniappBindingRow;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.FactoryOperatorRow;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.MiniappChannelRow;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.WechatSubjectRow;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappSessionCreated;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.UUID;

@Service
public class PlatformMiniappLoginTransactionService {

    static final String AUDIENCE = "miniapp-factory";
    static final String ENTRY_MODE = "FACTORY_ACCEPTANCE";
    static final List<String> CAPABILITIES = List.of(
            FactoryMiniappAuthorizationPort.ACCEPTANCE_READ,
            FactoryMiniappAuthorizationPort.BAG_INSTALL,
            FactoryMiniappAuthorizationPort.BAG_CORRECT);

    private final PlatformMiniappRepository repository;
    private final PlatformMiniappBindingTokenService tokens;
    private final JwtTokenProvider tokenProvider;
    private final Duration sessionDuration;

    PlatformMiniappLoginTransactionService(
            PlatformMiniappRepository repository,
            PlatformMiniappBindingTokenService tokens,
            JwtTokenProvider tokenProvider,
            @Value("${ecobin.identity.factory-miniapp-session-duration:PT2H}")
            Duration sessionDuration) {
        if (sessionDuration == null
                || sessionDuration.isZero()
                || sessionDuration.isNegative()) {
            throw new IllegalArgumentException(
                    "factory miniapp session duration must be positive");
        }
        this.repository = repository;
        this.tokens = tokens;
        this.tokenProvider = tokenProvider;
        this.sessionDuration = sessionDuration;
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            rollbackFor = Exception.class)
    public FactoryMiniappSessionCreated completeLogin(
            String appId,
            String openid,
            String bindingToken) {
        MiniappChannelRow channel = repository.findChannelByAppId(
                        appId, true)
                .orElseThrow(
                        PlatformMiniappLoginTransactionService::loginDisabled);
        requireEnabled(channel);
        Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
        repository.ensureWechatSubject(
                UUID.randomUUID(), channel.id(), openid, now);
        WechatSubjectRow subject = repository.findWechatSubject(
                        channel.id(), openid, true)
                .orElseThrow(() -> new IllegalStateException(
                        "ensured WeChat subject is unavailable"));
        if (!"ACTIVE".equals(subject.status())) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.WECHAT_SUBJECT_FROZEN",
                    "当前微信身份已冻结");
        }

        FactoryMiniappBindingRow binding = repository
                .findActiveBindingBySubject(
                        channel.id(), subject.id(), true)
                .orElse(null);
        FactoryOperatorRow operator;
        boolean newlyBound = false;
        if (binding != null) {
            operator = requireOperator(binding.factoryOperatorId(), true);
        } else {
            String rawToken = bindingToken == null || bindingToken.isBlank()
                    ? null : bindingToken;
            if (rawToken == null) {
                throw bindingRequired();
            }
            byte[] tokenDigest = tokens.digest(rawToken);
            BindingIntentRow intent = repository.findBindingIntent(
                            tokenDigest, true)
                    .orElseThrow(
                            PlatformMiniappLoginTransactionService
                                    ::bindingTokenInvalid);
            if (intent.channelId() != channel.id()) {
                throw bindingTokenInvalid();
            }
            operator = requireOperator(intent.factoryOperatorId(), true);

            if ("CONSUMED".equals(intent.status())) {
                if (intent.consumedWechatSubjectId() == null
                        || intent.consumedWechatSubjectId() != subject.id()) {
                    throw bindingTokenInvalid();
                }
                binding = repository.findActiveBindingByOperator(
                                operator.id(), true)
                        .filter(candidate ->
                                candidate.channelId() == channel.id()
                                        && candidate.wechatSubjectId()
                                        == subject.id())
                        .orElseThrow(
                                PlatformMiniappLoginTransactionService
                                        ::bindingInvalid);
            } else {
                requireUsable(intent, now);
                if (repository.findActiveBindingByOperator(
                                operator.id(), true)
                        .isPresent()) {
                    throw new TargetApiException(
                            409,
                            "IDENTITY.FACTORY_OPERATOR_ALREADY_BOUND",
                            "该厂家操作员已经绑定其他微信身份");
                }
                if (repository.findActiveBindingBySubject(
                                channel.id(), subject.id(), true)
                        .isPresent()) {
                    throw new TargetApiException(
                            409,
                            "IDENTITY.FACTORY_SUBJECT_ALREADY_BOUND",
                            "当前微信身份已经绑定其他厂家操作员");
                }
                try {
                    repository.insertBinding(
                            UUID.randomUUID(),
                            operator.id(),
                            channel.id(),
                            subject.id(),
                            now);
                } catch (DataIntegrityViolationException conflict) {
                    throw new TargetApiException(
                            409,
                            "IDENTITY.FACTORY_BINDING_CONFLICT",
                            "厂家操作员或微信身份已经被绑定");
                }
                if (repository.consumeBindingIntent(
                                intent.id(), subject.id(), now) != 1) {
                    throw bindingTokenInvalid();
                }
                binding = repository.findActiveBindingByOperator(
                                operator.id(), false)
                        .orElseThrow(() -> new IllegalStateException(
                                "created factory binding is unavailable"));
                newlyBound = true;
            }
        }
        return issueSession(
                channel,
                subject,
                binding,
                operator,
                newlyBound,
                now);
    }

    private FactoryOperatorRow requireOperator(
            long factoryOperatorId,
            boolean forUpdate) {
        FactoryOperatorRow operator = repository.findFactoryOperatorById(
                        factoryOperatorId, forUpdate)
                .orElseThrow(
                        PlatformMiniappLoginTransactionService
                                ::operatorUnavailable);
        if (!operator.enabled()) {
            throw operatorUnavailable();
        }
        return operator;
    }

    private FactoryMiniappSessionCreated issueSession(
            MiniappChannelRow channel,
            WechatSubjectRow subject,
            FactoryMiniappBindingRow binding,
            FactoryOperatorRow operator,
            boolean newlyBound,
            Instant now) {
        Instant issuedAt = now.truncatedTo(ChronoUnit.SECONDS);
        Instant expiresAt = issuedAt.plus(sessionDuration);
        UUID sessionUid = UUID.randomUUID();
        repository.insertSession(
                sessionUid,
                operator.id(),
                binding.id(),
                channel.id(),
                subject.id(),
                issuedAt,
                expiresAt,
                operator.authVersion());
        String token = tokenProvider.generateTargetFactoryMiniappToken(
                operator.uid(), sessionUid, issuedAt, expiresAt);
        return new FactoryMiniappSessionCreated(
                token,
                "Bearer",
                AUDIENCE,
                ENTRY_MODE,
                expiresAt,
                operator.uid(),
                operator.operatorCode(),
                operator.displayName(),
                CAPABILITIES,
                newlyBound);
    }

    private static void requireEnabled(MiniappChannelRow channel) {
        if (!channel.loginEnabled() || channel.activatedAt() == null) {
            throw loginDisabled();
        }
    }

    private static void requireUsable(
            BindingIntentRow intent,
            Instant now) {
        if (!"PENDING".equals(intent.status())
                || intent.consumedAt() != null
                || intent.consumedWechatSubjectId() != null) {
            throw bindingTokenInvalid();
        }
        if (!intent.expiresAt().isAfter(now)) {
            throw new TargetApiException(
                    422,
                    "IDENTITY.FACTORY_BINDING_TOKEN_EXPIRED",
                    "厂家操作员绑定凭证已经过期");
        }
    }

    private static TargetApiException loginDisabled() {
        return new TargetApiException(
                403,
                "IDENTITY.MINIAPP_LOGIN_DISABLED",
                "当前小程序登录入口不可用");
    }

    private static TargetApiException operatorUnavailable() {
        return new TargetApiException(
                403,
                "IDENTITY.FACTORY_OPERATOR_UNAVAILABLE",
                "厂家操作员当前不可用");
    }

    private static TargetApiException bindingRequired() {
        return new TargetApiException(
                422,
                "IDENTITY.FACTORY_BINDING_REQUIRED",
                "首次进入厂家端需要后台生成的一次性绑定码");
    }

    private static TargetApiException bindingTokenInvalid() {
        return new TargetApiException(
                422,
                "IDENTITY.FACTORY_BINDING_TOKEN_INVALID",
                "厂家操作员绑定凭证无效或已经被其他微信使用");
    }

    private static TargetApiException bindingInvalid() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "厂家操作员微信绑定状态已经变化");
    }
}
