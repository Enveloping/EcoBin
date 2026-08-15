package org.enveloping.ecobin.identity.application.platformminiapp;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

final class InMemoryPlatformMiniappRepository
        implements PlatformMiniappRepository {

    FactoryOperatorRow operator;
    MiniappChannelRow channel;
    final Map<String, WechatSubjectRow> subjects = new LinkedHashMap<>();
    final Map<String, BindingIntentRow> intents = new LinkedHashMap<>();
    final List<FactoryMiniappBindingRow> bindings = new ArrayList<>();
    final Map<UUID, SessionData> sessions = new LinkedHashMap<>();
    private long nextSubjectId = 1;
    private long nextIntentId = 1;
    private long nextBindingId = 1;

    @Override
    public Optional<FactoryOperatorRow> findFactoryOperator(
            long factoryOperatorId,
            UUID factoryOperatorUid,
            boolean forUpdate) {
        return operator != null
                && operator.id() == factoryOperatorId
                && operator.uid().equals(factoryOperatorUid)
                ? Optional.of(operator) : Optional.empty();
    }

    @Override
    public Optional<FactoryOperatorRow> findFactoryOperatorById(
            long factoryOperatorId,
            boolean forUpdate) {
        return operator != null && operator.id() == factoryOperatorId
                ? Optional.of(operator) : Optional.empty();
    }

    @Override
    public Optional<FactoryOperatorRow> findFactoryOperatorByUid(
            UUID factoryOperatorUid,
            boolean forUpdate) {
        return operator != null && operator.uid().equals(factoryOperatorUid)
                ? Optional.of(operator) : Optional.empty();
    }

    @Override
    public Optional<MiniappChannelRow> findChannelByAppId(
            String appId,
            boolean forUpdate) {
        return channel != null && channel.appId().equals(appId)
                ? Optional.of(channel) : Optional.empty();
    }

    @Override
    public List<MiniappChannelRow> findEnabledChannels() {
        return channel != null
                && channel.loginEnabled()
                && channel.activatedAt() != null
                ? List.of(channel) : List.of();
    }

    @Override
    public Optional<WechatSubjectRow> findWechatSubject(
            long channelId,
            String openid,
            boolean forUpdate) {
        if (channel == null || channel.id() != channelId) {
            return Optional.empty();
        }
        return Optional.ofNullable(subjects.get(openid));
    }

    @Override
    public void ensureWechatSubject(
            UUID subjectUid,
            long channelId,
            String openid,
            Instant now) {
        subjects.computeIfAbsent(
                openid,
                ignored -> new WechatSubjectRow(
                        nextSubjectId++, subjectUid, "ACTIVE"));
    }

    @Override
    public Optional<BindingIntentRow> findBindingIntent(
            byte[] bindingTokenSha256,
            boolean forUpdate) {
        return Optional.ofNullable(intents.get(key(bindingTokenSha256)));
    }

    @Override
    public int expirePendingBindingIntents(
            long factoryOperatorId,
            Instant now) {
        return replaceIntents(factoryOperatorId, row ->
                "PENDING".equals(row.status())
                        && !row.expiresAt().isAfter(now)
                        ? copyIntent(row, "EXPIRED", null, null)
                        : row);
    }

    @Override
    public int cancelPendingBindingIntents(long factoryOperatorId) {
        return replaceIntents(factoryOperatorId, row ->
                "PENDING".equals(row.status())
                        ? copyIntent(row, "CANCELLED", null, null)
                        : row);
    }

    private int replaceIntents(
            long factoryOperatorId,
            java.util.function.UnaryOperator<BindingIntentRow> replacement) {
        int changed = 0;
        for (var entry : new ArrayList<>(intents.entrySet())) {
            BindingIntentRow before = entry.getValue();
            if (before.factoryOperatorId() != factoryOperatorId) continue;
            BindingIntentRow after = replacement.apply(before);
            if (after != before) {
                intents.put(entry.getKey(), after);
                changed++;
            }
        }
        return changed;
    }

    @Override
    public void insertBindingIntent(
            UUID bindingIntentUid,
            long factoryOperatorId,
            long channelId,
            long createdByPlatformAdminId,
            byte[] bindingTokenSha256,
            Instant expiresAt,
            Instant createdAt) {
        intents.put(key(bindingTokenSha256), new BindingIntentRow(
                nextIntentId++,
                bindingIntentUid,
                factoryOperatorId,
                channelId,
                "PENDING",
                expiresAt,
                null,
                null));
    }

    @Override
    public int consumeBindingIntent(
            long intentId,
            long wechatSubjectId,
            Instant consumedAt) {
        for (var entry : new ArrayList<>(intents.entrySet())) {
            BindingIntentRow row = entry.getValue();
            if (row.id() == intentId
                    && "PENDING".equals(row.status())
                    && row.expiresAt().isAfter(consumedAt)) {
                intents.put(entry.getKey(), copyIntent(
                        row, "CONSUMED", consumedAt, wechatSubjectId));
                return 1;
            }
        }
        return 0;
    }

    @Override
    public Optional<FactoryMiniappBindingRow> findActiveBindingByOperator(
            long factoryOperatorId,
            boolean forUpdate) {
        return bindings.stream()
                .filter(row -> row.factoryOperatorId() == factoryOperatorId)
                .filter(row -> "ACTIVE".equals(row.status()))
                .findFirst();
    }

    @Override
    public Optional<FactoryMiniappBindingRow> findActiveBindingBySubject(
            long channelId,
            long wechatSubjectId,
            boolean forUpdate) {
        return bindings.stream()
                .filter(row -> row.channelId() == channelId)
                .filter(row -> row.wechatSubjectId() == wechatSubjectId)
                .filter(row -> "ACTIVE".equals(row.status()))
                .findFirst();
    }

    @Override
    public void insertBinding(
            UUID bindingUid,
            long factoryOperatorId,
            long channelId,
            long wechatSubjectId,
            Instant now) {
        bindings.add(new FactoryMiniappBindingRow(
                nextBindingId++,
                bindingUid,
                factoryOperatorId,
                channelId,
                wechatSubjectId,
                "ACTIVE",
                now,
                null));
    }

    @Override
    public void insertSession(
            UUID sessionUid,
            long factoryOperatorId,
            long bindingId,
            long channelId,
            long wechatSubjectId,
            Instant issuedAt,
            Instant expiresAt,
            long authVersionSnapshot) {
        sessions.put(sessionUid, new SessionData(
                sessionUid,
                factoryOperatorId,
                bindingId,
                channelId,
                wechatSubjectId,
                issuedAt,
                expiresAt,
                null,
                authVersionSnapshot));
    }

    @Override
    public Optional<FactoryMiniappSessionRow> findSession(UUID sessionUid) {
        SessionData session = sessions.get(sessionUid);
        if (session == null || operator == null || channel == null) {
            return Optional.empty();
        }
        FactoryMiniappBindingRow binding = bindings.stream()
                .filter(row -> row.id() == session.bindingId())
                .findFirst().orElse(null);
        WechatSubjectRow subject = subjects.values().stream()
                .filter(row -> row.id() == session.subjectId())
                .findFirst().orElse(null);
        if (binding == null || subject == null) return Optional.empty();
        return Optional.of(new FactoryMiniappSessionRow(
                session.uid(),
                session.issuedAt(),
                session.expiresAt(),
                session.revokedAt(),
                session.authVersionSnapshot(),
                operator.id(),
                operator.uid(),
                operator.operatorCode(),
                operator.displayName(),
                operator.enabled(),
                operator.authVersion(),
                binding.status(),
                binding.revokedAt(),
                channel.loginEnabled(),
                channel.activatedAt(),
                subject.status()));
    }

    @Override
    public int revokeSession(
            UUID sessionUid,
            long factoryOperatorId,
            Instant revokedAt,
            String reason) {
        SessionData row = sessions.get(sessionUid);
        if (row == null
                || row.factoryOperatorId() != factoryOperatorId
                || row.revokedAt() != null) return 0;
        sessions.put(sessionUid, row.revokedAt(revokedAt));
        return 1;
    }

    @Override
    public int revokeActiveSessions(
            long factoryOperatorId,
            Instant revokedAt,
            String reason) {
        int changed = 0;
        for (var entry : new ArrayList<>(sessions.entrySet())) {
            SessionData row = entry.getValue();
            if (row.factoryOperatorId() == factoryOperatorId
                    && row.revokedAt() == null) {
                sessions.put(entry.getKey(), row.revokedAt(revokedAt));
                changed++;
            }
        }
        return changed;
    }

    @Override
    public int revokeActiveBinding(
            long factoryOperatorId,
            Instant revokedAt,
            String reason) {
        int changed = 0;
        for (int index = 0; index < bindings.size(); index++) {
            FactoryMiniappBindingRow row = bindings.get(index);
            if (row.factoryOperatorId() == factoryOperatorId
                    && "ACTIVE".equals(row.status())) {
                bindings.set(index, new FactoryMiniappBindingRow(
                        row.id(), row.uid(), row.factoryOperatorId(),
                        row.channelId(), row.wechatSubjectId(), "REVOKED",
                        row.boundAt(), revokedAt));
                changed++;
            }
        }
        return changed;
    }

    private static BindingIntentRow copyIntent(
            BindingIntentRow row,
            String status,
            Instant consumedAt,
            Long consumedSubjectId) {
        return new BindingIntentRow(
                row.id(), row.uid(), row.factoryOperatorId(), row.channelId(),
                status, row.expiresAt(), consumedAt, consumedSubjectId);
    }

    private static String key(byte[] bytes) {
        return HexFormat.of().formatHex(bytes);
    }

    record SessionData(
            UUID uid,
            long factoryOperatorId,
            long bindingId,
            long channelId,
            long subjectId,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authVersionSnapshot) {

        SessionData revokedAt(Instant value) {
            return new SessionData(
                    uid, factoryOperatorId, bindingId, channelId, subjectId,
                    issuedAt, expiresAt, value, authVersionSnapshot);
        }
    }
}
