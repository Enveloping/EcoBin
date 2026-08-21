package org.enveloping.ecobin.funds.application.walletquery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.query.PersonalWalletEntryAudience;
import org.enveloping.ecobin.funds.api.result.WalletEntryPage;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WalletQueryServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 13L;
    private static final long USER_ID = 17L;
    private static final long WALLET_ID = 19L;
    private static final UUID USER_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final Instant NOW =
            Instant.parse("2026-08-21T10:00:00Z");

    @Mock
    private WalletReadRepository repository;

    @Mock
    private DeliveryWalletQueryOwnerRef ordinaryOwner;

    @Mock
    private DeliveryWalletQueryOwnerRef auditOwner;

    private WalletQueryService service;

    @BeforeEach
    void setUp() {
        ObjectMapper objectMapper = JsonMapper.builder()
                .findAndAddModules()
                .build();
        Clock clock = Clock.fixed(NOW, ZoneOffset.UTC);
        service = new WalletQueryService(
                repository,
                new WalletCursorCodec(
                        objectMapper,
                        "wallet-query-test-signing-secret-32-bytes",
                        clock),
                objectMapper,
                clock);
        bind(ordinaryOwner);
        bind(auditOwner);
        when(repository.findWallet(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID))
                .thenReturn(Optional.of(
                        new WalletReadRepository.WalletRow(
                                WALLET_ID,
                                1000,
                                200,
                                3)));
    }

    @Test
    void personalCursorCannotCrossOrdinaryAndAuditViews() {
        when(repository.findPersonalEntries(
                TENANT_ID,
                ORGANIZATION_ID,
                WALLET_ID,
                PersonalWalletEntryAudience.ORDINARY_USER,
                null,
                2))
                .thenReturn(List.of(entry(5), entry(3)));

        WalletEntryPage first = service.personalEntries(
                ordinaryOwner,
                PersonalWalletEntryAudience.ORDINARY_USER,
                null,
                1);

        assertThat(first.items())
                .extracting(item -> item.entrySequenceNo())
                .containsExactly(5L);
        assertThat(first.nextCursor()).isNotBlank();
        assertThatThrownBy(() -> service.personalEntries(
                auditOwner,
                PersonalWalletEntryAudience.AUDIT,
                first.nextCursor(),
                1))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo("COMMON.INVALID_CURSOR"));
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static void bind(DeliveryWalletQueryOwnerRef ownerRef) {
        when(ownerRef.withWalletOwnerOnce(any())).thenAnswer(invocation -> {
            DeliveryWalletQueryOwnerRef.WalletQualificationOwnerFunction
                    function = invocation.getArgument(0);
            return function.apply(
                    TENANT_ID,
                    ORGANIZATION_ID,
                    USER_ID,
                    USER_UID);
        });
    }

    private static WalletReadRepository.EntryRow entry(long sequence) {
        return new WalletReadRepository.EntryRow(
                UUID.randomUUID(),
                USER_UID,
                sequence,
                "DELIVERY_INITIAL_REVIEW",
                100,
                0,
                1000,
                200,
                "DELIVERY_ORDER",
                "DO-" + sequence,
                LocalDateTime.ofInstant(NOW, ZoneOffset.UTC));
    }
}
