package org.enveloping.ecobin.funds.api.command;

import org.enveloping.ecobin.funds.api.persistence.DeliveryRevisionWalletEntryRef;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * 将一条已经落库的投递修订差额应用到归属用户钱包。
 *
 * <p>公开 UID 是业务与幂等身份；关系引用只负责在当前事务中建立外键，
 * 不能替代公开身份。</p>
 */
public record ApplyDeliveryRevisionDeltaCommand(
        OrganizationUserUid organizationUserUid,
        UUID revisionUid,
        DeliveryRevisionWalletEntryRef revisionRef,
        DeliveryRevisionKind revisionKind,
        long deltaCent,
        long currentStopThresholdCent,
        Instant trustedOccurredAt) {

    public ApplyDeliveryRevisionDeltaCommand {
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        Objects.requireNonNull(revisionUid, "revisionUid");
        Objects.requireNonNull(revisionRef, "revisionRef");
        Objects.requireNonNull(revisionKind, "revisionKind");
        Objects.requireNonNull(trustedOccurredAt, "trustedOccurredAt");
        if (revisionUid.version() != 4
                || revisionUid.variant() != 2) {
            throw new IllegalArgumentException(
                    "revisionUid must be an RFC 4122 UUIDv4");
        }
        if (deltaCent == 0) {
            throw new IllegalArgumentException(
                    "deltaCent must not be zero");
        }
        if (currentStopThresholdCent >= 0) {
            throw new IllegalArgumentException(
                    "currentStopThresholdCent must be negative");
        }
    }
}
