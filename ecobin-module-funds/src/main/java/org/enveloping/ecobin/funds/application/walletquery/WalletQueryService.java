package org.enveloping.ecobin.funds.application.walletquery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.port.WalletQueryPort;
import org.enveloping.ecobin.funds.api.query.WalletEntryFilter;
import org.enveloping.ecobin.funds.api.result.WalletBalanceSnapshot;
import org.enveloping.ecobin.funds.api.result.WalletEntryItem;
import org.enveloping.ecobin.funds.api.result.WalletEntryPage;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.WalletQueryScopeRef;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

@Service
public class WalletQueryService implements WalletQueryPort {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;
    private static final Set<String> ENTRY_TYPES = Set.of(
            "DELIVERY_INITIAL_REVIEW",
            "DELIVERY_CORRECTION",
            "WITHDRAWAL_FREEZE",
            "WITHDRAWAL_SUCCEEDED",
            "WITHDRAWAL_RELEASED",
            "MANUAL_ADJUSTMENT");

    private final WalletReadRepository repository;
    private final WalletCursorCodec cursorCodec;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public WalletQueryService(
            WalletReadRepository repository,
            WalletCursorCodec cursorCodec,
            ObjectMapper objectMapper) {
        this(repository, cursorCodec, objectMapper, Clock.systemUTC());
    }

    WalletQueryService(
            WalletReadRepository repository,
            WalletCursorCodec cursorCodec,
            ObjectMapper objectMapper,
            Clock clock) {
        this.repository = repository;
        this.cursorCodec = cursorCodec;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public WalletBalanceSnapshot balance(
            DeliveryWalletQueryOwnerRef ownerRef) {
        Objects.requireNonNull(ownerRef, "ownerRef");
        return ownerRef.withWalletOwnerOnce(
                (tenantId, organizationId, userId, userUid) -> {
                    WalletReadRepository.WalletRow wallet =
                            requiredWallet(
                                    tenantId,
                                    organizationId,
                                    userId);
                    return new WalletBalanceSnapshot(
                            new OrganizationUserUid(userUid),
                            wallet.lockVersion(),
                            wallet.availableBalanceCent(),
                            wallet.frozenWithdrawalCent());
                });
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public WalletEntryPage personalEntries(
            DeliveryWalletQueryOwnerRef ownerRef,
            String cursor,
            Integer limit) {
        Objects.requireNonNull(ownerRef, "ownerRef");
        int pageSize = normalizeLimit(limit);
        return ownerRef.withWalletOwnerOnce(
                (tenantId, organizationId, userId, userUid) -> {
                    WalletReadRepository.WalletRow wallet =
                            requiredWallet(
                                    tenantId,
                                    organizationId,
                                    userId);
                    String fingerprint = fingerprint(Map.of(
                            "mode", "PERSONAL",
                            "tenant", tenantId,
                            "organization", organizationId,
                            "userUid", userUid.toString()));
                    WalletCursorCodec.DecodedPersonal decoded =
                            blank(cursor)
                                    ? null
                                    : cursorCodec.decodePersonal(
                                    cursor,
                                    fingerprint);
                    Instant asOf = decoded == null
                            ? observedAt()
                            : decoded.asOf();
                    List<WalletReadRepository.EntryRow> rows =
                            repository.findPersonalEntries(
                                    tenantId,
                                    organizationId,
                                    wallet.id(),
                                    decoded == null
                                            ? null
                                            : decoded.lastSequence(),
                                    pageSize + 1);
                    return page(
                            rows,
                            pageSize,
                            asOf,
                            last -> cursorCodec.encodePersonal(
                                    asOf,
                                    last.entrySequenceNo(),
                                    fingerprint));
                });
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public WalletEntryPage organizationEntries(
            WalletQueryScopeRef scopeRef,
            WalletEntryFilter requestedFilter,
            String cursor,
            Integer limit) {
        Objects.requireNonNull(scopeRef, "scopeRef");
        WalletEntryFilter filter = normalizeFilter(requestedFilter);
        int pageSize = normalizeLimit(limit);
        return scopeRef.withWalletScopeOnce(
                (tenantId, organizationId, userId, userUid) -> {
                    String fingerprint = fingerprint(Map.of(
                            "mode", "ORGANIZATION",
                            "tenant", tenantId,
                            "organization", organizationId,
                            "userUid", nullMarker(userUid),
                            "entryType", nullMarker(filter.entryType()),
                            "occurredFrom",
                            nullMarker(filter.occurredFrom()),
                            "occurredTo",
                            nullMarker(filter.occurredTo()),
                            "sourceNo", nullMarker(filter.sourceNo())));
                    WalletCursorCodec.DecodedOrganization decoded =
                            blank(cursor)
                                    ? null
                                    : cursorCodec.decodeOrganization(
                                    cursor,
                                    fingerprint);
                    long highWatermark = decoded == null
                            ? repository.currentOrganizationHighWatermark(
                                    tenantId,
                                    organizationId)
                            : decoded.highWatermark();
                    Instant asOf = decoded == null
                            ? observedAt()
                            : decoded.asOf();
                    List<WalletReadRepository.EntryRow> rows =
                            repository.findOrganizationEntries(
                                    new WalletReadRepository
                                            .OrganizationPageQuery(
                                            tenantId,
                                            organizationId,
                                            userId,
                                            filter.entryType(),
                                            databaseTime(
                                                    filter.occurredFrom()),
                                            databaseTime(
                                                    filter.occurredTo()),
                                            filter.sourceNo(),
                                            highWatermark,
                                            decoded == null
                                                    ? null
                                                    : decoded.lastOccurredAt(),
                                            decoded == null
                                                    ? null
                                                    : decoded
                                                            .lastOrganizationUserUid(),
                                            decoded == null
                                                    ? null
                                                    : decoded.lastSequence(),
                                            pageSize + 1));
                    return page(
                            rows,
                            pageSize,
                            asOf,
                            last -> cursorCodec.encodeOrganization(
                                    asOf,
                                    highWatermark,
                                    last.occurredAt(),
                                    last.organizationUserUid(),
                                    last.entrySequenceNo(),
                                    fingerprint));
                });
    }

    private WalletReadRepository.WalletRow requiredWallet(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        return repository.findWallet(
                        tenantId,
                        organizationId,
                        organizationUserId)
                .orElseThrow(() -> new TargetApiException(
                        500,
                        "WALLET.NOT_INITIALIZED",
                        "机构用户钱包未初始化"));
    }

    private WalletEntryPage page(
            List<WalletReadRepository.EntryRow> rows,
            int pageSize,
            Instant asOf,
            CursorEncoder encoder) {
        boolean hasMore = rows.size() > pageSize;
        List<WalletReadRepository.EntryRow> included = hasMore
                ? rows.subList(0, pageSize)
                : rows;
        String nextCursor = hasMore
                ? encoder.encode(included.getLast())
                : null;
        return new WalletEntryPage(
                included.stream().map(this::entry).toList(),
                asOf,
                nextCursor);
    }

    private WalletEntryItem entry(
            WalletReadRepository.EntryRow row) {
        return new WalletEntryItem(
                row.entryUid(),
                new OrganizationUserUid(
                        row.organizationUserUid()),
                row.entrySequenceNo(),
                row.eventType(),
                row.availableDeltaCent(),
                row.frozenDeltaCent(),
                row.availableAfterCent(),
                row.frozenAfterCent(),
                row.sourceType(),
                row.sourceNo(),
                row.occurredAt().toInstant(ZoneOffset.UTC));
    }

    private WalletEntryFilter normalizeFilter(
            WalletEntryFilter requested) {
        WalletEntryFilter filter = requested == null
                ? new WalletEntryFilter(null, null, null, null)
                : requested;
        String entryType = trimmed(filter.entryType());
        if (entryType != null && !ENTRY_TYPES.contains(entryType)) {
            throw validation(
                    "entryType",
                    "entryType 不是受支持的钱包流水类型");
        }
        if (filter.occurredFrom() != null
                && filter.occurredTo() != null
                && !filter.occurredFrom()
                        .isBefore(filter.occurredTo())) {
            throw validation(
                    "occurredTo",
                    "occurredTo 必须晚于 occurredFrom");
        }
        String sourceNo = trimmed(filter.sourceNo());
        if (sourceNo != null && sourceNo.length() > 64) {
            throw validation(
                    "sourceNo",
                    "sourceNo 长度不能超过 64");
        }
        return new WalletEntryFilter(
                entryType,
                filter.occurredFrom(),
                filter.occurredTo(),
                sourceNo);
    }

    private String fingerprint(Map<String, ?> fields) {
        try {
            byte[] bytes = objectMapper.writeValueAsBytes(fields);
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(bytes));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "wallet entry filters cannot be encoded",
                    exception);
        }
    }

    private static int normalizeLimit(Integer requested) {
        int limit = requested == null ? DEFAULT_LIMIT : requested;
        if (limit < 1 || limit > MAX_LIMIT) {
            throw validation(
                    "limit",
                    "limit 必须在 1 到 100 之间");
        }
        return limit;
    }

    private Instant observedAt() {
        return clock.instant().truncatedTo(ChronoUnit.MILLIS);
    }

    private static LocalDateTime databaseTime(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static boolean blank(String value) {
        return value == null || value.isBlank();
    }

    private static String trimmed(String value) {
        return blank(value) ? null : value.trim();
    }

    private static Object nullMarker(Object value) {
        return value == null ? "<null>" : value;
    }

    private static TargetApiException validation(
            String field,
            String message) {
        return new TargetApiException(
                400,
                "COMMON.VALIDATION_FAILED",
                "钱包流水查询参数不符合接口契约",
                false,
                Map.of(field, message));
    }

    @FunctionalInterface
    private interface CursorEncoder {

        String encode(WalletReadRepository.EntryRow row);
    }
}
