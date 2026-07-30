package org.enveloping.ecobin.funds.api.command;

import org.enveloping.ecobin.funds.api.persistence.DeliveryRevisionWalletEntryRef;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * 将一条已经落库的投递修订差额应用到归属用户钱包。
 *
 * <p>归属用户的内部编号与公开 UUID 只能来自 identity 签发的同事务可信
 * 组合，调用方不能分别传入后重新拼接。投递修订引用只负责建立 recycling
 * 修订事实的内部复合外键。</p>
 */
public record ApplyDeliveryRevisionDeltaCommand(
        String deliveryOrderNo,
        UUID revisionUid,
        DeliveryWalletEntryOwnerRef walletOwnerRef,
        DeliveryRevisionWalletEntryRef revisionRef,
        DeliveryRevisionKind revisionKind,
        long deltaCent,
        long currentStopThresholdCent,
        Instant trustedOccurredAt) {

    public ApplyDeliveryRevisionDeltaCommand {
        if (deliveryOrderNo == null
                || deliveryOrderNo.isBlank()
                || deliveryOrderNo.length() > 64) {
            throw new IllegalArgumentException(
                    "deliveryOrderNo must contain 1 to 64 characters");
        }
        deliveryOrderNo = deliveryOrderNo.trim();
        Objects.requireNonNull(revisionUid, "revisionUid");
        Objects.requireNonNull(walletOwnerRef, "walletOwnerRef");
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
