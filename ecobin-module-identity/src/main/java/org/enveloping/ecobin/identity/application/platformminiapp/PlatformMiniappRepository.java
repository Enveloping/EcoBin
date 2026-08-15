package org.enveloping.ecobin.identity.application.platformminiapp;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Persistence boundary for the factory-operator mini-program identity.
 * The historical class name is retained inside the module so the unfinished
 * V52 branch can be migrated without touching remote-support code.
 */
interface PlatformMiniappRepository {

    Optional<FactoryOperatorRow> findFactoryOperator(
            long factoryOperatorId,
            UUID factoryOperatorUid,
            boolean forUpdate);

    Optional<FactoryOperatorRow> findFactoryOperatorById(
            long factoryOperatorId,
            boolean forUpdate);

    Optional<FactoryOperatorRow> findFactoryOperatorByUid(
            UUID factoryOperatorUid,
            boolean forUpdate);

    Optional<MiniappChannelRow> findChannelByAppId(
            String appId,
            boolean forUpdate);

    List<MiniappChannelRow> findEnabledChannels();

    Optional<WechatSubjectRow> findWechatSubject(
            long channelId,
            String openid,
            boolean forUpdate);

    void ensureWechatSubject(
            UUID subjectUid,
            long channelId,
            String openid,
            Instant now);

    Optional<BindingIntentRow> findBindingIntent(
            byte[] bindingTokenSha256,
            boolean forUpdate);

    int expirePendingBindingIntents(
            long factoryOperatorId,
            Instant now);

    int cancelPendingBindingIntents(long factoryOperatorId);

    void insertBindingIntent(
            UUID bindingIntentUid,
            long factoryOperatorId,
            long channelId,
            long createdByPlatformAdminId,
            byte[] bindingTokenSha256,
            Instant expiresAt,
            Instant createdAt);

    int consumeBindingIntent(
            long intentId,
            long wechatSubjectId,
            Instant consumedAt);

    Optional<FactoryMiniappBindingRow> findActiveBindingByOperator(
            long factoryOperatorId,
            boolean forUpdate);

    Optional<FactoryMiniappBindingRow> findActiveBindingBySubject(
            long channelId,
            long wechatSubjectId,
            boolean forUpdate);

    void insertBinding(
            UUID bindingUid,
            long factoryOperatorId,
            long channelId,
            long wechatSubjectId,
            Instant now);

    void insertSession(
            UUID sessionUid,
            long factoryOperatorId,
            long bindingId,
            long channelId,
            long wechatSubjectId,
            Instant issuedAt,
            Instant expiresAt,
            long authVersionSnapshot);

    Optional<FactoryMiniappSessionRow> findSession(UUID sessionUid);

    int revokeSession(
            UUID sessionUid,
            long factoryOperatorId,
            Instant revokedAt,
            String reason);

    int revokeActiveSessions(
            long factoryOperatorId,
            Instant revokedAt,
            String reason);

    int revokeActiveBinding(
            long factoryOperatorId,
            Instant revokedAt,
            String reason);

    record FactoryOperatorRow(
            long id,
            UUID uid,
            String operatorCode,
            String displayName,
            boolean enabled,
            long authVersion,
            long lockVersion) {
    }

    record MiniappChannelRow(
            long id,
            String appId,
            String appSecret,
            boolean loginEnabled,
            Instant activatedAt) {
    }

    record WechatSubjectRow(
            long id,
            UUID uid,
            String status) {
    }

    record BindingIntentRow(
            long id,
            UUID uid,
            long factoryOperatorId,
            long channelId,
            String status,
            Instant expiresAt,
            Instant consumedAt,
            Long consumedWechatSubjectId) {
    }

    record FactoryMiniappBindingRow(
            long id,
            UUID uid,
            long factoryOperatorId,
            long channelId,
            long wechatSubjectId,
            String status,
            Instant boundAt,
            Instant revokedAt) {
    }

    record FactoryMiniappSessionRow(
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authVersionSnapshot,
            long factoryOperatorId,
            UUID factoryOperatorUid,
            String operatorCode,
            String displayName,
            boolean factoryOperatorEnabled,
            long factoryOperatorAuthVersion,
            String bindingStatus,
            Instant bindingRevokedAt,
            boolean channelLoginEnabled,
            Instant channelActivatedAt,
            String subjectStatus) {
    }
}
