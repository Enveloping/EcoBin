package org.enveloping.ecobin.funds.api.command;

import org.enveloping.ecobin.funds.api.persistence.DeliveryRevisionWalletEntryRef;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.UUID;
import java.util.function.Function;

import static org.junit.jupiter.api.Assertions.assertThrows;

class ApplyDeliveryRevisionDeltaCommandTest {

    @Test
    void zeroDeltaMustBeHandledByRecyclingWithoutCallingFunds() {
        assertThrows(
                IllegalArgumentException.class,
                () -> command(0, -100));
    }

    @Test
    void stopThresholdMustRemainNegative() {
        assertThrows(
                IllegalArgumentException.class,
                () -> command(1, 0));
    }

    @Test
    void revisionUidMustBeUuidV4() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new ApplyDeliveryRevisionDeltaCommand(
                        new OrganizationUserUid(UUID.randomUUID()),
                        "DO-TEST-0001",
                        UUID.fromString(
                                "00000000-0000-0000-0000-000000000001"),
                        reference(),
                        DeliveryRevisionKind.CORRECTION,
                        1,
                        -100,
                        Instant.EPOCH));
    }

    private static ApplyDeliveryRevisionDeltaCommand command(
            long delta,
            long threshold) {
        return new ApplyDeliveryRevisionDeltaCommand(
                new OrganizationUserUid(UUID.randomUUID()),
                "DO-TEST-0001",
                UUID.randomUUID(),
                reference(),
                DeliveryRevisionKind.CORRECTION,
                delta,
                threshold,
                Instant.EPOCH);
    }

    private static DeliveryRevisionWalletEntryRef reference() {
        return new DeliveryRevisionWalletEntryRef() {
            @Override
            public <T> T withWalletEntryForeignKeysOnce(
                    Function<WalletEntryForeignKeys, T> function) {
                return function.apply(
                        new WalletEntryForeignKeys(
                                1,
                                2,
                                3,
                                4));
            }
        };
    }
}
