package org.enveloping.ecobin.integration.config;

import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetSubscriptionProperties;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.integration.wechat.WechatConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.InitializingBean;
import org.springframework.boot.health.contributor.Health;
import org.springframework.boot.health.contributor.HealthIndicator;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.stereotype.Component;
import org.springframework.util.ClassUtils;

/**
 * 在任何 SmartLifecycle 外联入口启动前校验 Fake/真实配置边界。
 */
@Component("externalBoundaryHealthIndicator")
public final class ExternalBoundaryGuard
        implements InitializingBean, HealthIndicator {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(ExternalBoundaryGuard.class);

    private final ExternalAdapterModeProperties modeProperties;
    private final OneNetSubscriptionProperties subscriptionProperties;
    private final OneNetProperties oneNetProperties;
    private final CosProperties cosProperties;
    private final WechatConfig wechatConfig;
    private final Environment environment;

    public ExternalBoundaryGuard(
            ExternalAdapterModeProperties modeProperties,
            OneNetSubscriptionProperties subscriptionProperties,
            OneNetProperties oneNetProperties,
            CosProperties cosProperties,
            WechatConfig wechatConfig,
            Environment environment) {
        this.modeProperties = modeProperties;
        this.subscriptionProperties = subscriptionProperties;
        this.oneNetProperties = oneNetProperties;
        this.cosProperties = cosProperties;
        this.wechatConfig = wechatConfig;
        this.environment = environment;
    }

    @Override
    public void afterPropertiesSet() {
        ExternalBoundaryPolicy.Verification verification = verify();
        LOGGER.info(
                "External adapter boundary accepted mode={} inboundBlocked={}",
                verification.mode(),
                verification.inboundBlocked());
    }

    @Override
    public Health health() {
        try {
            ExternalBoundaryPolicy.Verification verification = verify();
            return Health.up()
                    .withDetail("mode", verification.mode().name())
                    .withDetail("inboundBlocked", verification.inboundBlocked())
                    .build();
        } catch (ExternalBoundaryException exception) {
            return Health.down()
                    .withDetail("reason", exception.getMessage())
                    .build();
        }
    }

    private ExternalBoundaryPolicy.Verification verify() {
        boolean fakeMode =
                modeProperties.getMode() == ExternalAdapterModeProperties.Mode.FAKE;
        return ExternalBoundaryPolicy.verify(new ExternalBoundaryPolicy.Snapshot(
                modeProperties.getMode(),
                modeProperties.getFake().isBlockInbound(),
                isLegacyTestRuntime(),
                subscriptionProperties.isEnabled(),
                fakeMode
                        ? hasAnyText(
                                subscriptionProperties.getAccessId(),
                                subscriptionProperties.getSecretKey(),
                                subscriptionProperties.getSubscriptionName())
                        : subscriptionProperties.isConfigured(),
                fakeMode
                        ? hasAnyText(
                                oneNetProperties.getProductId(),
                                oneNetProperties.getAccessKey())
                        : oneNetProperties.isConfigured(),
                fakeMode
                        ? hasAnyText(
                                cosProperties.getSecretId(),
                                cosProperties.getSecretKey(),
                                cosProperties.getRegion(),
                                cosProperties.getBucketName(),
                                cosProperties.getBaseUrl())
                        : cosProperties.isConfigured()
                                && hasText(cosProperties.getBaseUrl()),
                fakeMode
                        ? hasAnyText(
                                wechatConfig.getAppid(),
                                wechatConfig.getSecret())
                        : wechatConfig.isConfigured()));
    }

    private boolean isLegacyTestRuntime() {
        return environment.acceptsProfiles(Profiles.of("test"))
                && ClassUtils.isPresent(
                        "org.junit.jupiter.api.Test",
                        getClass().getClassLoader());
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }

    private static boolean hasAnyText(String... values) {
        for (String value : values) {
            if (hasText(value)) {
                return true;
            }
        }
        return false;
    }
}
