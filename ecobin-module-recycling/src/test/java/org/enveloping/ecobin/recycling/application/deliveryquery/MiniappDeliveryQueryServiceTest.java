package org.enveloping.ecobin.recycling.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;
import org.enveloping.ecobin.device.api.port.MiniappDeliveryDeviceQueryPort;
import org.enveloping.ecobin.device.api.result.DeliveryDeviceOptionsSnapshot;
import org.enveloping.ecobin.device.api.result.DeliveryDevicePortOptionSnapshot;
import org.enveloping.ecobin.device.api.result.OwnedDeliverySessionSnapshot;
import org.enveloping.ecobin.funds.api.port.DeliveryWalletQualificationQueryPort;
import org.enveloping.ecobin.funds.api.result.DeliveryWalletQualification;
import org.enveloping.ecobin.funds.api.result.DeliveryWalletQualificationBlocker;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.port.MiniappDeliveryIdentityQueryPort;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappDeliveryIdentity;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.UUID;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MiniappDeliveryQueryServiceTest {

    private static final UUID USER_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID LOGIN_SESSION_UID = UUID.fromString(
            "20000000-0000-4000-8000-000000000001");
    private static final UUID DELIVERY_SESSION_UID = UUID.fromString(
            "30000000-0000-4000-8000-000000000001");
    private static final Instant AS_OF =
            Instant.parse("2026-07-29T02:00:00Z");
    private static final Instant STARTED_AT =
            Instant.parse("2026-07-29T02:00:01Z");
    private static final Instant COMPLETED_AT =
            Instant.parse("2026-07-29T02:01:00Z");

    @Mock
    private MiniappDeliveryIdentityQueryPort identity;
    @Mock
    private MiniappDeliveryDeviceQueryPort device;
    @Mock
    private DeliveryBusinessReadPort business;
    @Mock
    private DeliveryWalletQualificationQueryPort wallet;
    @Mock
    private DeliveryQueryOrganizationUserRef userRef;
    @Mock
    private DeliveryWalletQueryOwnerRef walletRef;
    @Mock
    private DeliveryOptionsBusinessQueryRef optionsBusinessRef;
    @Mock
    private DeliverySessionBusinessQueryRef sessionBusinessRef;

    private MiniappDeliveryQueryService service;

    @BeforeEach
    void setUp() {
        service = new MiniappDeliveryQueryService(
                identity,
                device,
                business,
                wallet);
    }

    @Test
    void mergesUserWalletDeviceAndRecyclingBlockersInStableOrder() {
        when(identity.current()).thenReturn(identity(false));
        when(device.deliveryOptions(any())).thenReturn(
                new DeliveryDeviceOptionsSnapshot(
                        "Dv_0123456789abcdefghijklmn",
                        "校园回收机",
                        "教学楼一层",
                        true,
                        AS_OF,
                        List.of(
                                new DeliveryDevicePortOptionSnapshot(
                                        2,
                                        "塑料投口",
                                        "0.4500",
                                        "INFRARED_OR_WEIGHT",
                                        List.of("DEVICE_BUSY"))),
                        optionsBusinessRef));
        when(business.currentOptions(optionsBusinessRef)).thenReturn(
                new DeliveryOptionsBusinessFacts(
                        OptionalLong.of(-500),
                        List.of(new DeliveryPortBusinessFacts(
                                2,
                                false,
                                DeliveryPortBusinessFacts
                                        .BaselineState.INVALID,
                                new BigDecimal("135.20"),
                                DeliveryPortBusinessFacts
                                        .DetectionGate.READY,
                                DeliveryPortBusinessFacts
                                        .ConfirmedFullnessState.FULL,
                                true,
                                true,
                                true))));
        when(wallet.current(any())).thenReturn(
                new DeliveryWalletQualification(
                        new OrganizationUserUid(USER_UID),
                        -500,
                        -500,
                        false,
                        List.of(
                                DeliveryWalletQualificationBlocker
                                        .WALLET_DELIVERY_LIMIT_REACHED)));

        var result = service.deliveryOptions(
                "Dv_0123456789abcdefghijklmn");

        assertThat(result.deviceCode())
                .isEqualTo("Dv_0123456789abcdefghijklmn");
        assertThat(result.deviceBusy()).isTrue();
        assertThat(result.ports()).singleElement()
                .satisfies(port -> {
                    assertThat(port.fullnessPercent())
                            .isEqualTo("135.20");
                    assertThat(port.deliveryAllowed()).isFalse();
                    assertThat(port.blockers()).containsExactly(
                            "PHONE_BINDING_REQUIRED",
                            "WALLET_DELIVERY_LIMIT_REACHED",
                            "DEVICE_BUSY",
                            "CURRENT_BAG_MISSING",
                            "BASELINE_REMEASUREMENT_ACTIVE",
                            "PORT_CLEAN_OPERATION_ACTIVE",
                            "CLEAN_RESTARTED_CLEAN_REQUIRED");
                });
    }

    @Test
    void missingDeliveryConfigurationBlocksDisplayAndSkipsWalletRead() {
        when(identity.current()).thenReturn(identity(true));
        when(device.deliveryOptions(any())).thenReturn(
                healthyDeviceOptions());
        when(business.currentOptions(optionsBusinessRef)).thenReturn(
                new DeliveryOptionsBusinessFacts(
                        OptionalLong.empty(),
                        List.of(healthyBusinessPort())));

        var result = service.deliveryOptions(
                "Dv_0123456789abcdefghijklmn");

        assertThat(result.ports()).singleElement()
                .satisfies(port -> {
                    assertThat(port.deliveryAllowed()).isFalse();
                    assertThat(port.blockers()).containsExactly(
                            "CONFIGURATION_NOT_APPLIED");
                });
        verify(wallet, never()).current(any());
    }

    @Test
    void missingFullnessObservationDoesNotBlockDelivery() {
        when(identity.current()).thenReturn(identity(true));
        when(device.deliveryOptions(any())).thenReturn(
                deviceOptions("INFRARED_ONLY"));
        when(business.currentOptions(optionsBusinessRef)).thenReturn(
                new DeliveryOptionsBusinessFacts(
                        OptionalLong.of(-500),
                        List.of(new DeliveryPortBusinessFacts(
                                2,
                                true,
                                DeliveryPortBusinessFacts
                                        .BaselineState.MISSING,
                                null,
                                DeliveryPortBusinessFacts
                                        .DetectionGate.MISSING,
                                DeliveryPortBusinessFacts
                                        .ConfirmedFullnessState.MISSING,
                                false,
                                false,
                                false))));
        when(wallet.current(any())).thenReturn(
                eligibleWallet());

        var result = service.deliveryOptions(
                "Dv_0123456789abcdefghijklmn");

        assertThat(result.ports()).singleElement()
                .satisfies(port -> {
                    assertThat(port.fullnessPercent()).isNull();
                    assertThat(port.deliveryAllowed()).isTrue();
                    assertThat(port.blockers()).isEmpty();
                });
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "WEIGHT_ONLY",
            "INFRARED_OR_WEIGHT"
    })
    void weightBasedModeRequiresAValidCurrentBagBaseline(
            String fullnessMode) {
        when(identity.current()).thenReturn(identity(true));
        when(device.deliveryOptions(any())).thenReturn(
                deviceOptions(fullnessMode));
        when(business.currentOptions(optionsBusinessRef)).thenReturn(
                new DeliveryOptionsBusinessFacts(
                        OptionalLong.of(-500),
                        List.of(new DeliveryPortBusinessFacts(
                                2,
                                true,
                                DeliveryPortBusinessFacts
                                        .BaselineState.INVALID,
                                null,
                                DeliveryPortBusinessFacts
                                        .DetectionGate.READY,
                                DeliveryPortBusinessFacts
                                        .ConfirmedFullnessState.NOT_FULL,
                                false,
                                false,
                                false))));
        when(wallet.current(any())).thenReturn(
                eligibleWallet());

        var result = service.deliveryOptions(
                "Dv_0123456789abcdefghijklmn");

        assertThat(result.ports()).singleElement()
                .satisfies(port -> {
                    assertThat(port.deliveryAllowed()).isFalse();
                    assertThat(port.blockers()).containsExactly(
                            "WEIGHT_BASELINE_MISSING");
                });
    }

    @Test
    void currentBagConfirmedFullBlocksDelivery() {
        when(identity.current()).thenReturn(identity(true));
        when(device.deliveryOptions(any())).thenReturn(
                healthyDeviceOptions());
        when(business.currentOptions(optionsBusinessRef)).thenReturn(
                new DeliveryOptionsBusinessFacts(
                        OptionalLong.of(-500),
                        List.of(new DeliveryPortBusinessFacts(
                                2,
                                true,
                                DeliveryPortBusinessFacts
                                        .BaselineState.VALID,
                                new BigDecimal("100.00"),
                                DeliveryPortBusinessFacts
                                        .DetectionGate.READY,
                                DeliveryPortBusinessFacts
                                        .ConfirmedFullnessState.FULL,
                                false,
                                false,
                                false))));
        when(wallet.current(any())).thenReturn(
                eligibleWallet());

        var result = service.deliveryOptions(
                "Dv_0123456789abcdefghijklmn");

        assertThat(result.ports()).singleElement()
                .satisfies(port -> {
                    assertThat(port.deliveryAllowed()).isFalse();
                    assertThat(port.blockers()).containsExactly(
                            "PORT_FULL");
                });
    }

    @ParameterizedTest
    @MethodSource("sessionPresentations")
    void mapsOnlyTheDocumentedWholeSessionPhases(
            String deviceStatus,
            Instant deviceCompletedAt,
            String expectedStatus,
            String expectedPhase,
            Long expectedPollAfterMs,
            String expectedAction) {
        when(identity.current()).thenReturn(identity(true));
        when(device.ownedSession(any())).thenReturn(
                new OwnedDeliverySessionSnapshot(
                        DELIVERY_SESSION_UID,
                        deviceStatus,
                        "Dv_0123456789abcdefghijklmn",
                        2,
                        STARTED_AT,
                        deviceCompletedAt,
                        terminal(deviceStatus) ? COMPLETED_AT : null,
                        terminal(deviceStatus) ? "USER_ENDED" : null,
                        sessionBusinessRef));
        when(business.findDeliveryOrderNo(sessionBusinessRef))
                .thenReturn(
                        "BUSINESS_CONFIRMED".equals(deviceStatus)
                                ? Optional.of("DO202607290001")
                                : Optional.empty());

        var result = service.deliverySession(
                DELIVERY_SESSION_UID);

        assertThat(result.status()).isEqualTo(expectedStatus);
        assertThat(result.phase()).isEqualTo(expectedPhase);
        assertThat(result.recommendedPollAfterMs())
                .isEqualTo(expectedPollAfterMs);
        assertThat(result.nextActions())
                .containsExactly(expectedAction);
        assertThat(result.startedAt()).isEqualTo(STARTED_AT);
        if ("BUSINESS_CONFIRMED".equals(deviceStatus)) {
            assertThat(result.deliveryOrderNo())
                    .isEqualTo("DO202607290001");
        }
    }

    private static Stream<Arguments> sessionPresentations() {
        return Stream.of(
                Arguments.of(
                        "AUTHORIZATION_QUEUED",
                        null,
                        "ACTIVE",
                        "START_QUEUED",
                        1_000L,
                        "WAIT"),
                Arguments.of(
                        "IN_PROGRESS",
                        null,
                        "ACTIVE",
                        "IN_PROGRESS",
                        1_000L,
                        "WAIT_ON_DEVICE"),
                Arguments.of(
                        "IN_PROGRESS",
                        COMPLETED_AT,
                        "ACTIVE",
                        "FINAL_RESULT_PENDING",
                        1_000L,
                        "WAIT"),
                Arguments.of(
                        "RESULT_PENDING_RECOVERY",
                        COMPLETED_AT,
                        "ACTIVE",
                        "RECOVERY_REQUIRED",
                        1_000L,
                        "WAIT"),
                Arguments.of(
                        "BUSINESS_CONFIRMED",
                        COMPLETED_AT,
                        "COMPLETED",
                        "BUSINESS_CONFIRMED",
                        null,
                        "VIEW_ORDER"),
                Arguments.of(
                        "PRE_OPEN_ENDED",
                        null,
                        "ENDED",
                        "PRE_START_FAILED",
                        null,
                        "SESSION_ENDED"));
    }

    private DeliveryDeviceOptionsSnapshot healthyDeviceOptions() {
        return deviceOptions("INFRARED_OR_WEIGHT");
    }

    private DeliveryDeviceOptionsSnapshot deviceOptions(
            String fullnessMode) {
        return new DeliveryDeviceOptionsSnapshot(
                "Dv_0123456789abcdefghijklmn",
                "校园回收机",
                "教学楼一层",
                false,
                AS_OF,
                List.of(new DeliveryDevicePortOptionSnapshot(
                        2,
                        "塑料投口",
                        "0.4500",
                        fullnessMode,
                        List.of())),
                optionsBusinessRef);
    }

    private CurrentMiniappDeliveryIdentity identity(
            boolean phoneBound) {
        return new CurrentMiniappDeliveryIdentity(
                "tenant-demo",
                "org-demo",
                new OrganizationUserUid(USER_UID),
                phoneBound,
                new SessionUid(LOGIN_SESSION_UID),
                userRef,
                walletRef);
    }

    private static DeliveryPortBusinessFacts healthyBusinessPort() {
        return new DeliveryPortBusinessFacts(
                2,
                true,
                DeliveryPortBusinessFacts.BaselineState.VALID,
                new BigDecimal("35.20"),
                DeliveryPortBusinessFacts.DetectionGate.READY,
                DeliveryPortBusinessFacts
                        .ConfirmedFullnessState.NOT_FULL,
                false,
                false,
                false);
    }

    private static DeliveryWalletQualification eligibleWallet() {
        return new DeliveryWalletQualification(
                new OrganizationUserUid(USER_UID),
                0,
                -500,
                true,
                List.of());
    }

    private static boolean terminal(String status) {
        return "BUSINESS_CONFIRMED".equals(status)
                || "PRE_OPEN_ENDED".equals(status);
    }
}
