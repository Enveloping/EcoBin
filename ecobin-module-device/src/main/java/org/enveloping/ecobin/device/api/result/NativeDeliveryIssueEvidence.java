package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.device.api.uart.EcobinUartProtocol;
import static org.enveloping.ecobin.device.api.uart.EcobinUartProtocol.*;

import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.UUID;

/** Bounded UART 2 START/boot evidence, not proof of closed doors or settlement. */
public final class NativeDeliveryIssueEvidence {
    private static final long SAFE_MAX = 9_007_199_254_740_991L;
    private NativeDeliveryIssueEvidence() {}

    public static Start validateArchive(String startHex, String bootKind, String bootHex,
            UUID sessionUid, int portNo, long sourceBoot, long targetBoot) {
        byte[] raw = hex(startHex, START_DELIVERY_SESSION_PAYLOAD_MIN_LENGTH);
        validateBody(MESSAGE_START_DELIVERY_SESSION, raw, SenderRole.EDGE);
        ByteBuffer b = ByteBuffer.wrap(raw);
        UUID command = uuid(b, START_DELIVERY_SESSION_MCU_COMMAND_UID_OFFSET);
        long boot = b.getLong(START_DELIVERY_SESSION_TARGET_MCU_BOOT_ID_OFFSET);
        long sequence = Integer.toUnsignedLong(b.getInt(START_DELIVERY_SESSION_COMMAND_SEQUENCE_OFFSET));
        UUID session = uuid(b, START_DELIVERY_SESSION_SESSION_UID_OFFSET);
        int port = Byte.toUnsignedInt(b.get(START_DELIVERY_SESSION_PORT_NO_OFFSET));
        long config = b.getLong(START_DELIVERY_SESSION_CONFIG_VERSION_OFFSET);
        String configDigest = HexFormat.of().formatHex(raw, START_DELIVERY_SESSION_CONFIG_CONTENT_SHA256_OFFSET,
                START_DELIVERY_SESSION_CONFIG_CONTENT_SHA256_OFFSET + 32);
        if (!session.equals(sessionUid) || port != portNo || boot != sourceBoot
                || sourceBoot >= targetBoot || targetBoot > SAFE_MAX) {
            throw new IllegalArgumentException("native issue START differs from archived identity");
        }
        ByteBuffer witness;
        long observed;
        if ("BOOT_PROBE_REPLY".equals(bootKind)) {
            byte[] bytes = hex(bootHex, BOOT_PROBE_REPLY_PAYLOAD_MIN_LENGTH);
            validateBody(MESSAGE_BOOT_PROBE_REPLY, bytes, SenderRole.MCU);
            witness = ByteBuffer.wrap(bytes);
            observed = witness.getLong(BOOT_PROBE_REPLY_MCU_BOOT_ID_OFFSET);
        } else if ("BIND_BOOT_REPLY".equals(bootKind)) {
            byte[] bytes = hex(bootHex, BIND_BOOT_REPLY_PAYLOAD_MIN_LENGTH);
            validateBody(MESSAGE_BIND_BOOT_REPLY, bytes, SenderRole.MCU);
            witness = ByteBuffer.wrap(bytes);
            observed = witness.getLong(BIND_BOOT_REPLY_MCU_BOOT_ID_OFFSET);
            int status = Byte.toUnsignedInt(witness.get(BIND_BOOT_REPLY_STATUS_OFFSET));
            if (status != 1 && status != 3) {
                throw new IllegalArgumentException("native issue lacks a positive boot binding");
            }
        } else throw new IllegalArgumentException("unsupported native issue boot witness");
        if (observed != targetBoot) throw new IllegalArgumentException("native issue boot witness differs");
        return new Start(command, sequence, session, port, boot, config, configDigest);
    }

    private static byte[] hex(String value, int bytes) {
        if (value == null || value.length() != bytes*2 || !value.matches("[0-9a-f]+"))
            throw new IllegalArgumentException("native issue evidence length/encoding differs");
        return HexFormat.of().parseHex(value);
    }
    private static UUID uuid(ByteBuffer buffer, int offset) {
        return new UUID(buffer.getLong(offset), buffer.getLong(offset + 8));
    }
    private static void validateBody(int message, byte[] raw, SenderRole sender) {
        // The local wrapper invokes generated body checks; it is not a claim about received UART CRC.
        decodeFrame(encodeFrame(message, Boolean.TRUE.equals(ackRequired(message)) ? ACK_REQUIRED : 0, 1, raw), true, sender);
    }
    /** Validate the whole Registry-defined result, then bind it to the archived original START. */
    public static void validateLateResult(byte[] raw, Start start) {
        validateBody(MESSAGE_WORK_RESULT,raw,SenderRole.MCU);
        ByteBuffer b=ByteBuffer.wrap(raw);
        long boot=b.getLong(WORK_RESULT_MCU_BOOT_ID_OFFSET);
        UUID work=uuid(b,WORK_RESULT_WORK_UID_OFFSET);
        int kind=Byte.toUnsignedInt(b.get(WORK_RESULT_WORK_TYPE_OFFSET)),port=Byte.toUnsignedInt(b.get(WORK_RESULT_PORT_NO_OFFSET));
        long config=b.getLong(WORK_RESULT_CONFIG_VERSION_OFFSET);
        UUID command=uuid(b,WORK_RESULT_ORIGIN_COMMAND_UID_OFFSET);
        long sequence=Integer.toUnsignedLong(b.getInt(WORK_RESULT_ORIGIN_COMMAND_SEQUENCE_OFFSET));
        if(boot!=start.mcuBootId()||!work.equals(start.sessionUid())||kind!=2||port!=start.portNo()
                ||config!=start.configVersion()||!command.equals(start.commandUid())||sequence!=start.commandSequence())
            throw new IllegalArgumentException("late result does not belong to original archived delivery");
    }
    public static byte[] sha256(byte[] raw) {
        try { return MessageDigest.getInstance("SHA-256").digest(raw); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    public record Start(UUID commandUid, long commandSequence, UUID sessionUid, int portNo,
                        long mcuBootId, long configVersion, String configContentSha256) {}
}
