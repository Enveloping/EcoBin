package org.enveloping.ecobin.device.api.uart;

// Generated from contracts/uart/uart-registry.yaml.
// DO NOT EDIT. Registry SHA-256: 57354f9fc3a9fb33d767fa2a24b2d06e50c4f37025a5ff171b134177ffdcf6ec

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

public final class EcobinUartProtocol {
    public static final String REGISTRY_SHA256 = "57354f9fc3a9fb33d767fa2a24b2d06e50c4f37025a5ff171b134177ffdcf6ec";
    public static final String IMPLEMENTATION_STAGE = "SIMPLIFIED_BUSINESS_INTEGRATION_NOT_RELEASED";
    public static final int BAUD_RATE = 115200;
    public static final int DATA_BITS = 8;
    public static final int STOP_BITS = 1;
    public static final String PARITY = "NONE";
    public static final String FLOW_CONTROL = "NONE";
    public static final int PROTOCOL_MAJOR = 2;
    public static final int PROTOCOL_MINOR = 0;
    public static final int MAXIMUM_FRAME_LENGTH = 256;
    public static final int MAXIMUM_PAYLOAD_LENGTH = 242;
    public static final int ACK_REQUIRED = 0x01;
    public static final int MESSAGE_QUERY_DEVICE_FACTS = 0x45;
    public static final int MESSAGE_DEVICE_FACTS_REPLY = 0x46;
    public static final int MESSAGE_QUERY_DEVICE_IDENTITY = 0x47;
    public static final int MESSAGE_DEVICE_IDENTITY_REPLY = 0x48;
    public static final int MESSAGE_ACTUATOR_EVENT_SAVED = 0x60;
    public static final int MESSAGE_ACTUATOR_EVENT_SAVED_REPLY = 0x61;
    public static final int MESSAGE_QUERY_ACTUATOR_EVENT = 0x5E;
    public static final int MESSAGE_ACTUATOR_EVENT_QUERY_REPLY = 0x5F;
    public static final int MESSAGE_PROCESS_EVENT_SAVED = 0x5C;
    public static final int MESSAGE_PROCESS_EVENT_SAVED_REPLY = 0x5D;
    public static final int MESSAGE_QUERY_PROCESS_EVENT = 0x5A;
    public static final int MESSAGE_PROCESS_EVENT_QUERY_REPLY = 0x5B;
    public static final int MESSAGE_QUERY_WORK = 0x43;
    public static final int MESSAGE_WORK_QUERY_REPLY = 0x44;
    public static final int MESSAGE_WORK_RESULT = 0x40;
    public static final int MESSAGE_QUERY_RESULT = 0x41;
    public static final int MESSAGE_RESULT_QUERY_REPLY = 0x42;
    public static final int MESSAGE_HELLO = 0x01;
    public static final int MESSAGE_HELLO_ACK = 0x02;
    public static final int MESSAGE_ACK = 0x03;
    public static final int MESSAGE_NACK = 0x04;
    public static final int MESSAGE_QUERY_STATE = 0x05;
    public static final int MESSAGE_SAFE_CLOSE = 0x06;
    public static final int MESSAGE_BOOT_PROBE = 0x07;
    public static final int MESSAGE_BOOT_PROBE_REPLY = 0x08;
    public static final int MESSAGE_BIND_BOOT = 0x09;
    public static final int MESSAGE_BIND_BOOT_REPLY = 0x0A;
    public static final int MESSAGE_COMMAND_DECISION = 0x0B;
    public static final int MESSAGE_QUERY_COMMAND = 0x0C;
    public static final int MESSAGE_COMMAND_QUERY_RESULT = 0x0D;
    public static final int MESSAGE_RESULT_SAVED = 0x0E;
    public static final int MESSAGE_RESULT_SAVED_REPLY = 0x0F;
    public static final int MESSAGE_CONFIG_BEGIN = 0x10;
    public static final int MESSAGE_CONFIG_DEVICE_BLOCK = 0x11;
    public static final int MESSAGE_CONFIG_PORT_BLOCK = 0x12;
    public static final int MESSAGE_CONFIG_COMMIT = 0x13;
    public static final int MESSAGE_CONFIG_APPLY_RESULT = 0x14;
    public static final int MESSAGE_DEVICE_ENTRY_URL_BEGIN = 0x15;
    public static final int MESSAGE_DEVICE_ENTRY_URL_PART = 0x16;
    public static final int MESSAGE_DEVICE_ENTRY_URL_COMMIT = 0x17;
    public static final int MESSAGE_DEVICE_ENTRY_URL_APPLY_RESULT = 0x18;
    public static final int MESSAGE_START_DELIVERY_SESSION = 0x20;
    public static final int MESSAGE_START_CLEAN_OPERATION = 0x21;
    public static final int MESSAGE_UNLOCK_CLEAN_DOOR = 0x22;
    public static final int MESSAGE_RESUME_CLEAN_OPERATION = 0x23;
    public static final int MESSAGE_END_CLEAN_BEFORE_UNLOCK = 0x24;
    public static final int MESSAGE_SAMPLE_FULLNESS = 0x25;
    public static final int MESSAGE_MEASURE_BASELINE = 0x26;
    public static final int MESSAGE_AUTHORIZE_DELIVERY_FIRST_OPEN = 0x27;
    public static final int MESSAGE_CONFIRM_NO_ACTIVE_WORK = 0x28;
    public static final int MESSAGE_WORK_PREOPEN_WEIGHT_READY = 0x30;
    public static final int MESSAGE_DELIVERY_DOOR_COMMAND_RESULT = 0x31;
    public static final int MESSAGE_DELIVERY_LOCAL_DOOR_RESULT = 0x62;
    public static final int MESSAGE_CLEAN_OPERATION_INTERRUPTED = 0x65;
    public static final int MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED = 0x64;
    public static final int MESSAGE_DELIVERY_CYCLE_ABORTED = 0x63;
    public static final int MESSAGE_WORK_POSTCLOSE_WEIGHT_READY = 0x32;
    public static final int MESSAGE_DELIVERY_SELECTION = 0x33;
    public static final int MESSAGE_WORK_PREUNLOCK_WEIGHT_READY = 0x34;
    public static final int MESSAGE_CLEAN_LOCK_POWER_CHANGED = 0x35;
    public static final int MESSAGE_CLEAN_UNLOCK_REQUESTED = 0x36;
    public static final int MESSAGE_CLEAN_FINISH_REQUESTED = 0x37;
    public static final int MESSAGE_CLEAN_FINAL_WEIGHT_READY = 0x38;
    public static final int MESSAGE_FULLNESS_SAMPLE_RESULT = 0x39;
    public static final int MESSAGE_BASELINE_MEASUREMENT_RESULT = 0x3A;
    public static final int MESSAGE_FAULT_OBSERVED = 0x3B;
    public static final int MESSAGE_SAFETY_SENSOR_EVENT = 0x3C;
    public static final int MESSAGE_SAFE_CLOSE_RESULT = 0x3D;
    public static final int MESSAGE_CLEAN_COMPLETION_CONFIRMED = 0x3E;
    public static final int MESSAGE_BOOT_RECONCILIATION_RESULT = 0x3F;
    public static final int MESSAGE_STATE_SNAPSHOT_BEGIN = 0x50;
    public static final int MESSAGE_STATE_SNAPSHOT_PORT = 0x51;
    public static final int MESSAGE_STATE_SNAPSHOT_END = 0x52;
    public static final int QUERY_DEVICE_FACTS_PAYLOAD_MIN_LENGTH = 17;
    public static final int QUERY_DEVICE_FACTS_PAYLOAD_MAX_LENGTH = 17;
    public static final int QUERY_DEVICE_FACTS_QUERY_ID_OFFSET = 0;
    public static final int QUERY_DEVICE_FACTS_TARGET_MCU_BOOT_ID_OFFSET = 8;
    public static final int QUERY_DEVICE_FACTS_PORT_NO_OFFSET = 16;
    public static final int DEVICE_FACTS_REPLY_PAYLOAD_MIN_LENGTH = 226;
    public static final int DEVICE_FACTS_REPLY_PAYLOAD_MAX_LENGTH = 226;
    public static final int DEVICE_FACTS_REPLY_QUERY_ID_OFFSET = 0;
    public static final int DEVICE_FACTS_REPLY_TARGET_MCU_BOOT_ID_OFFSET = 8;
    public static final int DEVICE_FACTS_REPLY_PORT_NO_OFFSET = 16;
    public static final int DEVICE_FACTS_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 17;
    public static final int DEVICE_FACTS_REPLY_STATUS_OFFSET = 25;
    public static final int DEVICE_FACTS_REPLY_CAPTURED_UPTIME_MS_OFFSET = 26;
    public static final int DEVICE_FACTS_REPLY_APPLIED_CONFIG_VERSION_OFFSET = 34;
    public static final int DEVICE_FACTS_REPLY_APPLIED_CONTENT_SHA256_OFFSET = 42;
    public static final int DEVICE_FACTS_REPLY_APPLIED_MCU_PAYLOAD_SHA256_OFFSET = 74;
    public static final int DEVICE_FACTS_REPLY_CONFIG_STAGING_OFFSET = 106;
    public static final int DEVICE_FACTS_REPLY_CONTROL_UPTIME_MS_OFFSET = 107;
    public static final int DEVICE_FACTS_REPLY_LAST_DELIVERY_DOOR_COMMAND_OFFSET = 115;
    public static final int DEVICE_FACTS_REPLY_DOOR_ACTION_ACTIVE_OFFSET = 116;
    public static final int DEVICE_FACTS_REPLY_PB6_OUTPUT_OFFSET = 117;
    public static final int DEVICE_FACTS_REPLY_PB7_OUTPUT_OFFSET = 118;
    public static final int DEVICE_FACTS_REPLY_PB5_ACTIVE_OFFSET = 119;
    public static final int DEVICE_FACTS_REPLY_PINCH_PAUSED_OFFSET = 120;
    public static final int DEVICE_FACTS_REPLY_CLEAN_LOCK_POWERED_OFFSET = 121;
    public static final int DEVICE_FACTS_REPLY_UPDATE_LATCHED_OFFSET = 122;
    public static final int DEVICE_FACTS_REPLY_SCALE_READ_STATUS_OFFSET = 123;
    public static final int DEVICE_FACTS_REPLY_SCALE_ATTEMPT_SEQUENCE_OFFSET = 124;
    public static final int DEVICE_FACTS_REPLY_SCALE_CAPTURED_UPTIME_MS_OFFSET = 128;
    public static final int DEVICE_FACTS_REPLY_SCALE_WEIGHT_GRAMS_OFFSET = 136;
    public static final int DEVICE_FACTS_REPLY_SCALE_CALIBRATION_VERSION_OFFSET = 140;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_SEQUENCE_OFFSET = 144;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_STATE_OFFSET = 148;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_CONFIG_VERSION_OFFSET = 149;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_OBSERVED_UPTIME_MS_OFFSET = 157;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_ELAPSED_MS_OFFSET = 165;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_SAMPLE_COUNT_OFFSET = 167;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_WEIGHT_GRAMS_OFFSET = 168;
    public static final int DEVICE_FACTS_REPLY_MEASUREMENT_SPAN_GRAMS_OFFSET = 172;
    public static final int DEVICE_FACTS_REPLY_SMOKE_OBSERVATION_STATE_OFFSET = 176;
    public static final int DEVICE_FACTS_REPLY_SMOKE_OBSERVED_UPTIME_MS_OFFSET = 177;
    public static final int DEVICE_FACTS_REPLY_FULLNESS_OBSERVATION_KIND_OFFSET = 185;
    public static final int DEVICE_FACTS_REPLY_FULLNESS_READ_STATUS_OFFSET = 186;
    public static final int DEVICE_FACTS_REPLY_FULLNESS_CAPTURED_UPTIME_MS_OFFSET = 187;
    public static final int DEVICE_FACTS_REPLY_FULLNESS_INFRARED_BLOCKED_OFFSET = 195;
    public static final int DEVICE_FACTS_REPLY_FULLNESS_DISTANCE_MM_OFFSET = 196;
    public static final int DEVICE_FACTS_REPLY_RETAINED_WORK_STATE_OFFSET = 198;
    public static final int DEVICE_FACTS_REPLY_RETAINED_WORK_UID_OFFSET = 199;
    public static final int DEVICE_FACTS_REPLY_RETAINED_WORK_TYPE_OFFSET = 215;
    public static final int DEVICE_FACTS_REPLY_RETAINED_PORT_NO_OFFSET = 216;
    public static final int DEVICE_FACTS_REPLY_RETAINED_WORK_PHASE_OFFSET = 217;
    public static final int DEVICE_FACTS_REPLY_RETAINED_ORIGIN_COMMAND_SEQUENCE_OFFSET = 218;
    public static final int DEVICE_FACTS_REPLY_RETAINED_RESULT_SEQUENCE_OFFSET = 222;
    public static final int QUERY_DEVICE_IDENTITY_PAYLOAD_MIN_LENGTH = 16;
    public static final int QUERY_DEVICE_IDENTITY_PAYLOAD_MAX_LENGTH = 16;
    public static final int QUERY_DEVICE_IDENTITY_QUERY_ID_OFFSET = 0;
    public static final int QUERY_DEVICE_IDENTITY_TARGET_MCU_BOOT_ID_OFFSET = 8;
    public static final int DEVICE_IDENTITY_REPLY_PAYLOAD_MIN_LENGTH = 53;
    public static final int DEVICE_IDENTITY_REPLY_PAYLOAD_MAX_LENGTH = 85;
    public static final int DEVICE_IDENTITY_REPLY_QUERY_ID_OFFSET = 0;
    public static final int DEVICE_IDENTITY_REPLY_TARGET_MCU_BOOT_ID_OFFSET = 8;
    public static final int DEVICE_IDENTITY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 16;
    public static final int DEVICE_IDENTITY_REPLY_STATUS_OFFSET = 24;
    public static final int DEVICE_IDENTITY_REPLY_PROTOCOL_MAJOR_OFFSET = 25;
    public static final int DEVICE_IDENTITY_REPLY_PROTOCOL_MINOR_OFFSET = 26;
    public static final int DEVICE_IDENTITY_REPLY_PORT_COUNT_OFFSET = 27;
    public static final int DEVICE_IDENTITY_REPLY_CAPABILITY_BITMAP_OFFSET = 28;
    public static final int DEVICE_IDENTITY_REPLY_HIGHEST_COMMAND_SEQUENCE_OFFSET = 36;
    public static final int DEVICE_IDENTITY_REPLY_FIRMWARE_VERSION_CODE_OFFSET = 40;
    public static final int DEVICE_IDENTITY_REPLY_FIRMWARE_IDENTITY_HIGH_OFFSET = 44;
    public static final int DEVICE_IDENTITY_REPLY_FIRMWARE_IDENTITY_LOW_OFFSET = 48;
    public static final int DEVICE_IDENTITY_REPLY_FIRMWARE_VERSION_OFFSET = 52;
    public static final int ACTUATOR_EVENT_SAVED_PAYLOAD_MIN_LENGTH = 45;
    public static final int ACTUATOR_EVENT_SAVED_PAYLOAD_MAX_LENGTH = 45;
    public static final int ACTUATOR_EVENT_SAVED_MCU_BOOT_ID_OFFSET = 0;
    public static final int ACTUATOR_EVENT_SAVED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int ACTUATOR_EVENT_SAVED_EVENT_MESSAGE_TYPE_OFFSET = 12;
    public static final int ACTUATOR_EVENT_SAVED_EVENT_DIGEST_SHA256_OFFSET = 13;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_PAYLOAD_MIN_LENGTH = 54;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_PAYLOAD_MAX_LENGTH = 54;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_MCU_BOOT_ID_OFFSET = 0;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_EVENT_MESSAGE_TYPE_OFFSET = 12;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_EVENT_DIGEST_SHA256_OFFSET = 13;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 45;
    public static final int ACTUATOR_EVENT_SAVED_REPLY_STATUS_OFFSET = 53;
    public static final int QUERY_ACTUATOR_EVENT_PAYLOAD_MIN_LENGTH = 20;
    public static final int QUERY_ACTUATOR_EVENT_PAYLOAD_MAX_LENGTH = 20;
    public static final int QUERY_ACTUATOR_EVENT_QUERY_ID_OFFSET = 0;
    public static final int QUERY_ACTUATOR_EVENT_TARGET_MCU_BOOT_ID_OFFSET = 8;
    public static final int QUERY_ACTUATOR_EVENT_AFTER_MCU_EVENT_SEQUENCE_OFFSET = 16;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_PAYLOAD_MIN_LENGTH = 66;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH = 66;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_QUERY_ID_OFFSET = 0;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_TARGET_MCU_BOOT_ID_OFFSET = 8;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_AFTER_MCU_EVENT_SEQUENCE_OFFSET = 16;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 20;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_STATUS_OFFSET = 28;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_MCU_EVENT_SEQUENCE_OFFSET = 29;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_EVENT_MESSAGE_TYPE_OFFSET = 33;
    public static final int ACTUATOR_EVENT_QUERY_REPLY_EVENT_DIGEST_SHA256_OFFSET = 34;
    public static final int PROCESS_EVENT_SAVED_PAYLOAD_MIN_LENGTH = 45;
    public static final int PROCESS_EVENT_SAVED_PAYLOAD_MAX_LENGTH = 45;
    public static final int PROCESS_EVENT_SAVED_MCU_BOOT_ID_OFFSET = 0;
    public static final int PROCESS_EVENT_SAVED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int PROCESS_EVENT_SAVED_EVENT_MESSAGE_TYPE_OFFSET = 12;
    public static final int PROCESS_EVENT_SAVED_EVENT_DIGEST_SHA256_OFFSET = 13;
    public static final int PROCESS_EVENT_SAVED_REPLY_PAYLOAD_MIN_LENGTH = 54;
    public static final int PROCESS_EVENT_SAVED_REPLY_PAYLOAD_MAX_LENGTH = 54;
    public static final int PROCESS_EVENT_SAVED_REPLY_MCU_BOOT_ID_OFFSET = 0;
    public static final int PROCESS_EVENT_SAVED_REPLY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int PROCESS_EVENT_SAVED_REPLY_EVENT_MESSAGE_TYPE_OFFSET = 12;
    public static final int PROCESS_EVENT_SAVED_REPLY_EVENT_DIGEST_SHA256_OFFSET = 13;
    public static final int PROCESS_EVENT_SAVED_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 45;
    public static final int PROCESS_EVENT_SAVED_REPLY_STATUS_OFFSET = 53;
    public static final int QUERY_PROCESS_EVENT_PAYLOAD_MIN_LENGTH = 97;
    public static final int QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH = 97;
    public static final int QUERY_PROCESS_EVENT_QUERY_ID_OFFSET = 0;
    public static final int QUERY_PROCESS_EVENT_MCU_COMMAND_UID_OFFSET = 8;
    public static final int QUERY_PROCESS_EVENT_COMMAND_DIGEST_SHA256_OFFSET = 24;
    public static final int QUERY_PROCESS_EVENT_TARGET_MCU_BOOT_ID_OFFSET = 56;
    public static final int QUERY_PROCESS_EVENT_COMMAND_SEQUENCE_OFFSET = 64;
    public static final int QUERY_PROCESS_EVENT_WORK_UID_OFFSET = 68;
    public static final int QUERY_PROCESS_EVENT_WORK_TYPE_OFFSET = 84;
    public static final int QUERY_PROCESS_EVENT_PORT_NO_OFFSET = 85;
    public static final int QUERY_PROCESS_EVENT_EVENT_MESSAGE_TYPE_OFFSET = 86;
    public static final int QUERY_PROCESS_EVENT_STEP_SEQUENCE_OFFSET = 87;
    public static final int QUERY_PROCESS_EVENT_CONFIG_VERSION_OFFSET = 89;
    public static final int PROCESS_EVENT_QUERY_REPLY_PAYLOAD_MIN_LENGTH = 142;
    public static final int PROCESS_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH = 142;
    public static final int PROCESS_EVENT_QUERY_REPLY_QUERY_ID_OFFSET = 0;
    public static final int PROCESS_EVENT_QUERY_REPLY_MCU_COMMAND_UID_OFFSET = 8;
    public static final int PROCESS_EVENT_QUERY_REPLY_COMMAND_DIGEST_SHA256_OFFSET = 24;
    public static final int PROCESS_EVENT_QUERY_REPLY_TARGET_MCU_BOOT_ID_OFFSET = 56;
    public static final int PROCESS_EVENT_QUERY_REPLY_COMMAND_SEQUENCE_OFFSET = 64;
    public static final int PROCESS_EVENT_QUERY_REPLY_WORK_UID_OFFSET = 68;
    public static final int PROCESS_EVENT_QUERY_REPLY_WORK_TYPE_OFFSET = 84;
    public static final int PROCESS_EVENT_QUERY_REPLY_PORT_NO_OFFSET = 85;
    public static final int PROCESS_EVENT_QUERY_REPLY_EVENT_MESSAGE_TYPE_OFFSET = 86;
    public static final int PROCESS_EVENT_QUERY_REPLY_STEP_SEQUENCE_OFFSET = 87;
    public static final int PROCESS_EVENT_QUERY_REPLY_CONFIG_VERSION_OFFSET = 89;
    public static final int PROCESS_EVENT_QUERY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 97;
    public static final int PROCESS_EVENT_QUERY_REPLY_STATUS_OFFSET = 105;
    public static final int PROCESS_EVENT_QUERY_REPLY_MCU_EVENT_SEQUENCE_OFFSET = 106;
    public static final int PROCESS_EVENT_QUERY_REPLY_EVENT_DIGEST_SHA256_OFFSET = 110;
    public static final int QUERY_WORK_PAYLOAD_MIN_LENGTH = 86;
    public static final int QUERY_WORK_PAYLOAD_MAX_LENGTH = 86;
    public static final int QUERY_WORK_QUERY_ID_OFFSET = 0;
    public static final int QUERY_WORK_MCU_COMMAND_UID_OFFSET = 8;
    public static final int QUERY_WORK_COMMAND_DIGEST_SHA256_OFFSET = 24;
    public static final int QUERY_WORK_TARGET_MCU_BOOT_ID_OFFSET = 56;
    public static final int QUERY_WORK_COMMAND_SEQUENCE_OFFSET = 64;
    public static final int QUERY_WORK_WORK_UID_OFFSET = 68;
    public static final int QUERY_WORK_WORK_TYPE_OFFSET = 84;
    public static final int QUERY_WORK_PORT_NO_OFFSET = 85;
    public static final int WORK_QUERY_REPLY_PAYLOAD_MIN_LENGTH = 132;
    public static final int WORK_QUERY_REPLY_PAYLOAD_MAX_LENGTH = 132;
    public static final int WORK_QUERY_REPLY_QUERY_ID_OFFSET = 0;
    public static final int WORK_QUERY_REPLY_MCU_COMMAND_UID_OFFSET = 8;
    public static final int WORK_QUERY_REPLY_COMMAND_DIGEST_SHA256_OFFSET = 24;
    public static final int WORK_QUERY_REPLY_TARGET_MCU_BOOT_ID_OFFSET = 56;
    public static final int WORK_QUERY_REPLY_COMMAND_SEQUENCE_OFFSET = 64;
    public static final int WORK_QUERY_REPLY_WORK_UID_OFFSET = 68;
    public static final int WORK_QUERY_REPLY_WORK_TYPE_OFFSET = 84;
    public static final int WORK_QUERY_REPLY_PORT_NO_OFFSET = 85;
    public static final int WORK_QUERY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 86;
    public static final int WORK_QUERY_REPLY_STATUS_OFFSET = 94;
    public static final int WORK_QUERY_REPLY_PHASE_OFFSET = 95;
    public static final int WORK_QUERY_REPLY_RESULT_SEQUENCE_OFFSET = 96;
    public static final int WORK_QUERY_REPLY_RESULT_DIGEST_SHA256_OFFSET = 100;
    public static final int WORK_RESULT_PAYLOAD_MIN_LENGTH = 199;
    public static final int WORK_RESULT_PAYLOAD_MAX_LENGTH = 199;
    public static final int WORK_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int WORK_RESULT_RESULT_SEQUENCE_OFFSET = 8;
    public static final int WORK_RESULT_WORK_UID_OFFSET = 12;
    public static final int WORK_RESULT_RESULT_DIGEST_SHA256_OFFSET = 28;
    public static final int WORK_RESULT_WORK_TYPE_OFFSET = 60;
    public static final int WORK_RESULT_PORT_NO_OFFSET = 61;
    public static final int WORK_RESULT_CONFIG_VERSION_OFFSET = 62;
    public static final int WORK_RESULT_ORIGIN_COMMAND_UID_OFFSET = 70;
    public static final int WORK_RESULT_ORIGIN_COMMAND_SEQUENCE_OFFSET = 86;
    public static final int WORK_RESULT_COMPLETED_UPTIME_MS_OFFSET = 90;
    public static final int WORK_RESULT_DELIVERY_ROUND_COUNT_OFFSET = 98;
    public static final int WORK_RESULT_CLEAN_ACTION_SEQUENCE_OFFSET = 100;
    public static final int WORK_RESULT_FINISH_REASON_OFFSET = 104;
    public static final int WORK_RESULT_PHYSICAL_CLOSE_CONFIRMED_OFFSET = 105;
    public static final int WORK_RESULT_NEGATIVE_WEIGHT_ANOMALY_OFFSET = 106;
    public static final int WORK_RESULT_INITIAL_KIND_OFFSET = 107;
    public static final int WORK_RESULT_INITIAL_MEASUREMENT_UID_OFFSET = 108;
    public static final int WORK_RESULT_INITIAL_SOURCE_MCU_BOOT_ID_OFFSET = 124;
    public static final int WORK_RESULT_INITIAL_MCU_EVENT_SEQUENCE_OFFSET = 132;
    public static final int WORK_RESULT_INITIAL_WEIGHT_GRAMS_OFFSET = 136;
    public static final int WORK_RESULT_INITIAL_ELAPSED_MS_OFFSET = 140;
    public static final int WORK_RESULT_INITIAL_SAMPLE_COUNT_OFFSET = 142;
    public static final int WORK_RESULT_INITIAL_SPAN_GRAMS_OFFSET = 143;
    public static final int WORK_RESULT_INITIAL_CALIBRATION_VERSION_OFFSET = 147;
    public static final int WORK_RESULT_INITIAL_FAULT_CODE_OFFSET = 151;
    public static final int WORK_RESULT_FINAL_KIND_OFFSET = 153;
    public static final int WORK_RESULT_FINAL_MEASUREMENT_UID_OFFSET = 154;
    public static final int WORK_RESULT_FINAL_SOURCE_MCU_BOOT_ID_OFFSET = 170;
    public static final int WORK_RESULT_FINAL_MCU_EVENT_SEQUENCE_OFFSET = 178;
    public static final int WORK_RESULT_FINAL_WEIGHT_GRAMS_OFFSET = 182;
    public static final int WORK_RESULT_FINAL_ELAPSED_MS_OFFSET = 186;
    public static final int WORK_RESULT_FINAL_SAMPLE_COUNT_OFFSET = 188;
    public static final int WORK_RESULT_FINAL_SPAN_GRAMS_OFFSET = 189;
    public static final int WORK_RESULT_FINAL_CALIBRATION_VERSION_OFFSET = 193;
    public static final int WORK_RESULT_FINAL_FAULT_CODE_OFFSET = 197;
    public static final int QUERY_RESULT_PAYLOAD_MIN_LENGTH = 68;
    public static final int QUERY_RESULT_PAYLOAD_MAX_LENGTH = 68;
    public static final int QUERY_RESULT_QUERY_ID_OFFSET = 0;
    public static final int QUERY_RESULT_MCU_BOOT_ID_OFFSET = 8;
    public static final int QUERY_RESULT_RESULT_SEQUENCE_OFFSET = 16;
    public static final int QUERY_RESULT_WORK_UID_OFFSET = 20;
    public static final int QUERY_RESULT_RESULT_DIGEST_SHA256_OFFSET = 36;
    public static final int RESULT_QUERY_REPLY_PAYLOAD_MIN_LENGTH = 77;
    public static final int RESULT_QUERY_REPLY_PAYLOAD_MAX_LENGTH = 77;
    public static final int RESULT_QUERY_REPLY_QUERY_ID_OFFSET = 0;
    public static final int RESULT_QUERY_REPLY_MCU_BOOT_ID_OFFSET = 8;
    public static final int RESULT_QUERY_REPLY_RESULT_SEQUENCE_OFFSET = 16;
    public static final int RESULT_QUERY_REPLY_WORK_UID_OFFSET = 20;
    public static final int RESULT_QUERY_REPLY_RESULT_DIGEST_SHA256_OFFSET = 36;
    public static final int RESULT_QUERY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 68;
    public static final int RESULT_QUERY_REPLY_STATUS_OFFSET = 76;
    public static final int HELLO_PAYLOAD_MIN_LENGTH = 27;
    public static final int HELLO_PAYLOAD_MAX_LENGTH = 91;
    public static final int HELLO_SENDER_ROLE_OFFSET = 0;
    public static final int HELLO_SENDER_BOOT_ID_OFFSET = 1;
    public static final int HELLO_SUPPORTED_MAJOR_OFFSET = 9;
    public static final int HELLO_MINIMUM_MINOR_OFFSET = 10;
    public static final int HELLO_MAXIMUM_MINOR_OFFSET = 11;
    public static final int HELLO_PORT_COUNT_OFFSET = 12;
    public static final int HELLO_CAPABILITY_BITMAP_OFFSET = 13;
    public static final int HELLO_MAXIMUM_FRAME_LENGTH_OFFSET = 21;
    public static final int HELLO_PENDING_CRITICAL_EVENT_COUNT_OFFSET = 23;
    public static final int HELLO_FIRMWARE_IDENTITY_OFFSET = 25;
    public static final int HELLO_ACK_PAYLOAD_MIN_LENGTH = 32;
    public static final int HELLO_ACK_PAYLOAD_MAX_LENGTH = 32;
    public static final int HELLO_ACK_RESPONDER_BOOT_ID_OFFSET = 0;
    public static final int HELLO_ACK_REFERENCED_SENDER_BOOT_ID_OFFSET = 8;
    public static final int HELLO_ACK_SELECTED_MAJOR_OFFSET = 16;
    public static final int HELLO_ACK_SELECTED_MINOR_OFFSET = 17;
    public static final int HELLO_ACK_STATUS_OFFSET = 18;
    public static final int HELLO_ACK_PORT_COUNT_OFFSET = 19;
    public static final int HELLO_ACK_CAPABILITY_BITMAP_OFFSET = 20;
    public static final int HELLO_ACK_MAXIMUM_FRAME_LENGTH_OFFSET = 28;
    public static final int HELLO_ACK_ERROR_CODE_OFFSET = 30;
    public static final int ACK_PAYLOAD_MIN_LENGTH = 22;
    public static final int ACK_PAYLOAD_MAX_LENGTH = 22;
    public static final int ACK_SENDER_BOOT_ID_OFFSET = 0;
    public static final int ACK_REFERENCED_SENDER_BOOT_ID_OFFSET = 8;
    public static final int ACK_REFERENCED_TX_SEQUENCE_OFFSET = 16;
    public static final int ACK_REFERENCED_MESSAGE_TYPE_OFFSET = 20;
    public static final int ACK_DISPOSITION_OFFSET = 21;
    public static final int NACK_PAYLOAD_MIN_LENGTH = 23;
    public static final int NACK_PAYLOAD_MAX_LENGTH = 23;
    public static final int NACK_SENDER_BOOT_ID_OFFSET = 0;
    public static final int NACK_REFERENCED_SENDER_BOOT_ID_OFFSET = 8;
    public static final int NACK_REFERENCED_TX_SEQUENCE_OFFSET = 16;
    public static final int NACK_REFERENCED_MESSAGE_TYPE_OFFSET = 20;
    public static final int NACK_ERROR_CODE_OFFSET = 21;
    public static final int QUERY_STATE_PAYLOAD_MIN_LENGTH = 76;
    public static final int QUERY_STATE_PAYLOAD_MAX_LENGTH = 76;
    public static final int QUERY_STATE_MCU_COMMAND_UID_OFFSET = 0;
    public static final int QUERY_STATE_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int QUERY_STATE_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int QUERY_STATE_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int QUERY_STATE_SNAPSHOT_UID_OFFSET = 60;
    public static final int SAFE_CLOSE_PAYLOAD_MIN_LENGTH = 66;
    public static final int SAFE_CLOSE_PAYLOAD_MAX_LENGTH = 66;
    public static final int SAFE_CLOSE_MCU_COMMAND_UID_OFFSET = 0;
    public static final int SAFE_CLOSE_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int SAFE_CLOSE_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int SAFE_CLOSE_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int SAFE_CLOSE_SCOPE_OFFSET = 60;
    public static final int SAFE_CLOSE_PORT_NO_OFFSET = 61;
    public static final int SAFE_CLOSE_EXECUTION_DEADLINE_MS_OFFSET = 62;
    public static final int BOOT_PROBE_PAYLOAD_MIN_LENGTH = 8;
    public static final int BOOT_PROBE_PAYLOAD_MAX_LENGTH = 8;
    public static final int BOOT_PROBE_PROBE_ID_OFFSET = 0;
    public static final int BOOT_PROBE_REPLY_PAYLOAD_MIN_LENGTH = 16;
    public static final int BOOT_PROBE_REPLY_PAYLOAD_MAX_LENGTH = 16;
    public static final int BOOT_PROBE_REPLY_PROBE_ID_OFFSET = 0;
    public static final int BOOT_PROBE_REPLY_MCU_BOOT_ID_OFFSET = 8;
    public static final int BIND_BOOT_PAYLOAD_MIN_LENGTH = 16;
    public static final int BIND_BOOT_PAYLOAD_MAX_LENGTH = 16;
    public static final int BIND_BOOT_PROBE_ID_OFFSET = 0;
    public static final int BIND_BOOT_PROPOSED_MCU_BOOT_ID_OFFSET = 8;
    public static final int BIND_BOOT_REPLY_PAYLOAD_MIN_LENGTH = 25;
    public static final int BIND_BOOT_REPLY_PAYLOAD_MAX_LENGTH = 25;
    public static final int BIND_BOOT_REPLY_PROBE_ID_OFFSET = 0;
    public static final int BIND_BOOT_REPLY_PROPOSED_MCU_BOOT_ID_OFFSET = 8;
    public static final int BIND_BOOT_REPLY_MCU_BOOT_ID_OFFSET = 16;
    public static final int BIND_BOOT_REPLY_STATUS_OFFSET = 24;
    public static final int COMMAND_DECISION_PAYLOAD_MIN_LENGTH = 71;
    public static final int COMMAND_DECISION_PAYLOAD_MAX_LENGTH = 71;
    public static final int COMMAND_DECISION_MCU_COMMAND_UID_OFFSET = 0;
    public static final int COMMAND_DECISION_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int COMMAND_DECISION_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int COMMAND_DECISION_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int COMMAND_DECISION_CURRENT_MCU_BOOT_ID_OFFSET = 60;
    public static final int COMMAND_DECISION_OUTCOME_OFFSET = 68;
    public static final int COMMAND_DECISION_ERROR_CODE_OFFSET = 69;
    public static final int QUERY_COMMAND_PAYLOAD_MIN_LENGTH = 68;
    public static final int QUERY_COMMAND_PAYLOAD_MAX_LENGTH = 68;
    public static final int QUERY_COMMAND_QUERY_ID_OFFSET = 0;
    public static final int QUERY_COMMAND_MCU_COMMAND_UID_OFFSET = 8;
    public static final int QUERY_COMMAND_COMMAND_DIGEST_SHA256_OFFSET = 24;
    public static final int QUERY_COMMAND_TARGET_MCU_BOOT_ID_OFFSET = 56;
    public static final int QUERY_COMMAND_COMMAND_SEQUENCE_OFFSET = 64;
    public static final int COMMAND_QUERY_RESULT_PAYLOAD_MIN_LENGTH = 83;
    public static final int COMMAND_QUERY_RESULT_PAYLOAD_MAX_LENGTH = 83;
    public static final int COMMAND_QUERY_RESULT_QUERY_ID_OFFSET = 0;
    public static final int COMMAND_QUERY_RESULT_MCU_COMMAND_UID_OFFSET = 8;
    public static final int COMMAND_QUERY_RESULT_COMMAND_DIGEST_SHA256_OFFSET = 24;
    public static final int COMMAND_QUERY_RESULT_TARGET_MCU_BOOT_ID_OFFSET = 56;
    public static final int COMMAND_QUERY_RESULT_COMMAND_SEQUENCE_OFFSET = 64;
    public static final int COMMAND_QUERY_RESULT_CURRENT_MCU_BOOT_ID_OFFSET = 68;
    public static final int COMMAND_QUERY_RESULT_OUTCOME_OFFSET = 76;
    public static final int COMMAND_QUERY_RESULT_ERROR_CODE_OFFSET = 77;
    public static final int COMMAND_QUERY_RESULT_HIGHEST_COMMAND_SEQUENCE_OFFSET = 79;
    public static final int RESULT_SAVED_PAYLOAD_MIN_LENGTH = 60;
    public static final int RESULT_SAVED_PAYLOAD_MAX_LENGTH = 60;
    public static final int RESULT_SAVED_MCU_BOOT_ID_OFFSET = 0;
    public static final int RESULT_SAVED_RESULT_SEQUENCE_OFFSET = 8;
    public static final int RESULT_SAVED_WORK_UID_OFFSET = 12;
    public static final int RESULT_SAVED_RESULT_DIGEST_SHA256_OFFSET = 28;
    public static final int RESULT_SAVED_REPLY_PAYLOAD_MIN_LENGTH = 69;
    public static final int RESULT_SAVED_REPLY_PAYLOAD_MAX_LENGTH = 69;
    public static final int RESULT_SAVED_REPLY_MCU_BOOT_ID_OFFSET = 0;
    public static final int RESULT_SAVED_REPLY_RESULT_SEQUENCE_OFFSET = 8;
    public static final int RESULT_SAVED_REPLY_WORK_UID_OFFSET = 12;
    public static final int RESULT_SAVED_REPLY_RESULT_DIGEST_SHA256_OFFSET = 28;
    public static final int RESULT_SAVED_REPLY_CURRENT_MCU_BOOT_ID_OFFSET = 60;
    public static final int RESULT_SAVED_REPLY_STATUS_OFFSET = 68;
    public static final int CONFIG_BEGIN_PAYLOAD_MIN_LENGTH = 151;
    public static final int CONFIG_BEGIN_PAYLOAD_MAX_LENGTH = 151;
    public static final int CONFIG_BEGIN_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_BEGIN_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_BEGIN_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int CONFIG_BEGIN_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int CONFIG_BEGIN_APPLICATION_UID_OFFSET = 60;
    public static final int CONFIG_BEGIN_CONFIG_VERSION_OFFSET = 76;
    public static final int CONFIG_BEGIN_CONTENT_SHA256_OFFSET = 84;
    public static final int CONFIG_BEGIN_MCU_PAYLOAD_SHA256_OFFSET = 116;
    public static final int CONFIG_BEGIN_PART_INDEX_OFFSET = 148;
    public static final int CONFIG_BEGIN_PART_COUNT_OFFSET = 149;
    public static final int CONFIG_BEGIN_EXPECTED_PORT_COUNT_OFFSET = 150;
    public static final int CONFIG_DEVICE_BLOCK_PAYLOAD_MIN_LENGTH = 183;
    public static final int CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH = 183;
    public static final int CONFIG_DEVICE_BLOCK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_DEVICE_BLOCK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_DEVICE_BLOCK_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int CONFIG_DEVICE_BLOCK_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int CONFIG_DEVICE_BLOCK_APPLICATION_UID_OFFSET = 60;
    public static final int CONFIG_DEVICE_BLOCK_CONFIG_VERSION_OFFSET = 76;
    public static final int CONFIG_DEVICE_BLOCK_CONTENT_SHA256_OFFSET = 84;
    public static final int CONFIG_DEVICE_BLOCK_MCU_PAYLOAD_SHA256_OFFSET = 116;
    public static final int CONFIG_DEVICE_BLOCK_PART_INDEX_OFFSET = 148;
    public static final int CONFIG_DEVICE_BLOCK_PART_COUNT_OFFSET = 149;
    public static final int CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET = 150;
    public static final int CONFIG_DEVICE_BLOCK_NEGATIVE_WEIGHT_THRESHOLD_GRAMS_OFFSET = 154;
    public static final int CONFIG_DEVICE_BLOCK_DELIVERY_AUTO_CLOSE_MS_OFFSET = 158;
    public static final int CONFIG_DEVICE_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET = 162;
    public static final int CONFIG_DEVICE_BLOCK_DELIVERY_DOOR_TRAVEL_WAIT_MS_OFFSET = 166;
    public static final int CONFIG_DEVICE_BLOCK_CLEAN_SOLENOID_PULSE_MS_OFFSET = 170;
    public static final int CONFIG_DEVICE_BLOCK_SMOKE_MONITORING_ENABLED_OFFSET = 174;
    public static final int CONFIG_DEVICE_BLOCK_WEIGHT_POLL_INTERVAL_MS_OFFSET = 175;
    public static final int CONFIG_DEVICE_BLOCK_WEIGHT_RESPONSE_TIMEOUT_MS_OFFSET = 179;
    public static final int CONFIG_PORT_BLOCK_PAYLOAD_MIN_LENGTH = 207;
    public static final int CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH = 207;
    public static final int CONFIG_PORT_BLOCK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_PORT_BLOCK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_PORT_BLOCK_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int CONFIG_PORT_BLOCK_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int CONFIG_PORT_BLOCK_APPLICATION_UID_OFFSET = 60;
    public static final int CONFIG_PORT_BLOCK_CONFIG_VERSION_OFFSET = 76;
    public static final int CONFIG_PORT_BLOCK_CONTENT_SHA256_OFFSET = 84;
    public static final int CONFIG_PORT_BLOCK_MCU_PAYLOAD_SHA256_OFFSET = 116;
    public static final int CONFIG_PORT_BLOCK_PART_INDEX_OFFSET = 148;
    public static final int CONFIG_PORT_BLOCK_PART_COUNT_OFFSET = 149;
    public static final int CONFIG_PORT_BLOCK_PORT_NO_OFFSET = 150;
    public static final int CONFIG_PORT_BLOCK_ENABLED_OFFSET = 151;
    public static final int CONFIG_PORT_BLOCK_UNIT_PRICE_TEN_THOUSANDTHS_OFFSET = 152;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_MODE_OFFSET = 156;
    public static final int CONFIG_PORT_BLOCK_CONFIGURED_FULL_WEIGHT_GRAMS_OFFSET = 157;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_SETTLE_WAIT_MS_OFFSET = 161;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_SENSOR_KIND_OFFSET = 165;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_DISTANCE_THRESHOLD_MM_OFFSET = 166;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_SAMPLE_COUNT_OFFSET = 170;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_MINIMUM_VALID_SAMPLE_COUNT_OFFSET = 171;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_ECHO_TIMEOUT_US_OFFSET = 172;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_STABLE_WINDOW_MS_OFFSET = 176;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_FLUCTUATION_GRAMS_OFFSET = 180;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_REQUIRED_SAMPLE_COUNT_OFFSET = 184;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET = 186;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MINIMUM_GRAMS_OFFSET = 190;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_GRAMS_OFFSET = 194;
    public static final int CONFIG_PORT_BLOCK_CALIBRATION_VERSION_OFFSET = 198;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_SAMPLE_AGE_MS_OFFSET = 202;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MINIMUM_MEDIAN_SAMPLE_COUNT_OFFSET = 206;
    public static final int CONFIG_COMMIT_PAYLOAD_MIN_LENGTH = 150;
    public static final int CONFIG_COMMIT_PAYLOAD_MAX_LENGTH = 150;
    public static final int CONFIG_COMMIT_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_COMMIT_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_COMMIT_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int CONFIG_COMMIT_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int CONFIG_COMMIT_APPLICATION_UID_OFFSET = 60;
    public static final int CONFIG_COMMIT_CONFIG_VERSION_OFFSET = 76;
    public static final int CONFIG_COMMIT_CONTENT_SHA256_OFFSET = 84;
    public static final int CONFIG_COMMIT_MCU_PAYLOAD_SHA256_OFFSET = 116;
    public static final int CONFIG_COMMIT_PART_INDEX_OFFSET = 148;
    public static final int CONFIG_COMMIT_PART_COUNT_OFFSET = 149;
    public static final int CONFIG_APPLY_RESULT_PAYLOAD_MIN_LENGTH = 127;
    public static final int CONFIG_APPLY_RESULT_PAYLOAD_MAX_LENGTH = 127;
    public static final int CONFIG_APPLY_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int CONFIG_APPLY_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CONFIG_APPLY_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int CONFIG_APPLY_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int CONFIG_APPLY_RESULT_APPLICATION_UID_OFFSET = 36;
    public static final int CONFIG_APPLY_RESULT_STATUS_OFFSET = 52;
    public static final int CONFIG_APPLY_RESULT_CONFIG_VERSION_OFFSET = 53;
    public static final int CONFIG_APPLY_RESULT_CONTENT_SHA256_OFFSET = 61;
    public static final int CONFIG_APPLY_RESULT_MCU_PAYLOAD_SHA256_OFFSET = 93;
    public static final int CONFIG_APPLY_RESULT_FAULT_CODE_OFFSET = 125;
    public static final int DEVICE_ENTRY_URL_BEGIN_PAYLOAD_MIN_LENGTH = 111;
    public static final int DEVICE_ENTRY_URL_BEGIN_PAYLOAD_MAX_LENGTH = 111;
    public static final int DEVICE_ENTRY_URL_BEGIN_MCU_COMMAND_UID_OFFSET = 0;
    public static final int DEVICE_ENTRY_URL_BEGIN_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int DEVICE_ENTRY_URL_BEGIN_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int DEVICE_ENTRY_URL_BEGIN_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int DEVICE_ENTRY_URL_BEGIN_APPLICATION_UID_OFFSET = 60;
    public static final int DEVICE_ENTRY_URL_BEGIN_URL_LENGTH_OFFSET = 76;
    public static final int DEVICE_ENTRY_URL_BEGIN_URL_SHA256_OFFSET = 78;
    public static final int DEVICE_ENTRY_URL_BEGIN_PART_COUNT_OFFSET = 110;
    public static final int DEVICE_ENTRY_URL_PART_PAYLOAD_MIN_LENGTH = 111;
    public static final int DEVICE_ENTRY_URL_PART_PAYLOAD_MAX_LENGTH = 175;
    public static final int DEVICE_ENTRY_URL_PART_MCU_COMMAND_UID_OFFSET = 0;
    public static final int DEVICE_ENTRY_URL_PART_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int DEVICE_ENTRY_URL_PART_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int DEVICE_ENTRY_URL_PART_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int DEVICE_ENTRY_URL_PART_APPLICATION_UID_OFFSET = 60;
    public static final int DEVICE_ENTRY_URL_PART_URL_SHA256_OFFSET = 76;
    public static final int DEVICE_ENTRY_URL_PART_PART_INDEX_OFFSET = 108;
    public static final int DEVICE_ENTRY_URL_PART_PART_COUNT_OFFSET = 109;
    public static final int DEVICE_ENTRY_URL_PART_URL_CHUNK_OFFSET = 110;
    public static final int DEVICE_ENTRY_URL_COMMIT_PAYLOAD_MIN_LENGTH = 111;
    public static final int DEVICE_ENTRY_URL_COMMIT_PAYLOAD_MAX_LENGTH = 111;
    public static final int DEVICE_ENTRY_URL_COMMIT_MCU_COMMAND_UID_OFFSET = 0;
    public static final int DEVICE_ENTRY_URL_COMMIT_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int DEVICE_ENTRY_URL_COMMIT_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int DEVICE_ENTRY_URL_COMMIT_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int DEVICE_ENTRY_URL_COMMIT_APPLICATION_UID_OFFSET = 60;
    public static final int DEVICE_ENTRY_URL_COMMIT_URL_LENGTH_OFFSET = 76;
    public static final int DEVICE_ENTRY_URL_COMMIT_URL_SHA256_OFFSET = 78;
    public static final int DEVICE_ENTRY_URL_COMMIT_PART_COUNT_OFFSET = 110;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_PAYLOAD_MIN_LENGTH = 89;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_PAYLOAD_MAX_LENGTH = 89;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_APPLICATION_UID_OFFSET = 36;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_URL_LENGTH_OFFSET = 52;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_URL_SHA256_OFFSET = 54;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_STATUS_OFFSET = 86;
    public static final int DEVICE_ENTRY_URL_APPLY_RESULT_ERROR_CODE_OFFSET = 87;
    public static final int START_DELIVERY_SESSION_PAYLOAD_MIN_LENGTH = 137;
    public static final int START_DELIVERY_SESSION_PAYLOAD_MAX_LENGTH = 137;
    public static final int START_DELIVERY_SESSION_MCU_COMMAND_UID_OFFSET = 0;
    public static final int START_DELIVERY_SESSION_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int START_DELIVERY_SESSION_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int START_DELIVERY_SESSION_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int START_DELIVERY_SESSION_SESSION_UID_OFFSET = 60;
    public static final int START_DELIVERY_SESSION_PORT_NO_OFFSET = 76;
    public static final int START_DELIVERY_SESSION_CONFIG_VERSION_OFFSET = 77;
    public static final int START_DELIVERY_SESSION_CONFIG_CONTENT_SHA256_OFFSET = 85;
    public static final int START_DELIVERY_SESSION_UNIT_PRICE_TEN_THOUSANDTHS_OFFSET = 117;
    public static final int START_DELIVERY_SESSION_CONTINUE_DELIVERY_WAIT_MS_OFFSET = 121;
    public static final int START_DELIVERY_SESSION_NEGATIVE_WEIGHT_THRESHOLD_GRAMS_OFFSET = 125;
    public static final int START_DELIVERY_SESSION_START_EXECUTION_WINDOW_MS_OFFSET = 129;
    public static final int START_DELIVERY_SESSION_DELIVERY_AUTO_CLOSE_MS_OFFSET = 133;
    public static final int START_CLEAN_OPERATION_PAYLOAD_MIN_LENGTH = 125;
    public static final int START_CLEAN_OPERATION_PAYLOAD_MAX_LENGTH = 125;
    public static final int START_CLEAN_OPERATION_MCU_COMMAND_UID_OFFSET = 0;
    public static final int START_CLEAN_OPERATION_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int START_CLEAN_OPERATION_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int START_CLEAN_OPERATION_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int START_CLEAN_OPERATION_OPERATION_UID_OFFSET = 60;
    public static final int START_CLEAN_OPERATION_PORT_NO_OFFSET = 76;
    public static final int START_CLEAN_OPERATION_CONFIG_VERSION_OFFSET = 77;
    public static final int START_CLEAN_OPERATION_CONFIG_CONTENT_SHA256_OFFSET = 85;
    public static final int START_CLEAN_OPERATION_START_EXECUTION_WINDOW_MS_OFFSET = 117;
    public static final int START_CLEAN_OPERATION_OPERATION_WINDOW_MS_OFFSET = 121;
    public static final int UNLOCK_CLEAN_DOOR_PAYLOAD_MIN_LENGTH = 107;
    public static final int UNLOCK_CLEAN_DOOR_PAYLOAD_MAX_LENGTH = 107;
    public static final int UNLOCK_CLEAN_DOOR_MCU_COMMAND_UID_OFFSET = 0;
    public static final int UNLOCK_CLEAN_DOOR_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int UNLOCK_CLEAN_DOOR_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int UNLOCK_CLEAN_DOOR_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int UNLOCK_CLEAN_DOOR_OPERATION_UID_OFFSET = 60;
    public static final int UNLOCK_CLEAN_DOOR_PORT_NO_OFFSET = 76;
    public static final int UNLOCK_CLEAN_DOOR_CLEAN_ACTION_SEQUENCE_OFFSET = 77;
    public static final int UNLOCK_CLEAN_DOOR_RECOVERY_GENERATION_OFFSET = 79;
    public static final int UNLOCK_CLEAN_DOOR_UNLOCK_PULSE_MS_OFFSET = 83;
    public static final int UNLOCK_CLEAN_DOOR_REMAINING_OPERATION_WINDOW_MS_OFFSET = 87;
    public static final int UNLOCK_CLEAN_DOOR_PARENT_COMMAND_UID_OFFSET = 91;
    public static final int RESUME_CLEAN_OPERATION_PAYLOAD_MIN_LENGTH = 123;
    public static final int RESUME_CLEAN_OPERATION_PAYLOAD_MAX_LENGTH = 123;
    public static final int RESUME_CLEAN_OPERATION_MCU_COMMAND_UID_OFFSET = 0;
    public static final int RESUME_CLEAN_OPERATION_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int RESUME_CLEAN_OPERATION_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int RESUME_CLEAN_OPERATION_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int RESUME_CLEAN_OPERATION_OPERATION_UID_OFFSET = 60;
    public static final int RESUME_CLEAN_OPERATION_PORT_NO_OFFSET = 76;
    public static final int RESUME_CLEAN_OPERATION_RECOVERY_GENERATION_OFFSET = 77;
    public static final int RESUME_CLEAN_OPERATION_NEXT_CLEAN_ACTION_SEQUENCE_OFFSET = 81;
    public static final int RESUME_CLEAN_OPERATION_CONFIG_VERSION_OFFSET = 83;
    public static final int RESUME_CLEAN_OPERATION_CONFIG_CONTENT_SHA256_OFFSET = 91;
    public static final int END_CLEAN_BEFORE_UNLOCK_PAYLOAD_MIN_LENGTH = 102;
    public static final int END_CLEAN_BEFORE_UNLOCK_PAYLOAD_MAX_LENGTH = 102;
    public static final int END_CLEAN_BEFORE_UNLOCK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int END_CLEAN_BEFORE_UNLOCK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int END_CLEAN_BEFORE_UNLOCK_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int END_CLEAN_BEFORE_UNLOCK_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int END_CLEAN_BEFORE_UNLOCK_OPERATION_UID_OFFSET = 60;
    public static final int END_CLEAN_BEFORE_UNLOCK_PORT_NO_OFFSET = 76;
    public static final int END_CLEAN_BEFORE_UNLOCK_PARENT_COMMAND_UID_OFFSET = 77;
    public static final int END_CLEAN_BEFORE_UNLOCK_RECOVERY_GENERATION_OFFSET = 93;
    public static final int END_CLEAN_BEFORE_UNLOCK_EXECUTION_DEADLINE_MS_OFFSET = 97;
    public static final int END_CLEAN_BEFORE_UNLOCK_REASON_OFFSET = 101;
    public static final int SAMPLE_FULLNESS_PAYLOAD_MIN_LENGTH = 130;
    public static final int SAMPLE_FULLNESS_PAYLOAD_MAX_LENGTH = 130;
    public static final int SAMPLE_FULLNESS_MCU_COMMAND_UID_OFFSET = 0;
    public static final int SAMPLE_FULLNESS_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int SAMPLE_FULLNESS_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int SAMPLE_FULLNESS_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int SAMPLE_FULLNESS_DETECTION_UID_OFFSET = 60;
    public static final int SAMPLE_FULLNESS_PORT_NO_OFFSET = 76;
    public static final int SAMPLE_FULLNESS_SAMPLE_ROLE_OFFSET = 77;
    public static final int SAMPLE_FULLNESS_CONFIG_VERSION_OFFSET = 78;
    public static final int SAMPLE_FULLNESS_CONFIG_CONTENT_SHA256_OFFSET = 86;
    public static final int SAMPLE_FULLNESS_START_EXECUTION_WINDOW_MS_OFFSET = 118;
    public static final int SAMPLE_FULLNESS_SETTLE_WAIT_MS_OFFSET = 122;
    public static final int SAMPLE_FULLNESS_MEASUREMENT_TIMEOUT_MS_OFFSET = 126;
    public static final int MEASURE_BASELINE_PAYLOAD_MIN_LENGTH = 125;
    public static final int MEASURE_BASELINE_PAYLOAD_MAX_LENGTH = 125;
    public static final int MEASURE_BASELINE_MCU_COMMAND_UID_OFFSET = 0;
    public static final int MEASURE_BASELINE_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int MEASURE_BASELINE_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int MEASURE_BASELINE_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int MEASURE_BASELINE_MEASUREMENT_UID_OFFSET = 60;
    public static final int MEASURE_BASELINE_PORT_NO_OFFSET = 76;
    public static final int MEASURE_BASELINE_CONFIG_VERSION_OFFSET = 77;
    public static final int MEASURE_BASELINE_CONFIG_CONTENT_SHA256_OFFSET = 85;
    public static final int MEASURE_BASELINE_START_EXECUTION_WINDOW_MS_OFFSET = 117;
    public static final int MEASURE_BASELINE_MEASUREMENT_TIMEOUT_MS_OFFSET = 121;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PAYLOAD_MIN_LENGTH = 113;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PAYLOAD_MAX_LENGTH = 113;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_MCU_COMMAND_UID_OFFSET = 0;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_SESSION_UID_OFFSET = 60;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PORT_NO_OFFSET = 76;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_FIRST_PRE_OPEN_MEASUREMENT_UID_OFFSET = 77;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PARENT_START_COMMAND_UID_OFFSET = 93;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_REMAINING_START_AUTHORIZATION_MS_OFFSET = 109;
    public static final int CONFIRM_NO_ACTIVE_WORK_PAYLOAD_MIN_LENGTH = 100;
    public static final int CONFIRM_NO_ACTIVE_WORK_PAYLOAD_MAX_LENGTH = 100;
    public static final int CONFIRM_NO_ACTIVE_WORK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIRM_NO_ACTIVE_WORK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIRM_NO_ACTIVE_WORK_TARGET_MCU_BOOT_ID_OFFSET = 48;
    public static final int CONFIRM_NO_ACTIVE_WORK_COMMAND_SEQUENCE_OFFSET = 56;
    public static final int CONFIRM_NO_ACTIVE_WORK_CONFIG_VERSION_OFFSET = 60;
    public static final int CONFIRM_NO_ACTIVE_WORK_CONFIG_CONTENT_SHA256_OFFSET = 68;
    public static final int WORK_PREOPEN_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 97;
    public static final int WORK_PREOPEN_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 97;
    public static final int WORK_PREOPEN_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int WORK_PREOPEN_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int WORK_PREOPEN_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int WORK_PREOPEN_WEIGHT_READY_MCU_COMMAND_UID_OFFSET = 20;
    public static final int WORK_PREOPEN_WEIGHT_READY_SESSION_UID_OFFSET = 36;
    public static final int WORK_PREOPEN_WEIGHT_READY_PORT_NO_OFFSET = 52;
    public static final int WORK_PREOPEN_WEIGHT_READY_ROUND_INDEX_OFFSET = 53;
    public static final int WORK_PREOPEN_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 55;
    public static final int WORK_PREOPEN_WEIGHT_READY_MEASUREMENT_KIND_OFFSET = 71;
    public static final int WORK_PREOPEN_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 72;
    public static final int WORK_PREOPEN_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 76;
    public static final int WORK_PREOPEN_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 78;
    public static final int WORK_PREOPEN_WEIGHT_READY_SAMPLE_SPAN_GRAMS_OFFSET = 79;
    public static final int WORK_PREOPEN_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 83;
    public static final int WORK_PREOPEN_WEIGHT_READY_FAULT_CODE_OFFSET = 87;
    public static final int WORK_PREOPEN_WEIGHT_READY_CONFIG_VERSION_OFFSET = 89;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_PAYLOAD_MIN_LENGTH = 60;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_PAYLOAD_MAX_LENGTH = 60;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_SESSION_UID_OFFSET = 36;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_PORT_NO_OFFSET = 52;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_ROUND_INDEX_OFFSET = 53;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_COMMAND_OFFSET = 55;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_OUTPUT_STATUS_OFFSET = 56;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_PHYSICAL_DOOR_STATE_BASIS_OFFSET = 57;
    public static final int DELIVERY_DOOR_COMMAND_RESULT_FAULT_CODE_OFFSET = 58;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_PAYLOAD_MIN_LENGTH = 64;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_PAYLOAD_MAX_LENGTH = 64;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_SESSION_UID_OFFSET = 36;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_PORT_NO_OFFSET = 52;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_ROUND_INDEX_OFFSET = 53;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_COMMAND_OFFSET = 55;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_OUTPUT_STATUS_OFFSET = 56;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_PHYSICAL_DOOR_STATE_BASIS_OFFSET = 57;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_FAULT_CODE_OFFSET = 58;
    public static final int DELIVERY_LOCAL_DOOR_RESULT_SELECTION_EVENT_SEQUENCE_OFFSET = 60;
    public static final int CLEAN_OPERATION_INTERRUPTED_PAYLOAD_MIN_LENGTH = 61;
    public static final int CLEAN_OPERATION_INTERRUPTED_PAYLOAD_MAX_LENGTH = 61;
    public static final int CLEAN_OPERATION_INTERRUPTED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_OPERATION_INTERRUPTED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_OPERATION_INTERRUPTED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_OPERATION_INTERRUPTED_MCU_COMMAND_UID_OFFSET = 20;
    public static final int CLEAN_OPERATION_INTERRUPTED_OPERATION_UID_OFFSET = 36;
    public static final int CLEAN_OPERATION_INTERRUPTED_PORT_NO_OFFSET = 52;
    public static final int CLEAN_OPERATION_INTERRUPTED_CLEAN_ACTION_SEQUENCE_OFFSET = 53;
    public static final int CLEAN_OPERATION_INTERRUPTED_INTERRUPTED_PHASE_OFFSET = 55;
    public static final int CLEAN_OPERATION_INTERRUPTED_INTERRUPTION_REASON_OFFSET = 56;
    public static final int CLEAN_OPERATION_INTERRUPTED_FINAL_MEASUREMENT_EVENT_SEQUENCE_OFFSET = 57;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_PAYLOAD_MIN_LENGTH = 61;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_PAYLOAD_MAX_LENGTH = 61;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_MCU_BOOT_ID_OFFSET = 0;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_UPTIME_MS_OFFSET = 12;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_MCU_COMMAND_UID_OFFSET = 20;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_SESSION_UID_OFFSET = 36;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_PORT_NO_OFFSET = 52;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_ROUND_INDEX_OFFSET = 53;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_INTERRUPTED_PHASE_OFFSET = 55;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_INTERRUPTION_REASON_OFFSET = 56;
    public static final int DELIVERY_POSTCLOSE_INTERRUPTED_POST_CLOSE_MEASUREMENT_EVENT_SEQUENCE_OFFSET = 57;
    public static final int DELIVERY_CYCLE_ABORTED_PAYLOAD_MIN_LENGTH = 61;
    public static final int DELIVERY_CYCLE_ABORTED_PAYLOAD_MAX_LENGTH = 61;
    public static final int DELIVERY_CYCLE_ABORTED_MCU_BOOT_ID_OFFSET = 0;
    public static final int DELIVERY_CYCLE_ABORTED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int DELIVERY_CYCLE_ABORTED_UPTIME_MS_OFFSET = 12;
    public static final int DELIVERY_CYCLE_ABORTED_MCU_COMMAND_UID_OFFSET = 20;
    public static final int DELIVERY_CYCLE_ABORTED_SESSION_UID_OFFSET = 36;
    public static final int DELIVERY_CYCLE_ABORTED_PORT_NO_OFFSET = 52;
    public static final int DELIVERY_CYCLE_ABORTED_ROUND_INDEX_OFFSET = 53;
    public static final int DELIVERY_CYCLE_ABORTED_ABORT_REASON_OFFSET = 55;
    public static final int DELIVERY_CYCLE_ABORTED_OPEN_DISPATCHED_OFFSET = 56;
    public static final int DELIVERY_CYCLE_ABORTED_SELECTION_EVENT_SEQUENCE_OFFSET = 57;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 207;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 207;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MCU_COMMAND_UID_OFFSET = 20;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_SESSION_UID_OFFSET = 36;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_PORT_NO_OFFSET = 52;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_ROUND_INDEX_OFFSET = 53;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 55;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_KIND_OFFSET = 71;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 72;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 76;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 78;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_SAMPLE_SPAN_GRAMS_OFFSET = 79;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 83;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FAULT_CODE_OFFSET = 87;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_CONFIG_VERSION_OFFSET = 89;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_WORK_FULLNESS_STATUS_OFFSET = 97;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_GROUP_SEQUENCE_OFFSET = 98;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_CONFIG_CONTENT_SHA256_OFFSET = 102;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_MCU_PAYLOAD_SHA256_OFFSET = 134;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_STARTED_UPTIME_MS_OFFSET = 166;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_COMPLETED_UPTIME_MS_OFFSET = 174;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_LAST_CAPTURED_UPTIME_MS_OFFSET = 182;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_WORK_FULLNESS_SENSOR_KIND_OFFSET = 190;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_WORK_FULLNESS_SENSOR_VALUE_OFFSET = 191;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_WORK_FULLNESS_BASIS_OFFSET = 192;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_DISTANCE_PRESENT_OFFSET = 193;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_DISTANCE_MM_OFFSET = 194;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_REQUESTED_SAMPLE_COUNT_OFFSET = 198;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_COMPLETED_SAMPLE_COUNT_OFFSET = 199;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_VALID_SAMPLE_COUNT_OFFSET = 200;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_MINIMUM_VALID_SAMPLE_COUNT_OFFSET = 201;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_DISTANCE_THRESHOLD_MM_OFFSET = 202;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_STOP_REASON_OFFSET = 206;
    public static final int DELIVERY_SELECTION_PAYLOAD_MIN_LENGTH = 80;
    public static final int DELIVERY_SELECTION_PAYLOAD_MAX_LENGTH = 80;
    public static final int DELIVERY_SELECTION_MCU_BOOT_ID_OFFSET = 0;
    public static final int DELIVERY_SELECTION_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int DELIVERY_SELECTION_UPTIME_MS_OFFSET = 12;
    public static final int DELIVERY_SELECTION_MCU_COMMAND_UID_OFFSET = 20;
    public static final int DELIVERY_SELECTION_SESSION_UID_OFFSET = 36;
    public static final int DELIVERY_SELECTION_PORT_NO_OFFSET = 52;
    public static final int DELIVERY_SELECTION_ROUND_INDEX_OFFSET = 53;
    public static final int DELIVERY_SELECTION_POST_CLOSE_MEASUREMENT_UID_OFFSET = 55;
    public static final int DELIVERY_SELECTION_CONFIG_VERSION_OFFSET = 71;
    public static final int DELIVERY_SELECTION_SELECTION_OFFSET = 79;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 95;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 95;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MCU_COMMAND_UID_OFFSET = 20;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_OPERATION_UID_OFFSET = 36;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_PORT_NO_OFFSET = 52;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 53;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MEASUREMENT_KIND_OFFSET = 69;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 70;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 74;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 76;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_SAMPLE_SPAN_GRAMS_OFFSET = 77;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 81;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_FAULT_CODE_OFFSET = 85;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_CONFIG_VERSION_OFFSET = 87;
    public static final int CLEAN_LOCK_POWER_CHANGED_PAYLOAD_MIN_LENGTH = 55;
    public static final int CLEAN_LOCK_POWER_CHANGED_PAYLOAD_MAX_LENGTH = 55;
    public static final int CLEAN_LOCK_POWER_CHANGED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_LOCK_POWER_CHANGED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_LOCK_POWER_CHANGED_MCU_COMMAND_UID_OFFSET = 20;
    public static final int CLEAN_LOCK_POWER_CHANGED_OPERATION_UID_OFFSET = 36;
    public static final int CLEAN_LOCK_POWER_CHANGED_PORT_NO_OFFSET = 52;
    public static final int CLEAN_LOCK_POWER_CHANGED_LOCK_POWER_STATE_OFFSET = 53;
    public static final int CLEAN_LOCK_POWER_CHANGED_SOLENOID_HEALTH_OFFSET = 54;
    public static final int CLEAN_UNLOCK_REQUESTED_PAYLOAD_MIN_LENGTH = 63;
    public static final int CLEAN_UNLOCK_REQUESTED_PAYLOAD_MAX_LENGTH = 63;
    public static final int CLEAN_UNLOCK_REQUESTED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_UNLOCK_REQUESTED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_UNLOCK_REQUESTED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_UNLOCK_REQUESTED_MCU_COMMAND_UID_OFFSET = 20;
    public static final int CLEAN_UNLOCK_REQUESTED_OPERATION_UID_OFFSET = 36;
    public static final int CLEAN_UNLOCK_REQUESTED_PORT_NO_OFFSET = 52;
    public static final int CLEAN_UNLOCK_REQUESTED_CLEAN_ACTION_SEQUENCE_OFFSET = 53;
    public static final int CLEAN_UNLOCK_REQUESTED_CONFIG_VERSION_OFFSET = 55;
    public static final int CLEAN_FINISH_REQUESTED_PAYLOAD_MIN_LENGTH = 63;
    public static final int CLEAN_FINISH_REQUESTED_PAYLOAD_MAX_LENGTH = 63;
    public static final int CLEAN_FINISH_REQUESTED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_FINISH_REQUESTED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_FINISH_REQUESTED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_FINISH_REQUESTED_MCU_COMMAND_UID_OFFSET = 20;
    public static final int CLEAN_FINISH_REQUESTED_OPERATION_UID_OFFSET = 36;
    public static final int CLEAN_FINISH_REQUESTED_PORT_NO_OFFSET = 52;
    public static final int CLEAN_FINISH_REQUESTED_CLEAN_ACTION_SEQUENCE_OFFSET = 53;
    public static final int CLEAN_FINISH_REQUESTED_CONFIG_VERSION_OFFSET = 55;
    public static final int CLEAN_FINAL_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 191;
    public static final int CLEAN_FINAL_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 191;
    public static final int CLEAN_FINAL_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_FINAL_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_FINAL_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_FINAL_WEIGHT_READY_OPERATION_UID_OFFSET = 20;
    public static final int CLEAN_FINAL_WEIGHT_READY_PORT_NO_OFFSET = 36;
    public static final int CLEAN_FINAL_WEIGHT_READY_CLEAN_ACTION_SEQUENCE_OFFSET = 37;
    public static final int CLEAN_FINAL_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 39;
    public static final int CLEAN_FINAL_WEIGHT_READY_MEASUREMENT_KIND_OFFSET = 55;
    public static final int CLEAN_FINAL_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 56;
    public static final int CLEAN_FINAL_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 60;
    public static final int CLEAN_FINAL_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 62;
    public static final int CLEAN_FINAL_WEIGHT_READY_SAMPLE_SPAN_GRAMS_OFFSET = 63;
    public static final int CLEAN_FINAL_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 67;
    public static final int CLEAN_FINAL_WEIGHT_READY_FAULT_CODE_OFFSET = 71;
    public static final int CLEAN_FINAL_WEIGHT_READY_CONFIG_VERSION_OFFSET = 73;
    public static final int CLEAN_FINAL_WEIGHT_READY_WORK_FULLNESS_STATUS_OFFSET = 81;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_GROUP_SEQUENCE_OFFSET = 82;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_CONFIG_CONTENT_SHA256_OFFSET = 86;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_MCU_PAYLOAD_SHA256_OFFSET = 118;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_STARTED_UPTIME_MS_OFFSET = 150;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_COMPLETED_UPTIME_MS_OFFSET = 158;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_LAST_CAPTURED_UPTIME_MS_OFFSET = 166;
    public static final int CLEAN_FINAL_WEIGHT_READY_WORK_FULLNESS_SENSOR_KIND_OFFSET = 174;
    public static final int CLEAN_FINAL_WEIGHT_READY_WORK_FULLNESS_SENSOR_VALUE_OFFSET = 175;
    public static final int CLEAN_FINAL_WEIGHT_READY_WORK_FULLNESS_BASIS_OFFSET = 176;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_DISTANCE_PRESENT_OFFSET = 177;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_DISTANCE_MM_OFFSET = 178;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_REQUESTED_SAMPLE_COUNT_OFFSET = 182;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_COMPLETED_SAMPLE_COUNT_OFFSET = 183;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_VALID_SAMPLE_COUNT_OFFSET = 184;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_MINIMUM_VALID_SAMPLE_COUNT_OFFSET = 185;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_DISTANCE_THRESHOLD_MM_OFFSET = 186;
    public static final int CLEAN_FINAL_WEIGHT_READY_FULLNESS_STOP_REASON_OFFSET = 190;
    public static final int FULLNESS_SAMPLE_RESULT_PAYLOAD_MIN_LENGTH = 106;
    public static final int FULLNESS_SAMPLE_RESULT_PAYLOAD_MAX_LENGTH = 106;
    public static final int FULLNESS_SAMPLE_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int FULLNESS_SAMPLE_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int FULLNESS_SAMPLE_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int FULLNESS_SAMPLE_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int FULLNESS_SAMPLE_RESULT_DETECTION_UID_OFFSET = 36;
    public static final int FULLNESS_SAMPLE_RESULT_PORT_NO_OFFSET = 52;
    public static final int FULLNESS_SAMPLE_RESULT_SAMPLE_ROLE_OFFSET = 53;
    public static final int FULLNESS_SAMPLE_RESULT_FULLNESS_SENSOR_KIND_OFFSET = 54;
    public static final int FULLNESS_SAMPLE_RESULT_FULLNESS_SENSOR_VALUE_OFFSET = 55;
    public static final int FULLNESS_SAMPLE_RESULT_FULLNESS_SAMPLE_BASIS_OFFSET = 56;
    public static final int FULLNESS_SAMPLE_RESULT_REPRESENTATIVE_DISTANCE_PRESENT_OFFSET = 57;
    public static final int FULLNESS_SAMPLE_RESULT_REPRESENTATIVE_DISTANCE_MM_OFFSET = 58;
    public static final int FULLNESS_SAMPLE_RESULT_REQUESTED_SAMPLE_COUNT_OFFSET = 62;
    public static final int FULLNESS_SAMPLE_RESULT_VALID_SAMPLE_COUNT_OFFSET = 63;
    public static final int FULLNESS_SAMPLE_RESULT_MEASUREMENT_UID_OFFSET = 64;
    public static final int FULLNESS_SAMPLE_RESULT_MEASUREMENT_KIND_OFFSET = 80;
    public static final int FULLNESS_SAMPLE_RESULT_REPORTED_WEIGHT_GRAMS_OFFSET = 81;
    public static final int FULLNESS_SAMPLE_RESULT_MEASUREMENT_ELAPSED_MS_OFFSET = 85;
    public static final int FULLNESS_SAMPLE_RESULT_SAMPLE_COUNT_OFFSET = 87;
    public static final int FULLNESS_SAMPLE_RESULT_SAMPLE_SPAN_GRAMS_OFFSET = 88;
    public static final int FULLNESS_SAMPLE_RESULT_CALIBRATION_VERSION_OFFSET = 92;
    public static final int FULLNESS_SAMPLE_RESULT_FAULT_CODE_OFFSET = 96;
    public static final int FULLNESS_SAMPLE_RESULT_CONFIG_VERSION_OFFSET = 98;
    public static final int BASELINE_MEASUREMENT_RESULT_PAYLOAD_MIN_LENGTH = 95;
    public static final int BASELINE_MEASUREMENT_RESULT_PAYLOAD_MAX_LENGTH = 95;
    public static final int BASELINE_MEASUREMENT_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int BASELINE_MEASUREMENT_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int BASELINE_MEASUREMENT_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int BASELINE_MEASUREMENT_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int BASELINE_MEASUREMENT_RESULT_MEASUREMENT_UID_OFFSET = 36;
    public static final int BASELINE_MEASUREMENT_RESULT_PORT_NO_OFFSET = 52;
    public static final int BASELINE_MEASUREMENT_RESULT_WEIGHT_MEASUREMENT_UID_OFFSET = 53;
    public static final int BASELINE_MEASUREMENT_RESULT_MEASUREMENT_KIND_OFFSET = 69;
    public static final int BASELINE_MEASUREMENT_RESULT_REPORTED_WEIGHT_GRAMS_OFFSET = 70;
    public static final int BASELINE_MEASUREMENT_RESULT_MEASUREMENT_ELAPSED_MS_OFFSET = 74;
    public static final int BASELINE_MEASUREMENT_RESULT_SAMPLE_COUNT_OFFSET = 76;
    public static final int BASELINE_MEASUREMENT_RESULT_SAMPLE_SPAN_GRAMS_OFFSET = 77;
    public static final int BASELINE_MEASUREMENT_RESULT_CALIBRATION_VERSION_OFFSET = 81;
    public static final int BASELINE_MEASUREMENT_RESULT_FAULT_CODE_OFFSET = 85;
    public static final int BASELINE_MEASUREMENT_RESULT_CONFIG_VERSION_OFFSET = 87;
    public static final int FAULT_OBSERVED_PAYLOAD_MIN_LENGTH = 59;
    public static final int FAULT_OBSERVED_PAYLOAD_MAX_LENGTH = 59;
    public static final int FAULT_OBSERVED_MCU_BOOT_ID_OFFSET = 0;
    public static final int FAULT_OBSERVED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int FAULT_OBSERVED_UPTIME_MS_OFFSET = 12;
    public static final int FAULT_OBSERVED_FAULT_UID_OFFSET = 20;
    public static final int FAULT_OBSERVED_LIFECYCLE_OFFSET = 36;
    public static final int FAULT_OBSERVED_COMPONENT_OFFSET = 37;
    public static final int FAULT_OBSERVED_SEVERITY_OFFSET = 38;
    public static final int FAULT_OBSERVED_FAULT_CODE_OFFSET = 39;
    public static final int FAULT_OBSERVED_WORK_TYPE_OFFSET = 41;
    public static final int FAULT_OBSERVED_WORK_UID_OFFSET = 42;
    public static final int FAULT_OBSERVED_PORT_NO_OFFSET = 58;
    public static final int SAFETY_SENSOR_EVENT_PAYLOAD_MIN_LENGTH = 42;
    public static final int SAFETY_SENSOR_EVENT_PAYLOAD_MAX_LENGTH = 42;
    public static final int SAFETY_SENSOR_EVENT_MCU_BOOT_ID_OFFSET = 0;
    public static final int SAFETY_SENSOR_EVENT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int SAFETY_SENSOR_EVENT_UPTIME_MS_OFFSET = 12;
    public static final int SAFETY_SENSOR_EVENT_SMOKE_STATE_OFFSET = 20;
    public static final int SAFETY_SENSOR_EVENT_SMOKE_SENSOR_HEALTH_OFFSET = 21;
    public static final int SAFETY_SENSOR_EVENT_FAULT_CODE_OFFSET = 22;
    public static final int SAFETY_SENSOR_EVENT_WORK_TYPE_OFFSET = 24;
    public static final int SAFETY_SENSOR_EVENT_WORK_UID_OFFSET = 25;
    public static final int SAFETY_SENSOR_EVENT_PORT_NO_OFFSET = 41;
    public static final int SAFE_CLOSE_RESULT_PAYLOAD_MIN_LENGTH = 43;
    public static final int SAFE_CLOSE_RESULT_PAYLOAD_MAX_LENGTH = 43;
    public static final int SAFE_CLOSE_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int SAFE_CLOSE_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int SAFE_CLOSE_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int SAFE_CLOSE_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int SAFE_CLOSE_RESULT_SCOPE_OFFSET = 36;
    public static final int SAFE_CLOSE_RESULT_PORT_NO_OFFSET = 37;
    public static final int SAFE_CLOSE_RESULT_COMMAND_OFFSET = 38;
    public static final int SAFE_CLOSE_RESULT_OUTPUT_STATUS_OFFSET = 39;
    public static final int SAFE_CLOSE_RESULT_PHYSICAL_DOOR_STATE_BASIS_OFFSET = 40;
    public static final int SAFE_CLOSE_RESULT_FAULT_CODE_OFFSET = 41;
    public static final int CLEAN_COMPLETION_CONFIRMED_PAYLOAD_MIN_LENGTH = 83;
    public static final int CLEAN_COMPLETION_CONFIRMED_PAYLOAD_MAX_LENGTH = 83;
    public static final int CLEAN_COMPLETION_CONFIRMED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_COMPLETION_CONFIRMED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_COMPLETION_CONFIRMED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_COMPLETION_CONFIRMED_MCU_COMMAND_UID_OFFSET = 20;
    public static final int CLEAN_COMPLETION_CONFIRMED_OPERATION_UID_OFFSET = 36;
    public static final int CLEAN_COMPLETION_CONFIRMED_PORT_NO_OFFSET = 52;
    public static final int CLEAN_COMPLETION_CONFIRMED_CLEAN_ACTION_SEQUENCE_OFFSET = 53;
    public static final int CLEAN_COMPLETION_CONFIRMED_FINAL_MEASUREMENT_UID_OFFSET = 55;
    public static final int CLEAN_COMPLETION_CONFIRMED_LOCK_POWER_STATE_OFFSET = 71;
    public static final int CLEAN_COMPLETION_CONFIRMED_SOLENOID_HEALTH_OFFSET = 72;
    public static final int CLEAN_COMPLETION_CONFIRMED_CLEAN_DOOR_STATE_BASIS_OFFSET = 73;
    public static final int CLEAN_COMPLETION_CONFIRMED_CLEANER_PHYSICAL_CLOSE_CONFIRMED_OFFSET = 74;
    public static final int CLEAN_COMPLETION_CONFIRMED_CONFIG_VERSION_OFFSET = 75;
    public static final int BOOT_RECONCILIATION_RESULT_PAYLOAD_MIN_LENGTH = 64;
    public static final int BOOT_RECONCILIATION_RESULT_PAYLOAD_MAX_LENGTH = 64;
    public static final int BOOT_RECONCILIATION_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int BOOT_RECONCILIATION_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int BOOT_RECONCILIATION_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int BOOT_RECONCILIATION_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int BOOT_RECONCILIATION_RESULT_DECISION_OFFSET = 36;
    public static final int BOOT_RECONCILIATION_RESULT_STATUS_OFFSET = 37;
    public static final int BOOT_RECONCILIATION_RESULT_ACTIVE_WORK_TYPE_OFFSET = 38;
    public static final int BOOT_RECONCILIATION_RESULT_ACTIVE_WORK_UID_OFFSET = 39;
    public static final int BOOT_RECONCILIATION_RESULT_ACTIVE_PORT_NO_OFFSET = 55;
    public static final int BOOT_RECONCILIATION_RESULT_RECOVERY_GENERATION_OFFSET = 56;
    public static final int BOOT_RECONCILIATION_RESULT_NEXT_CLEAN_ACTION_SEQUENCE_OFFSET = 60;
    public static final int BOOT_RECONCILIATION_RESULT_FAULT_CODE_OFFSET = 62;
    public static final int STATE_SNAPSHOT_BEGIN_PAYLOAD_MIN_LENGTH = 229;
    public static final int STATE_SNAPSHOT_BEGIN_PAYLOAD_MAX_LENGTH = 229;
    public static final int STATE_SNAPSHOT_BEGIN_MCU_BOOT_ID_OFFSET = 0;
    public static final int STATE_SNAPSHOT_BEGIN_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int STATE_SNAPSHOT_BEGIN_UPTIME_MS_OFFSET = 12;
    public static final int STATE_SNAPSHOT_BEGIN_SNAPSHOT_UID_OFFSET = 20;
    public static final int STATE_SNAPSHOT_BEGIN_QUERY_COMMAND_UID_OFFSET = 36;
    public static final int STATE_SNAPSHOT_BEGIN_PROTOCOL_MAJOR_OFFSET = 52;
    public static final int STATE_SNAPSHOT_BEGIN_PROTOCOL_MINOR_OFFSET = 53;
    public static final int STATE_SNAPSHOT_BEGIN_FIRMWARE_VERSION_CODE_OFFSET = 54;
    public static final int STATE_SNAPSHOT_BEGIN_ACTIVE_WORK_TYPE_OFFSET = 58;
    public static final int STATE_SNAPSHOT_BEGIN_ACTIVE_WORK_UID_OFFSET = 59;
    public static final int STATE_SNAPSHOT_BEGIN_ACTIVE_PORT_NO_OFFSET = 75;
    public static final int STATE_SNAPSHOT_BEGIN_ACTIVE_WORK_PHASE_OFFSET = 76;
    public static final int STATE_SNAPSHOT_BEGIN_LATEST_MCU_COMMAND_UID_OFFSET = 77;
    public static final int STATE_SNAPSHOT_BEGIN_APPLIED_CONFIG_VERSION_OFFSET = 93;
    public static final int STATE_SNAPSHOT_BEGIN_APPLIED_CONTENT_SHA256_OFFSET = 101;
    public static final int STATE_SNAPSHOT_BEGIN_APPLIED_MCU_PAYLOAD_SHA256_OFFSET = 133;
    public static final int STATE_SNAPSHOT_BEGIN_STAGING_VALID_OFFSET = 165;
    public static final int STATE_SNAPSHOT_BEGIN_STAGING_APPLICATION_UID_OFFSET = 166;
    public static final int STATE_SNAPSHOT_BEGIN_STAGING_CONFIG_VERSION_OFFSET = 182;
    public static final int STATE_SNAPSHOT_BEGIN_STAGING_MCU_PAYLOAD_SHA256_OFFSET = 190;
    public static final int STATE_SNAPSHOT_BEGIN_STAGING_PART_COUNT_OFFSET = 222;
    public static final int STATE_SNAPSHOT_BEGIN_STAGING_RECEIVED_PART_BITMAP_OFFSET = 223;
    public static final int STATE_SNAPSHOT_BEGIN_PORT_COUNT_OFFSET = 225;
    public static final int STATE_SNAPSHOT_BEGIN_PART_INDEX_OFFSET = 226;
    public static final int STATE_SNAPSHOT_BEGIN_PART_COUNT_OFFSET = 227;
    public static final int STATE_SNAPSHOT_BEGIN_RESET_REASON_OFFSET = 228;
    public static final int STATE_SNAPSHOT_PORT_PAYLOAD_MIN_LENGTH = 81;
    public static final int STATE_SNAPSHOT_PORT_PAYLOAD_MAX_LENGTH = 81;
    public static final int STATE_SNAPSHOT_PORT_MCU_BOOT_ID_OFFSET = 0;
    public static final int STATE_SNAPSHOT_PORT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int STATE_SNAPSHOT_PORT_UPTIME_MS_OFFSET = 12;
    public static final int STATE_SNAPSHOT_PORT_SNAPSHOT_UID_OFFSET = 20;
    public static final int STATE_SNAPSHOT_PORT_PART_INDEX_OFFSET = 36;
    public static final int STATE_SNAPSHOT_PORT_PART_COUNT_OFFSET = 37;
    public static final int STATE_SNAPSHOT_PORT_PORT_NO_OFFSET = 38;
    public static final int STATE_SNAPSHOT_PORT_LAST_DELIVERY_DOOR_COMMAND_OFFSET = 39;
    public static final int STATE_SNAPSHOT_PORT_LAST_DELIVERY_DOOR_OUTPUT_STATUS_OFFSET = 40;
    public static final int STATE_SNAPSHOT_PORT_DELIVERY_DOOR_PHYSICAL_STATE_BASIS_OFFSET = 41;
    public static final int STATE_SNAPSHOT_PORT_CLEAN_LOCK_POWER_STATE_OFFSET = 42;
    public static final int STATE_SNAPSHOT_PORT_CLEAN_SOLENOID_HEALTH_OFFSET = 43;
    public static final int STATE_SNAPSHOT_PORT_CLEAN_DOOR_STATE_BASIS_OFFSET = 44;
    public static final int STATE_SNAPSHOT_PORT_CLEANER_PHYSICAL_CLOSE_CONFIRMED_OFFSET = 45;
    public static final int STATE_SNAPSHOT_PORT_MEASUREMENT_STATUS_OFFSET = 46;
    public static final int STATE_SNAPSHOT_PORT_WEIGHT_VALUE_PRESENT_OFFSET = 47;
    public static final int STATE_SNAPSHOT_PORT_REPORTED_WEIGHT_GRAMS_OFFSET = 48;
    public static final int STATE_SNAPSHOT_PORT_WEIGHT_VALUE_KIND_OFFSET = 52;
    public static final int STATE_SNAPSHOT_PORT_MEASUREMENT_ELAPSED_MS_OFFSET = 53;
    public static final int STATE_SNAPSHOT_PORT_SAMPLE_COUNT_OFFSET = 57;
    public static final int STATE_SNAPSHOT_PORT_CALIBRATION_VERSION_OFFSET = 59;
    public static final int STATE_SNAPSHOT_PORT_WEIGHT_SENSOR_HEALTH_OFFSET = 63;
    public static final int STATE_SNAPSHOT_PORT_FAULT_CODE_OFFSET = 64;
    public static final int STATE_SNAPSHOT_PORT_FULLNESS_SENSOR_KIND_OFFSET = 66;
    public static final int STATE_SNAPSHOT_PORT_FULLNESS_SENSOR_VALUE_OFFSET = 67;
    public static final int STATE_SNAPSHOT_PORT_FULLNESS_SAMPLE_BASIS_OFFSET = 68;
    public static final int STATE_SNAPSHOT_PORT_REPRESENTATIVE_DISTANCE_PRESENT_OFFSET = 69;
    public static final int STATE_SNAPSHOT_PORT_REPRESENTATIVE_DISTANCE_MM_OFFSET = 70;
    public static final int STATE_SNAPSHOT_PORT_FULLNESS_VALID_SAMPLE_COUNT_OFFSET = 74;
    public static final int STATE_SNAPSHOT_PORT_SMOKE_STATE_OFFSET = 75;
    public static final int STATE_SNAPSHOT_PORT_SMOKE_SENSOR_HEALTH_OFFSET = 76;
    public static final int STATE_SNAPSHOT_PORT_FAULT_BITMAP_OFFSET = 77;
    public static final int STATE_SNAPSHOT_END_PAYLOAD_MIN_LENGTH = 96;
    public static final int STATE_SNAPSHOT_END_PAYLOAD_MAX_LENGTH = 96;
    public static final int STATE_SNAPSHOT_END_MCU_BOOT_ID_OFFSET = 0;
    public static final int STATE_SNAPSHOT_END_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int STATE_SNAPSHOT_END_UPTIME_MS_OFFSET = 12;
    public static final int STATE_SNAPSHOT_END_SNAPSHOT_UID_OFFSET = 20;
    public static final int STATE_SNAPSHOT_END_PART_INDEX_OFFSET = 36;
    public static final int STATE_SNAPSHOT_END_PART_COUNT_OFFSET = 37;
    public static final int STATE_SNAPSHOT_END_PENDING_CRITICAL_EVENT_COUNT_OFFSET = 38;
    public static final int STATE_SNAPSHOT_END_OLDEST_PENDING_EVENT_BOOT_ID_OFFSET = 40;
    public static final int STATE_SNAPSHOT_END_OLDEST_PENDING_EVENT_SEQUENCE_OFFSET = 48;
    public static final int STATE_SNAPSHOT_END_LATEST_PENDING_EVENT_BOOT_ID_OFFSET = 52;
    public static final int STATE_SNAPSHOT_END_LATEST_PENDING_EVENT_SEQUENCE_OFFSET = 60;
    public static final int STATE_SNAPSHOT_END_SNAPSHOT_SHA256_OFFSET = 64;

    public enum SenderRole { EDGE, MCU }
    public enum Direction { BIDIRECTIONAL, EDGE_TO_MCU, MCU_TO_EDGE }

    private EcobinUartProtocol() {}

    private static boolean bytesZero(byte[] data, int offset, int length) {
        for (int i = offset; i < offset + length; i++) { if (data[i] != 0) return false; }
        return true;
    }
    private static boolean deviceEntryUrlChunkSafe(byte[] data, int offset, int length, boolean first) {
        byte[] prefix = new byte[] {'h', 't', 't', 'p', 's', ':', '/', '/'};
        if (length == 0 || (first && (length < prefix.length
            || !Arrays.equals(Arrays.copyOfRange(data, offset, offset + prefix.length), prefix)))) return false;
        for (int i = offset; i < offset + length; i++) {
            int value = Byte.toUnsignedInt(data[i]);
            if (value < 0x21 || value > 0x7e || value == 0x22 || value == 0x5c) return false;
        }
        return true;
    }
    // Session shape only; not a runtime freshness or admission decision.
    private static void validateSessionPayload(int messageType, byte[] payload) {
        switch (messageType) {
        case MESSAGE_BOOT_PROBE:
            if (payload.length < 8 || payload.length > 8) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            return;
        case MESSAGE_BOOT_PROBE_REPLY:
            if (payload.length < 16 || payload.length > 16) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            return;
        case MESSAGE_BIND_BOOT:
            if (payload.length < 16 || payload.length > 16) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            return;
        case MESSAGE_BIND_BOOT_REPLY:
            if (payload.length < 25 || payload.length > 25) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 16, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (ByteBuffer.wrap(payload, 16, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if (Byte.toUnsignedInt(payload[24]) != 1 && Byte.toUnsignedInt(payload[24]) != 2 && Byte.toUnsignedInt(payload[24]) != 3) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            if ((Byte.toUnsignedInt(payload[24]) == 1 && ByteBuffer.wrap(payload, 16, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong()) || (Byte.toUnsignedInt(payload[24]) == 2 && ByteBuffer.wrap(payload, 16, 8).order(ByteOrder.BIG_ENDIAN).getLong() != 0) || (Byte.toUnsignedInt(payload[24]) == 3 && ByteBuffer.wrap(payload, 16, 8).order(ByteOrder.BIG_ENDIAN).getLong() == 0)) { throw new IllegalArgumentException("invalid bootstrap payload"); }
            return;
        case MESSAGE_COMMAND_DECISION:
            if (payload.length < 71 || payload.length > 71) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[68]) != 1 && Byte.toUnsignedInt(payload[68]) != 2 && Byte.toUnsignedInt(payload[68]) != 3 && Byte.toUnsignedInt(payload[68]) != 4 && Byte.toUnsignedInt(payload[68]) != 5 && Byte.toUnsignedInt(payload[68]) != 6) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 6 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 7 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 8 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 9 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 10 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 11 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 12) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[68]) == 3) != (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[68]) == 2) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 69, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[68]) == 6) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_COMMAND:
            if (payload.length < 68 || payload.length > 68) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 8, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_COMMAND_QUERY_RESULT:
            if (payload.length < 83 || payload.length > 83) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 8, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[76]) != 1 && Byte.toUnsignedInt(payload[76]) != 2 && Byte.toUnsignedInt(payload[76]) != 3 && Byte.toUnsignedInt(payload[76]) != 4 && Byte.toUnsignedInt(payload[76]) != 5 && Byte.toUnsignedInt(payload[76]) != 6) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 6 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 7 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 8 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 9 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 10 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 11 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 12) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[76]) == 3) != (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[76]) == 2) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 77, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() == 0 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 79, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() == ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() && ((Byte.toUnsignedInt(payload[76]) == 6 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) <= Integer.toUnsignedLong(ByteBuffer.wrap(payload, 79, 4).order(ByteOrder.BIG_ENDIAN).getInt())) || (Byte.toUnsignedInt(payload[76]) == 5 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > Integer.toUnsignedLong(ByteBuffer.wrap(payload, 79, 4).order(ByteOrder.BIG_ENDIAN).getInt())) || ((Byte.toUnsignedInt(payload[76]) == 1 || Byte.toUnsignedInt(payload[76]) == 2 || Byte.toUnsignedInt(payload[76]) == 4) && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != Integer.toUnsignedLong(ByteBuffer.wrap(payload, 79, 4).order(ByteOrder.BIG_ENDIAN).getInt())))) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_RESULT_SAVED:
            if (payload.length < 60 || payload.length > 60) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 12, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_RESULT_SAVED_REPLY:
            if (payload.length < 69 || payload.length > 69) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 12, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[68]) != 1 && Byte.toUnsignedInt(payload[68]) != 2 && Byte.toUnsignedInt(payload[68]) != 3 && Byte.toUnsignedInt(payload[68]) != 4 && Byte.toUnsignedInt(payload[68]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[68]) == 5) != (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DEVICE_ENTRY_URL_APPLY_RESULT:
            if (payload.length < 89 || payload.length > 89) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 52, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 52, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 192L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) != 1 && Byte.toUnsignedInt(payload[86]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 6 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 7 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 8 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 9 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 10 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 11 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 12) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[86]) == 1 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0) || (Byte.toUnsignedInt(payload[86]) == 2 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 7)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 54, 32)) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_WORK_RESULT:
            if (payload.length < 199 || payload.length > 199) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 12, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[60]) != 0 && Byte.toUnsignedInt(payload[60]) != 1 && Byte.toUnsignedInt(payload[60]) != 2 && Byte.toUnsignedInt(payload[60]) != 3 && Byte.toUnsignedInt(payload[60]) != 4 && Byte.toUnsignedInt(payload[60]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[61]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[61]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 62, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 62, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 70, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 86, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 90, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 90, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[104]) != 1 && Byte.toUnsignedInt(payload[104]) != 2 && Byte.toUnsignedInt(payload[104]) != 3 && Byte.toUnsignedInt(payload[104]) != 4 && Byte.toUnsignedInt(payload[104]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[105]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[106]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) != 0 && Byte.toUnsignedInt(payload[107]) != 1 && Byte.toUnsignedInt(payload[107]) != 2 && Byte.toUnsignedInt(payload[107]) != 3 && Byte.toUnsignedInt(payload[107]) != 4 && Byte.toUnsignedInt(payload[107]) != 5 && Byte.toUnsignedInt(payload[107]) != 6 && Byte.toUnsignedInt(payload[107]) != 7 && Byte.toUnsignedInt(payload[107]) != 8 && Byte.toUnsignedInt(payload[107]) != 9 && Byte.toUnsignedInt(payload[107]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 124, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 124, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 140, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[142]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[153]) != 0 && Byte.toUnsignedInt(payload[153]) != 1 && Byte.toUnsignedInt(payload[153]) != 2 && Byte.toUnsignedInt(payload[153]) != 3 && Byte.toUnsignedInt(payload[153]) != 4 && Byte.toUnsignedInt(payload[153]) != 5 && Byte.toUnsignedInt(payload[153]) != 6 && Byte.toUnsignedInt(payload[153]) != 7 && Byte.toUnsignedInt(payload[153]) != 8 && Byte.toUnsignedInt(payload[153]) != 9 && Byte.toUnsignedInt(payload[153]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 170, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 170, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 186, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[188]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[60]) != 2 && Byte.toUnsignedInt(payload[60]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[60]) == 2 && (Byte.toUnsignedInt(payload[104]) == 3 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 100, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0 || Byte.toUnsignedInt(payload[105]) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[60]) == 3 && (Byte.toUnsignedInt(payload[104]) <= 2 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 98, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 || Byte.toUnsignedInt(payload[106]) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[104]) <= 2 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 98, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[104]) == 3 && (Byte.toUnsignedInt(payload[105]) == 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 100, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[107]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) == 10 && Byte.toUnsignedInt(payload[104]) != 4 && Byte.toUnsignedInt(payload[104]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) <= 1 && !bytesZero(payload, 108, 45)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) > 1 && (bytesZero(payload, 108, 16) || ByteBuffer.wrap(payload, 124, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 132, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[107]) == 2 || Byte.toUnsignedInt(payload[107]) == 3) && (Byte.toUnsignedInt(payload[142]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 151, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 143, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 140, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) > 3 && ByteBuffer.wrap(payload, 136, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[153]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[153]) == 10 && Byte.toUnsignedInt(payload[104]) != 4 && Byte.toUnsignedInt(payload[104]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[153]) <= 1 && !bytesZero(payload, 154, 45)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[153]) > 1 && (bytesZero(payload, 154, 16) || ByteBuffer.wrap(payload, 170, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 178, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[153]) == 2 || Byte.toUnsignedInt(payload[153]) == 3) && (Byte.toUnsignedInt(payload[188]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 197, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[153]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 189, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[153]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 186, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[153]) > 3 && ByteBuffer.wrap(payload, 182, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[107]) > 1 && Byte.toUnsignedInt(payload[153]) > 1 && ((Arrays.equals(payload, 108, 124, payload, 154, 170)) || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 132, 4).order(ByteOrder.BIG_ENDIAN).getInt()) >= Integer.toUnsignedLong(ByteBuffer.wrap(payload, 178, 4).order(ByteOrder.BIG_ENDIAN).getInt()))) { throw new IllegalArgumentException("invalid session payload"); }
            if (!resultDigestMatches(payload)) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_RESULT:
            if (payload.length < 68 || payload.length > 68) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 16, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_RESULT_QUERY_REPLY:
            if (payload.length < 77 || payload.length > 77) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 16, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[76]) != 1 && Byte.toUnsignedInt(payload[76]) != 2 && Byte.toUnsignedInt(payload[76]) != 3 && Byte.toUnsignedInt(payload[76]) != 4 && Byte.toUnsignedInt(payload[76]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[76]) == 5) != (ByteBuffer.wrap(payload, 68, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_WORK:
            if (payload.length < 86 || payload.length > 86) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 8, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 68, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[84]) != 0 && Byte.toUnsignedInt(payload[84]) != 1 && Byte.toUnsignedInt(payload[84]) != 2 && Byte.toUnsignedInt(payload[84]) != 3 && Byte.toUnsignedInt(payload[84]) != 4 && Byte.toUnsignedInt(payload[84]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[84]) != 2 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_WORK_QUERY_REPLY:
            if (payload.length < 132 || payload.length > 132) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 8, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 68, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[84]) != 0 && Byte.toUnsignedInt(payload[84]) != 1 && Byte.toUnsignedInt(payload[84]) != 2 && Byte.toUnsignedInt(payload[84]) != 3 && Byte.toUnsignedInt(payload[84]) != 4 && Byte.toUnsignedInt(payload[84]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 86, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 86, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[94]) != 1 && Byte.toUnsignedInt(payload[94]) != 2 && Byte.toUnsignedInt(payload[94]) != 3 && Byte.toUnsignedInt(payload[94]) != 4 && Byte.toUnsignedInt(payload[94]) != 5 && Byte.toUnsignedInt(payload[94]) != 6) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[95]) != 0 && Byte.toUnsignedInt(payload[95]) != 1 && Byte.toUnsignedInt(payload[95]) != 2 && Byte.toUnsignedInt(payload[95]) != 16 && Byte.toUnsignedInt(payload[95]) != 17 && Byte.toUnsignedInt(payload[95]) != 18 && Byte.toUnsignedInt(payload[95]) != 19 && Byte.toUnsignedInt(payload[95]) != 20 && Byte.toUnsignedInt(payload[95]) != 21 && Byte.toUnsignedInt(payload[95]) != 22 && Byte.toUnsignedInt(payload[95]) != 23 && Byte.toUnsignedInt(payload[95]) != 24 && Byte.toUnsignedInt(payload[95]) != 25 && Byte.toUnsignedInt(payload[95]) != 26 && Byte.toUnsignedInt(payload[95]) != 32 && Byte.toUnsignedInt(payload[95]) != 33 && Byte.toUnsignedInt(payload[95]) != 34 && Byte.toUnsignedInt(payload[95]) != 35 && Byte.toUnsignedInt(payload[95]) != 36 && Byte.toUnsignedInt(payload[95]) != 37 && Byte.toUnsignedInt(payload[95]) != 38 && Byte.toUnsignedInt(payload[95]) != 39 && Byte.toUnsignedInt(payload[95]) != 40 && Byte.toUnsignedInt(payload[95]) != 48 && Byte.toUnsignedInt(payload[95]) != 49 && Byte.toUnsignedInt(payload[95]) != 64) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[84]) != 2 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[94]) == 6) != (ByteBuffer.wrap(payload, 86, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[94]) == 2 || Byte.toUnsignedInt(payload[94]) == 3) && (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 96, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0 || (Byte.toUnsignedInt(payload[84]) == 2 && Byte.toUnsignedInt(payload[95]) != 26) || (Byte.toUnsignedInt(payload[84]) == 3 && Byte.toUnsignedInt(payload[95]) != 39))) { throw new IllegalArgumentException("invalid session payload"); }
            if (!(Byte.toUnsignedInt(payload[94]) == 2 || Byte.toUnsignedInt(payload[94]) == 3) && (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 96, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0 || !bytesZero(payload, 100, 32))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[94]) == 1 && Byte.toUnsignedInt(payload[84]) == 2 && (Byte.toUnsignedInt(payload[95]) != 16 && Byte.toUnsignedInt(payload[95]) != 17 && Byte.toUnsignedInt(payload[95]) != 18 && Byte.toUnsignedInt(payload[95]) != 19 && Byte.toUnsignedInt(payload[95]) != 20 && Byte.toUnsignedInt(payload[95]) != 21 && Byte.toUnsignedInt(payload[95]) != 22 && Byte.toUnsignedInt(payload[95]) != 23 && Byte.toUnsignedInt(payload[95]) != 24 && Byte.toUnsignedInt(payload[95]) != 25 && Byte.toUnsignedInt(payload[95]) != 26 && Byte.toUnsignedInt(payload[95]) != 64)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[94]) == 1 && Byte.toUnsignedInt(payload[84]) == 3 && (Byte.toUnsignedInt(payload[95]) != 32 && Byte.toUnsignedInt(payload[95]) != 33 && Byte.toUnsignedInt(payload[95]) != 34 && Byte.toUnsignedInt(payload[95]) != 35 && Byte.toUnsignedInt(payload[95]) != 36 && Byte.toUnsignedInt(payload[95]) != 37 && Byte.toUnsignedInt(payload[95]) != 38 && Byte.toUnsignedInt(payload[95]) != 39 && Byte.toUnsignedInt(payload[95]) != 40 && Byte.toUnsignedInt(payload[95]) != 64)) { throw new IllegalArgumentException("invalid session payload"); }
            if (!(Byte.toUnsignedInt(payload[94]) == 2 || Byte.toUnsignedInt(payload[94]) == 3) && Byte.toUnsignedInt(payload[94]) != 1 && Byte.toUnsignedInt(payload[95]) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_DEVICE_FACTS:
            if (payload.length < 17 || payload.length > 17) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[16]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[16]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DEVICE_FACTS_REPLY:
            if (payload.length < 226 || payload.length > 226) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[16]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[16]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 17, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 17, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[25]) != 1 && Byte.toUnsignedInt(payload[25]) != 2 && Byte.toUnsignedInt(payload[25]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 26, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 26, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 34, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 34, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[106]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 107, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 107, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[115]) != 0 && Byte.toUnsignedInt(payload[115]) != 1 && Byte.toUnsignedInt(payload[115]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[116]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[117]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[118]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[119]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[120]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[121]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[122]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[123]) != 0 && Byte.toUnsignedInt(payload[123]) != 1 && Byte.toUnsignedInt(payload[123]) != 2 && Byte.toUnsignedInt(payload[123]) != 3 && Byte.toUnsignedInt(payload[123]) != 4 && Byte.toUnsignedInt(payload[123]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 128, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 128, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[148]) != 0 && Byte.toUnsignedInt(payload[148]) != 1 && Byte.toUnsignedInt(payload[148]) != 2 && Byte.toUnsignedInt(payload[148]) != 3 && Byte.toUnsignedInt(payload[148]) != 4 && Byte.toUnsignedInt(payload[148]) != 5 && Byte.toUnsignedInt(payload[148]) != 6 && Byte.toUnsignedInt(payload[148]) != 7) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 149, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 149, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 157, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 157, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 165, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[167]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[176]) != 0 && Byte.toUnsignedInt(payload[176]) != 1 && Byte.toUnsignedInt(payload[176]) != 2 && Byte.toUnsignedInt(payload[176]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 177, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 177, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[185]) != 0 && Byte.toUnsignedInt(payload[185]) != 1 && Byte.toUnsignedInt(payload[185]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[186]) != 0 && Byte.toUnsignedInt(payload[186]) != 1 && Byte.toUnsignedInt(payload[186]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 187, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 187, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[195]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) != 0 && Byte.toUnsignedInt(payload[198]) != 1 && Byte.toUnsignedInt(payload[198]) != 2 && Byte.toUnsignedInt(payload[198]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[215]) != 0 && Byte.toUnsignedInt(payload[215]) != 1 && Byte.toUnsignedInt(payload[215]) != 2 && Byte.toUnsignedInt(payload[215]) != 3 && Byte.toUnsignedInt(payload[215]) != 4 && Byte.toUnsignedInt(payload[215]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[216]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[217]) != 0 && Byte.toUnsignedInt(payload[217]) != 1 && Byte.toUnsignedInt(payload[217]) != 2 && Byte.toUnsignedInt(payload[217]) != 16 && Byte.toUnsignedInt(payload[217]) != 17 && Byte.toUnsignedInt(payload[217]) != 18 && Byte.toUnsignedInt(payload[217]) != 19 && Byte.toUnsignedInt(payload[217]) != 20 && Byte.toUnsignedInt(payload[217]) != 21 && Byte.toUnsignedInt(payload[217]) != 22 && Byte.toUnsignedInt(payload[217]) != 23 && Byte.toUnsignedInt(payload[217]) != 24 && Byte.toUnsignedInt(payload[217]) != 25 && Byte.toUnsignedInt(payload[217]) != 26 && Byte.toUnsignedInt(payload[217]) != 32 && Byte.toUnsignedInt(payload[217]) != 33 && Byte.toUnsignedInt(payload[217]) != 34 && Byte.toUnsignedInt(payload[217]) != 35 && Byte.toUnsignedInt(payload[217]) != 36 && Byte.toUnsignedInt(payload[217]) != 37 && Byte.toUnsignedInt(payload[217]) != 38 && Byte.toUnsignedInt(payload[217]) != 39 && Byte.toUnsignedInt(payload[217]) != 40 && Byte.toUnsignedInt(payload[217]) != 48 && Byte.toUnsignedInt(payload[217]) != 49 && Byte.toUnsignedInt(payload[217]) != 64) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[25]) == 2) != (ByteBuffer.wrap(payload, 17, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[25]) != 1 && !bytesZero(payload, 26, 200)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 107, 8).order(ByteOrder.BIG_ENDIAN).getLong() > ByteBuffer.wrap(payload, 26, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if ((ByteBuffer.wrap(payload, 34, 8).order(ByteOrder.BIG_ENDIAN).getLong() == 0 && (!bytesZero(payload, 42, 32) || !bytesZero(payload, 74, 32))) || (ByteBuffer.wrap(payload, 34, 8).order(ByteOrder.BIG_ENDIAN).getLong() != 0 && (bytesZero(payload, 42, 32) || bytesZero(payload, 74, 32)))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[116]) != 0 && Byte.toUnsignedInt(payload[115]) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[120]) != 0) != (Byte.toUnsignedInt(payload[116]) != 0 && Byte.toUnsignedInt(payload[115]) == 2 && Byte.toUnsignedInt(payload[119]) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[117]) != 0) != (Byte.toUnsignedInt(payload[116]) != 0 && Byte.toUnsignedInt(payload[115]) == 1)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[118]) != 0) != (Byte.toUnsignedInt(payload[116]) != 0 && Byte.toUnsignedInt(payload[115]) == 2 && Byte.toUnsignedInt(payload[119]) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[122]) != 0 && (Byte.toUnsignedInt(payload[116]) != 0 || Byte.toUnsignedInt(payload[121]) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[123]) == 0 && !bytesZero(payload, 124, 20)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[123]) != 0 && (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 124, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0 || ByteBuffer.wrap(payload, 128, 8).order(ByteOrder.BIG_ENDIAN).getLong() > ByteBuffer.wrap(payload, 26, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[123]) != 1 && ByteBuffer.wrap(payload, 136, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[148]) == 0 && !bytesZero(payload, 144, 32)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[148]) != 0 && (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 144, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0 || ByteBuffer.wrap(payload, 149, 8).order(ByteOrder.BIG_ENDIAN).getLong() == 0 || ByteBuffer.wrap(payload, 157, 8).order(ByteOrder.BIG_ENDIAN).getLong() > ByteBuffer.wrap(payload, 26, 8).order(ByteOrder.BIG_ENDIAN).getLong() || Short.toUnsignedInt(ByteBuffer.wrap(payload, 165, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > ByteBuffer.wrap(payload, 157, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[148]) == 2 || Byte.toUnsignedInt(payload[148]) == 3) && Byte.toUnsignedInt(payload[167]) < 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[148]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 172, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[148]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 165, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[148]) != 2 && Byte.toUnsignedInt(payload[148]) != 3 && (ByteBuffer.wrap(payload, 168, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 172, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[148]) == 1 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 165, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 177, 8).order(ByteOrder.BIG_ENDIAN).getLong() > ByteBuffer.wrap(payload, 26, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[176]) == 0 && ByteBuffer.wrap(payload, 177, 8).order(ByteOrder.BIG_ENDIAN).getLong() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 187, 8).order(ByteOrder.BIG_ENDIAN).getLong() > ByteBuffer.wrap(payload, 26, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[186]) == 0 && (Byte.toUnsignedInt(payload[185]) != 0 || ByteBuffer.wrap(payload, 187, 8).order(ByteOrder.BIG_ENDIAN).getLong() != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[186]) != 0 && Byte.toUnsignedInt(payload[185]) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[186]) != 1 || Byte.toUnsignedInt(payload[185]) != 2) && Byte.toUnsignedInt(payload[195]) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[186]) != 1 || Byte.toUnsignedInt(payload[185]) != 1) && Short.toUnsignedInt(ByteBuffer.wrap(payload, 196, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) == 0 && !bytesZero(payload, 198, 28)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) != 0 && (bytesZero(payload, 199, 16) || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 218, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0 || Byte.toUnsignedInt(payload[216]) == 0 || (Byte.toUnsignedInt(payload[215]) != 2 && Byte.toUnsignedInt(payload[215]) != 3))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) == 1 && Byte.toUnsignedInt(payload[215]) == 2 && (Byte.toUnsignedInt(payload[217]) != 16 && Byte.toUnsignedInt(payload[217]) != 17 && Byte.toUnsignedInt(payload[217]) != 18 && Byte.toUnsignedInt(payload[217]) != 19 && Byte.toUnsignedInt(payload[217]) != 20 && Byte.toUnsignedInt(payload[217]) != 21 && Byte.toUnsignedInt(payload[217]) != 22 && Byte.toUnsignedInt(payload[217]) != 23 && Byte.toUnsignedInt(payload[217]) != 24 && Byte.toUnsignedInt(payload[217]) != 25 && Byte.toUnsignedInt(payload[217]) != 26 && Byte.toUnsignedInt(payload[217]) != 64)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) > 1 && Byte.toUnsignedInt(payload[215]) == 2 && Byte.toUnsignedInt(payload[217]) != 26) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) == 1 && Byte.toUnsignedInt(payload[215]) == 3 && (Byte.toUnsignedInt(payload[217]) != 32 && Byte.toUnsignedInt(payload[217]) != 33 && Byte.toUnsignedInt(payload[217]) != 34 && Byte.toUnsignedInt(payload[217]) != 35 && Byte.toUnsignedInt(payload[217]) != 36 && Byte.toUnsignedInt(payload[217]) != 37 && Byte.toUnsignedInt(payload[217]) != 38 && Byte.toUnsignedInt(payload[217]) != 39 && Byte.toUnsignedInt(payload[217]) != 40 && Byte.toUnsignedInt(payload[217]) != 64)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) > 1 && Byte.toUnsignedInt(payload[215]) == 3 && Byte.toUnsignedInt(payload[217]) != 39) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[198]) == 1 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 222, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0) || (Byte.toUnsignedInt(payload[198]) > 1 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 222, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_DEVICE_IDENTITY:
            if (payload.length < 16 || payload.length > 16) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DEVICE_IDENTITY_REPLY:
            if (payload.length < 53 || payload.length > 85) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 16, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 16, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[24]) != 1 && Byte.toUnsignedInt(payload[24]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[25]) != 2L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[26]) != 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[27]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 28, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) == 0 || Byte.toUnsignedInt(payload[52]) > 32 || payload.length != 53 + Byte.toUnsignedInt(payload[52])) { throw new IllegalArgumentException("invalid session payload"); }
            if ((ByteBuffer.wrap(payload, 28, 8).order(ByteOrder.BIG_ENDIAN).getLong() & ~65535L) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if ((ByteBuffer.wrap(payload, 28, 8).order(ByteOrder.BIG_ENDIAN).getLong() & 33024L) != 33024L) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_PROCESS_EVENT:
            if (payload.length < 97 || payload.length > 97) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 8, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 68, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[84]) != 0 && Byte.toUnsignedInt(payload[84]) != 1 && Byte.toUnsignedInt(payload[84]) != 2 && Byte.toUnsignedInt(payload[84]) != 3 && Byte.toUnsignedInt(payload[84]) != 4 && Byte.toUnsignedInt(payload[84]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) != 48 && Byte.toUnsignedInt(payload[86]) != 50 && Byte.toUnsignedInt(payload[86]) != 52 && Byte.toUnsignedInt(payload[86]) != 56 && Byte.toUnsignedInt(payload[86]) != 57 && Byte.toUnsignedInt(payload[86]) != 58 && Byte.toUnsignedInt(payload[86]) != 51 && Byte.toUnsignedInt(payload[86]) != 54 && Byte.toUnsignedInt(payload[86]) != 55 && Byte.toUnsignedInt(payload[86]) != 62) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 48 && Byte.toUnsignedInt(payload[84]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 50 && Byte.toUnsignedInt(payload[84]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 51 && Byte.toUnsignedInt(payload[84]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 52 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 54 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 55 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 56 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 57 && Byte.toUnsignedInt(payload[84]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 58 && Byte.toUnsignedInt(payload[84]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 62 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 0) != (Byte.toUnsignedInt(payload[86]) == 48 || Byte.toUnsignedInt(payload[86]) == 50 || Byte.toUnsignedInt(payload[86]) == 51 || Byte.toUnsignedInt(payload[86]) == 54 || Byte.toUnsignedInt(payload[86]) == 55 || Byte.toUnsignedInt(payload[86]) == 56 || Byte.toUnsignedInt(payload[86]) == 62)) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_PROCESS_EVENT_QUERY_REPLY:
            if (payload.length < 142 || payload.length > 142) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 8, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 64, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 68, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[84]) != 0 && Byte.toUnsignedInt(payload[84]) != 1 && Byte.toUnsignedInt(payload[84]) != 2 && Byte.toUnsignedInt(payload[84]) != 3 && Byte.toUnsignedInt(payload[84]) != 4 && Byte.toUnsignedInt(payload[84]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[85]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) != 48 && Byte.toUnsignedInt(payload[86]) != 50 && Byte.toUnsignedInt(payload[86]) != 52 && Byte.toUnsignedInt(payload[86]) != 56 && Byte.toUnsignedInt(payload[86]) != 57 && Byte.toUnsignedInt(payload[86]) != 58 && Byte.toUnsignedInt(payload[86]) != 51 && Byte.toUnsignedInt(payload[86]) != 54 && Byte.toUnsignedInt(payload[86]) != 55 && Byte.toUnsignedInt(payload[86]) != 62) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 97, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 97, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[105]) != 1 && Byte.toUnsignedInt(payload[105]) != 2 && Byte.toUnsignedInt(payload[105]) != 3 && Byte.toUnsignedInt(payload[105]) != 4 && Byte.toUnsignedInt(payload[105]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 48 && Byte.toUnsignedInt(payload[84]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 50 && Byte.toUnsignedInt(payload[84]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 51 && Byte.toUnsignedInt(payload[84]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 52 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 54 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 55 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 56 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 57 && Byte.toUnsignedInt(payload[84]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 58 && Byte.toUnsignedInt(payload[84]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[86]) == 62 && Byte.toUnsignedInt(payload[84]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 0) != (Byte.toUnsignedInt(payload[86]) == 48 || Byte.toUnsignedInt(payload[86]) == 50 || Byte.toUnsignedInt(payload[86]) == 51 || Byte.toUnsignedInt(payload[86]) == 54 || Byte.toUnsignedInt(payload[86]) == 55 || Byte.toUnsignedInt(payload[86]) == 56 || Byte.toUnsignedInt(payload[86]) == 62)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[105]) == 5) != (ByteBuffer.wrap(payload, 97, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 56, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[105]) == 1 || Byte.toUnsignedInt(payload[105]) == 2) && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 106, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[105]) > 2 && (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 106, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0 || !bytesZero(payload, 110, 32))) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_PROCESS_EVENT_SAVED:
            if (payload.length < 45 || payload.length > 45) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[12]) != 48 && Byte.toUnsignedInt(payload[12]) != 50 && Byte.toUnsignedInt(payload[12]) != 52 && Byte.toUnsignedInt(payload[12]) != 56 && Byte.toUnsignedInt(payload[12]) != 57 && Byte.toUnsignedInt(payload[12]) != 58 && Byte.toUnsignedInt(payload[12]) != 51 && Byte.toUnsignedInt(payload[12]) != 54 && Byte.toUnsignedInt(payload[12]) != 55 && Byte.toUnsignedInt(payload[12]) != 62) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_PROCESS_EVENT_SAVED_REPLY:
            if (payload.length < 54 || payload.length > 54) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[12]) != 48 && Byte.toUnsignedInt(payload[12]) != 50 && Byte.toUnsignedInt(payload[12]) != 52 && Byte.toUnsignedInt(payload[12]) != 56 && Byte.toUnsignedInt(payload[12]) != 57 && Byte.toUnsignedInt(payload[12]) != 58 && Byte.toUnsignedInt(payload[12]) != 51 && Byte.toUnsignedInt(payload[12]) != 54 && Byte.toUnsignedInt(payload[12]) != 55 && Byte.toUnsignedInt(payload[12]) != 62) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 45, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 45, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[53]) != 1 && Byte.toUnsignedInt(payload[53]) != 2 && Byte.toUnsignedInt(payload[53]) != 3 && Byte.toUnsignedInt(payload[53]) != 4 && Byte.toUnsignedInt(payload[53]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[53]) == 5) != (ByteBuffer.wrap(payload, 45, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_ACTUATOR_EVENT:
            if (payload.length < 20 || payload.length > 20) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_ACTUATOR_EVENT_QUERY_REPLY:
            if (payload.length < 66 || payload.length > 66) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 20, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 20, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[28]) != 1 && Byte.toUnsignedInt(payload[28]) != 3 && Byte.toUnsignedInt(payload[28]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[33]) != 0 && Byte.toUnsignedInt(payload[33]) != 49 && Byte.toUnsignedInt(payload[33]) != 53 && Byte.toUnsignedInt(payload[33]) != 61 && Byte.toUnsignedInt(payload[33]) != 98 && Byte.toUnsignedInt(payload[33]) != 99 && Byte.toUnsignedInt(payload[33]) != 100 && Byte.toUnsignedInt(payload[33]) != 101) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[28]) == 5) != (ByteBuffer.wrap(payload, 20, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 8, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[28]) == 1 && (Byte.toUnsignedInt(payload[33]) == 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 29, 4).order(ByteOrder.BIG_ENDIAN).getInt()) <= Integer.toUnsignedLong(ByteBuffer.wrap(payload, 16, 4).order(ByteOrder.BIG_ENDIAN).getInt()))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[28]) != 1 && (Byte.toUnsignedInt(payload[33]) != 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 29, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0 || !bytesZero(payload, 34, 32))) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_ACTUATOR_EVENT_SAVED:
            if (payload.length < 45 || payload.length > 45) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[12]) != 49 && Byte.toUnsignedInt(payload[12]) != 53 && Byte.toUnsignedInt(payload[12]) != 61 && Byte.toUnsignedInt(payload[12]) != 98 && Byte.toUnsignedInt(payload[12]) != 99 && Byte.toUnsignedInt(payload[12]) != 100 && Byte.toUnsignedInt(payload[12]) != 101) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_ACTUATOR_EVENT_SAVED_REPLY:
            if (payload.length < 54 || payload.length > 54) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[12]) != 49 && Byte.toUnsignedInt(payload[12]) != 53 && Byte.toUnsignedInt(payload[12]) != 61 && Byte.toUnsignedInt(payload[12]) != 98 && Byte.toUnsignedInt(payload[12]) != 99 && Byte.toUnsignedInt(payload[12]) != 100 && Byte.toUnsignedInt(payload[12]) != 101) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 45, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 45, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[53]) != 1 && Byte.toUnsignedInt(payload[53]) != 2 && Byte.toUnsignedInt(payload[53]) != 3 && Byte.toUnsignedInt(payload[53]) != 4 && Byte.toUnsignedInt(payload[53]) != 5) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[53]) == 5) != (ByteBuffer.wrap(payload, 45, 8).order(ByteOrder.BIG_ENDIAN).getLong() != ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong())) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_WORK_PREOPEN_WEIGHT_READY:
            if (payload.length < 97 || payload.length > 97) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 55, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) != 0 && Byte.toUnsignedInt(payload[71]) != 1 && Byte.toUnsignedInt(payload[71]) != 2 && Byte.toUnsignedInt(payload[71]) != 3 && Byte.toUnsignedInt(payload[71]) != 4 && Byte.toUnsignedInt(payload[71]) != 5 && Byte.toUnsignedInt(payload[71]) != 6 && Byte.toUnsignedInt(payload[71]) != 7 && Byte.toUnsignedInt(payload[71]) != 8 && Byte.toUnsignedInt(payload[71]) != 9 && Byte.toUnsignedInt(payload[71]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[78]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[71]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) < 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[71]) == 2 || Byte.toUnsignedInt(payload[71]) == 3) && (Byte.toUnsignedInt(payload[78]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 79, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) > 3 && ByteBuffer.wrap(payload, 72, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_WORK_POSTCLOSE_WEIGHT_READY:
            if (payload.length < 207 || payload.length > 207) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 55, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) != 0 && Byte.toUnsignedInt(payload[71]) != 1 && Byte.toUnsignedInt(payload[71]) != 2 && Byte.toUnsignedInt(payload[71]) != 3 && Byte.toUnsignedInt(payload[71]) != 4 && Byte.toUnsignedInt(payload[71]) != 5 && Byte.toUnsignedInt(payload[71]) != 6 && Byte.toUnsignedInt(payload[71]) != 7 && Byte.toUnsignedInt(payload[71]) != 8 && Byte.toUnsignedInt(payload[71]) != 9 && Byte.toUnsignedInt(payload[71]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[78]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 89, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) != 0 && Byte.toUnsignedInt(payload[97]) != 1 && Byte.toUnsignedInt(payload[97]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 174, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 174, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 182, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 182, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[190]) != 0 && Byte.toUnsignedInt(payload[190]) != 1 && Byte.toUnsignedInt(payload[190]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[191]) != 0 && Byte.toUnsignedInt(payload[191]) != 1 && Byte.toUnsignedInt(payload[191]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[192]) != 0 && Byte.toUnsignedInt(payload[192]) != 1 && Byte.toUnsignedInt(payload[192]) != 2 && Byte.toUnsignedInt(payload[192]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[193]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[198]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[199]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[200]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[201]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 202, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 4000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[206]) != 0 && Byte.toUnsignedInt(payload[206]) != 1 && Byte.toUnsignedInt(payload[206]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) == 0 && !bytesZero(payload, 97, 110)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) != 0 && (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 98, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0 || Byte.toUnsignedInt(payload[190]) != 1 || bytesZero(payload, 102, 32) || bytesZero(payload, 134, 32))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) != 0 && (Byte.toUnsignedInt(payload[198]) < 3 || Byte.toUnsignedInt(payload[201]) == 0 || Byte.toUnsignedInt(payload[201]) > Byte.toUnsignedInt(payload[198]) || Byte.toUnsignedInt(payload[199]) > Byte.toUnsignedInt(payload[198]) || Byte.toUnsignedInt(payload[200]) > Byte.toUnsignedInt(payload[199]) || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 202, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) != 0 && (ByteBuffer.wrap(payload, 174, 8).order(ByteOrder.BIG_ENDIAN).getLong() < ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() || (Byte.toUnsignedInt(payload[199]) == 0 && ByteBuffer.wrap(payload, 182, 8).order(ByteOrder.BIG_ENDIAN).getLong() != 0) || (Byte.toUnsignedInt(payload[199]) > 0 && (ByteBuffer.wrap(payload, 182, 8).order(ByteOrder.BIG_ENDIAN).getLong() < ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() || ByteBuffer.wrap(payload, 182, 8).order(ByteOrder.BIG_ENDIAN).getLong() > ByteBuffer.wrap(payload, 174, 8).order(ByteOrder.BIG_ENDIAN).getLong())))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) == 2 && (Byte.toUnsignedInt(payload[206]) == 0 || Byte.toUnsignedInt(payload[191]) != 0 || Byte.toUnsignedInt(payload[192]) != 0 || Byte.toUnsignedInt(payload[193]) != 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 194, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) == 1 && (Byte.toUnsignedInt(payload[206]) != 0 || Byte.toUnsignedInt(payload[199]) != Byte.toUnsignedInt(payload[198]))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) == 1 && Byte.toUnsignedInt(payload[200]) >= Byte.toUnsignedInt(payload[201]) && (Byte.toUnsignedInt(payload[192]) != 1 || Byte.toUnsignedInt(payload[193]) != 1 || Byte.toUnsignedInt(payload[191]) != (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 194, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < Integer.toUnsignedLong(ByteBuffer.wrap(payload, 202, 4).order(ByteOrder.BIG_ENDIAN).getInt()) ? 2 : 1))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[97]) == 1 && Byte.toUnsignedInt(payload[200]) < Byte.toUnsignedInt(payload[201]) && (Byte.toUnsignedInt(payload[192]) != (Byte.toUnsignedInt(payload[200]) == 0 ? 2 : 3) || Byte.toUnsignedInt(payload[191]) != 1 || Byte.toUnsignedInt(payload[193]) != 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 194, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[71]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) < 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[71]) == 2 || Byte.toUnsignedInt(payload[71]) == 3) && (Byte.toUnsignedInt(payload[78]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 87, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 79, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) > 3 && ByteBuffer.wrap(payload, 72, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_WORK_PREUNLOCK_WEIGHT_READY:
            if (payload.length < 95 || payload.length > 95) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 53, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) != 0 && Byte.toUnsignedInt(payload[69]) != 1 && Byte.toUnsignedInt(payload[69]) != 2 && Byte.toUnsignedInt(payload[69]) != 3 && Byte.toUnsignedInt(payload[69]) != 4 && Byte.toUnsignedInt(payload[69]) != 5 && Byte.toUnsignedInt(payload[69]) != 6 && Byte.toUnsignedInt(payload[69]) != 7 && Byte.toUnsignedInt(payload[69]) != 8 && Byte.toUnsignedInt(payload[69]) != 9 && Byte.toUnsignedInt(payload[69]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 74, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 87, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 87, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[69]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) < 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 74, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[69]) == 2 || Byte.toUnsignedInt(payload[69]) == 3) && (Byte.toUnsignedInt(payload[76]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 77, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 74, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) > 3 && ByteBuffer.wrap(payload, 70, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_CLEAN_FINAL_WEIGHT_READY:
            if (payload.length < 191 || payload.length > 191) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[36]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[36]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 37, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 39, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 0 && Byte.toUnsignedInt(payload[55]) != 1 && Byte.toUnsignedInt(payload[55]) != 2 && Byte.toUnsignedInt(payload[55]) != 3 && Byte.toUnsignedInt(payload[55]) != 4 && Byte.toUnsignedInt(payload[55]) != 5 && Byte.toUnsignedInt(payload[55]) != 6 && Byte.toUnsignedInt(payload[55]) != 7 && Byte.toUnsignedInt(payload[55]) != 8 && Byte.toUnsignedInt(payload[55]) != 9 && Byte.toUnsignedInt(payload[55]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 60, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[62]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 73, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 73, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) != 0 && Byte.toUnsignedInt(payload[81]) != 1 && Byte.toUnsignedInt(payload[81]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 150, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 150, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 158, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 158, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[174]) != 0 && Byte.toUnsignedInt(payload[174]) != 1 && Byte.toUnsignedInt(payload[174]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[175]) != 0 && Byte.toUnsignedInt(payload[175]) != 1 && Byte.toUnsignedInt(payload[175]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[176]) != 0 && Byte.toUnsignedInt(payload[176]) != 1 && Byte.toUnsignedInt(payload[176]) != 2 && Byte.toUnsignedInt(payload[176]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[177]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[182]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[183]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[184]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[185]) > 9L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 186, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 4000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[190]) != 0 && Byte.toUnsignedInt(payload[190]) != 1 && Byte.toUnsignedInt(payload[190]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) == 0 && !bytesZero(payload, 81, 110)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) != 0 && (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 82, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0 || Byte.toUnsignedInt(payload[174]) != 1 || bytesZero(payload, 86, 32) || bytesZero(payload, 118, 32))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) != 0 && (Byte.toUnsignedInt(payload[182]) < 3 || Byte.toUnsignedInt(payload[185]) == 0 || Byte.toUnsignedInt(payload[185]) > Byte.toUnsignedInt(payload[182]) || Byte.toUnsignedInt(payload[183]) > Byte.toUnsignedInt(payload[182]) || Byte.toUnsignedInt(payload[184]) > Byte.toUnsignedInt(payload[183]) || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 186, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) != 0 && (ByteBuffer.wrap(payload, 158, 8).order(ByteOrder.BIG_ENDIAN).getLong() < ByteBuffer.wrap(payload, 150, 8).order(ByteOrder.BIG_ENDIAN).getLong() || (Byte.toUnsignedInt(payload[183]) == 0 && ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() != 0) || (Byte.toUnsignedInt(payload[183]) > 0 && (ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() < ByteBuffer.wrap(payload, 150, 8).order(ByteOrder.BIG_ENDIAN).getLong() || ByteBuffer.wrap(payload, 166, 8).order(ByteOrder.BIG_ENDIAN).getLong() > ByteBuffer.wrap(payload, 158, 8).order(ByteOrder.BIG_ENDIAN).getLong())))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) == 2 && (Byte.toUnsignedInt(payload[190]) == 0 || Byte.toUnsignedInt(payload[175]) != 0 || Byte.toUnsignedInt(payload[176]) != 0 || Byte.toUnsignedInt(payload[177]) != 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 178, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) == 1 && (Byte.toUnsignedInt(payload[190]) != 0 || Byte.toUnsignedInt(payload[183]) != Byte.toUnsignedInt(payload[182]))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) == 1 && Byte.toUnsignedInt(payload[184]) >= Byte.toUnsignedInt(payload[185]) && (Byte.toUnsignedInt(payload[176]) != 1 || Byte.toUnsignedInt(payload[177]) != 1 || Byte.toUnsignedInt(payload[175]) != (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 178, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < Integer.toUnsignedLong(ByteBuffer.wrap(payload, 186, 4).order(ByteOrder.BIG_ENDIAN).getInt()) ? 2 : 1))) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[81]) == 1 && Byte.toUnsignedInt(payload[184]) < Byte.toUnsignedInt(payload[185]) && (Byte.toUnsignedInt(payload[176]) != (Byte.toUnsignedInt(payload[184]) == 0 ? 2 : 3) || Byte.toUnsignedInt(payload[175]) != 1 || Byte.toUnsignedInt(payload[177]) != 0 || Integer.toUnsignedLong(ByteBuffer.wrap(payload, 178, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[55]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) < 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 60, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[55]) == 2 || Byte.toUnsignedInt(payload[55]) == 3) && (Byte.toUnsignedInt(payload[62]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 71, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 63, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 60, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) > 3 && ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_FULLNESS_SAMPLE_RESULT:
            if (payload.length < 106 || payload.length > 106) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[53]) != 1 && Byte.toUnsignedInt(payload[53]) != 2 && Byte.toUnsignedInt(payload[53]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[54]) != 1 && Byte.toUnsignedInt(payload[54]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 1 && Byte.toUnsignedInt(payload[55]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 1 && Byte.toUnsignedInt(payload[56]) != 2 && Byte.toUnsignedInt(payload[56]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[57]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[62]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 64, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[80]) != 0 && Byte.toUnsignedInt(payload[80]) != 1 && Byte.toUnsignedInt(payload[80]) != 2 && Byte.toUnsignedInt(payload[80]) != 3 && Byte.toUnsignedInt(payload[80]) != 4 && Byte.toUnsignedInt(payload[80]) != 5 && Byte.toUnsignedInt(payload[80]) != 6 && Byte.toUnsignedInt(payload[80]) != 7 && Byte.toUnsignedInt(payload[80]) != 8 && Byte.toUnsignedInt(payload[80]) != 9 && Byte.toUnsignedInt(payload[80]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[87]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 98, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 98, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[80]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[80]) < 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[80]) == 2 || Byte.toUnsignedInt(payload[80]) == 3) && (Byte.toUnsignedInt(payload[87]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 96, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[80]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 88, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[80]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[80]) > 3 && ByteBuffer.wrap(payload, 81, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) == 1 && Byte.toUnsignedInt(payload[57]) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 1 && (Byte.toUnsignedInt(payload[55]) != 1 || Byte.toUnsignedInt(payload[57]) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[57]) == 0 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 58, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[63]) > Byte.toUnsignedInt(payload[62])) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_BASELINE_MEASUREMENT_RESULT:
            if (payload.length < 95 || payload.length > 95) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 53, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) != 0 && Byte.toUnsignedInt(payload[69]) != 1 && Byte.toUnsignedInt(payload[69]) != 2 && Byte.toUnsignedInt(payload[69]) != 3 && Byte.toUnsignedInt(payload[69]) != 4 && Byte.toUnsignedInt(payload[69]) != 5 && Byte.toUnsignedInt(payload[69]) != 6 && Byte.toUnsignedInt(payload[69]) != 7 && Byte.toUnsignedInt(payload[69]) != 8 && Byte.toUnsignedInt(payload[69]) != 9 && Byte.toUnsignedInt(payload[69]) != 10) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 74, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 5000L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 32L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 87, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 87, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[69]) == 10) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1031)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) < 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 74, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong()) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[69]) == 2 || Byte.toUnsignedInt(payload[69]) == 3) && (Byte.toUnsignedInt(payload[76]) < 5 || Short.toUnsignedInt(ByteBuffer.wrap(payload, 85, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) == 2 && Integer.toUnsignedLong(ByteBuffer.wrap(payload, 77, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) == 3 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 74, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5000) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[69]) > 3 && ByteBuffer.wrap(payload, 70, 4).order(ByteOrder.BIG_ENDIAN).getInt() != 0) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DELIVERY_SELECTION:
            if (payload.length < 80 || payload.length > 80) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 55, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 71, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 71, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[79]) != 1 && Byte.toUnsignedInt(payload[79]) != 2 && Byte.toUnsignedInt(payload[79]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_CLEAN_UNLOCK_REQUESTED:
            if (payload.length < 63 || payload.length > 63) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 55, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 55, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_CLEAN_FINISH_REQUESTED:
            if (payload.length < 63 || payload.length > 63) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 55, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 55, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_CLEAN_COMPLETION_CONFIRMED:
            if (payload.length < 83 || payload.length > 83) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 55, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) != 1 && Byte.toUnsignedInt(payload[71]) != 2 && Byte.toUnsignedInt(payload[71]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[72]) != 1 && Byte.toUnsignedInt(payload[72]) != 2 && Byte.toUnsignedInt(payload[72]) != 3 && Byte.toUnsignedInt(payload[72]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[73]) != 1 && Byte.toUnsignedInt(payload[73]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[74]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[74]) != 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 75, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 75, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[71]) != 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[73]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DELIVERY_DOOR_COMMAND_RESULT:
            if (payload.length < 60 || payload.length > 60) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 0 && Byte.toUnsignedInt(payload[55]) != 1 && Byte.toUnsignedInt(payload[55]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 0 && Byte.toUnsignedInt(payload[56]) != 1 && Byte.toUnsignedInt(payload[56]) != 2 && Byte.toUnsignedInt(payload[56]) != 3 && Byte.toUnsignedInt(payload[56]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[57]) != 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) == 0 || Byte.toUnsignedInt(payload[56]) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) == 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) == 3 && Byte.toUnsignedInt(payload[55]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_CLEAN_LOCK_POWER_CHANGED:
            if (payload.length < 55 || payload.length > 55) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[53]) != 1 && Byte.toUnsignedInt(payload[53]) != 2 && Byte.toUnsignedInt(payload[53]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[54]) != 1 && Byte.toUnsignedInt(payload[54]) != 2 && Byte.toUnsignedInt(payload[54]) != 3 && Byte.toUnsignedInt(payload[54]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_SAFE_CLOSE_RESULT:
            if (payload.length < 43 || payload.length > 43) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[36]) != 1 && Byte.toUnsignedInt(payload[36]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[37]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[37]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[38]) != 0 && Byte.toUnsignedInt(payload[38]) != 1 && Byte.toUnsignedInt(payload[38]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[39]) != 0 && Byte.toUnsignedInt(payload[39]) != 1 && Byte.toUnsignedInt(payload[39]) != 2 && Byte.toUnsignedInt(payload[39]) != 3 && Byte.toUnsignedInt(payload[39]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[40]) != 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[38]) == 0 || Byte.toUnsignedInt(payload[39]) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[39]) == 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[39]) != 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 41, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[39]) == 3 && Byte.toUnsignedInt(payload[38]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[38]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DELIVERY_LOCAL_DOOR_RESULT:
            if (payload.length < 64 || payload.length > 64) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 2L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 0 && Byte.toUnsignedInt(payload[55]) != 1 && Byte.toUnsignedInt(payload[55]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 0 && Byte.toUnsignedInt(payload[56]) != 1 && Byte.toUnsignedInt(payload[56]) != 2 && Byte.toUnsignedInt(payload[56]) != 3 && Byte.toUnsignedInt(payload[56]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[57]) != 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 256 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 257 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 768 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1024 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1025 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1026 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1027 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1028 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1029 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1030 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1031 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1280 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1536 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 1792 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 2048) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 60, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 60, 4).order(ByteOrder.BIG_ENDIAN).getInt()) >= Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt())) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) == 0 || Byte.toUnsignedInt(payload[56]) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) == 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 513 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 514) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 4 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 58, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) == 3 && Byte.toUnsignedInt(payload[55]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DELIVERY_CYCLE_ABORTED:
            if (payload.length < 61 || payload.length > 61) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 1 && Byte.toUnsignedInt(payload[55]) != 2 && Byte.toUnsignedInt(payload[55]) != 3) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) > 1) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 3 && Byte.toUnsignedInt(payload[56]) != 0) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 1) != (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 57, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 57, 4).order(ByteOrder.BIG_ENDIAN).getInt()) >= Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt())) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED:
            if (payload.length < 61 || payload.length > 61) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 0 && Byte.toUnsignedInt(payload[55]) != 1 && Byte.toUnsignedInt(payload[55]) != 2 && Byte.toUnsignedInt(payload[55]) != 16 && Byte.toUnsignedInt(payload[55]) != 17 && Byte.toUnsignedInt(payload[55]) != 18 && Byte.toUnsignedInt(payload[55]) != 19 && Byte.toUnsignedInt(payload[55]) != 20 && Byte.toUnsignedInt(payload[55]) != 21 && Byte.toUnsignedInt(payload[55]) != 22 && Byte.toUnsignedInt(payload[55]) != 23 && Byte.toUnsignedInt(payload[55]) != 24 && Byte.toUnsignedInt(payload[55]) != 25 && Byte.toUnsignedInt(payload[55]) != 26 && Byte.toUnsignedInt(payload[55]) != 32 && Byte.toUnsignedInt(payload[55]) != 33 && Byte.toUnsignedInt(payload[55]) != 34 && Byte.toUnsignedInt(payload[55]) != 35 && Byte.toUnsignedInt(payload[55]) != 36 && Byte.toUnsignedInt(payload[55]) != 37 && Byte.toUnsignedInt(payload[55]) != 38 && Byte.toUnsignedInt(payload[55]) != 39 && Byte.toUnsignedInt(payload[55]) != 40 && Byte.toUnsignedInt(payload[55]) != 48 && Byte.toUnsignedInt(payload[55]) != 49 && Byte.toUnsignedInt(payload[55]) != 64) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 1 && Byte.toUnsignedInt(payload[56]) != 2) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 23 && Byte.toUnsignedInt(payload[55]) != 24 && Byte.toUnsignedInt(payload[55]) != 25 && Byte.toUnsignedInt(payload[55]) != 26) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[55]) == 23) != (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 57, 4).order(ByteOrder.BIG_ENDIAN).getInt()) == 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 57, 4).order(ByteOrder.BIG_ENDIAN).getInt()) >= Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt())) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_CLEAN_OPERATION_INTERRUPTED:
            if (payload.length < 61 || payload.length > 61) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 0, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 0L) { throw new IllegalArgumentException("invalid session payload"); }
            if (ByteBuffer.wrap(payload, 12, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 20, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (bytesZero(payload, 36, 16)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) < 1L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[52]) > 6L) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[55]) != 0 && Byte.toUnsignedInt(payload[55]) != 1 && Byte.toUnsignedInt(payload[55]) != 2 && Byte.toUnsignedInt(payload[55]) != 16 && Byte.toUnsignedInt(payload[55]) != 17 && Byte.toUnsignedInt(payload[55]) != 18 && Byte.toUnsignedInt(payload[55]) != 19 && Byte.toUnsignedInt(payload[55]) != 20 && Byte.toUnsignedInt(payload[55]) != 21 && Byte.toUnsignedInt(payload[55]) != 22 && Byte.toUnsignedInt(payload[55]) != 23 && Byte.toUnsignedInt(payload[55]) != 24 && Byte.toUnsignedInt(payload[55]) != 25 && Byte.toUnsignedInt(payload[55]) != 26 && Byte.toUnsignedInt(payload[55]) != 32 && Byte.toUnsignedInt(payload[55]) != 33 && Byte.toUnsignedInt(payload[55]) != 34 && Byte.toUnsignedInt(payload[55]) != 35 && Byte.toUnsignedInt(payload[55]) != 36 && Byte.toUnsignedInt(payload[55]) != 37 && Byte.toUnsignedInt(payload[55]) != 38 && Byte.toUnsignedInt(payload[55]) != 39 && Byte.toUnsignedInt(payload[55]) != 40 && Byte.toUnsignedInt(payload[55]) != 48 && Byte.toUnsignedInt(payload[55]) != 49 && Byte.toUnsignedInt(payload[55]) != 64) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 1 && Byte.toUnsignedInt(payload[56]) != 2 && Byte.toUnsignedInt(payload[56]) != 3 && Byte.toUnsignedInt(payload[56]) != 4) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) != 4 && (Byte.toUnsignedInt(payload[55]) != 35 && Byte.toUnsignedInt(payload[55]) != 36 && Byte.toUnsignedInt(payload[55]) != 37 && Byte.toUnsignedInt(payload[55]) != 38)) { throw new IllegalArgumentException("invalid session payload"); }
            if (Byte.toUnsignedInt(payload[56]) == 4 && !((Byte.toUnsignedInt(payload[55]) == 34 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 0) || (Byte.toUnsignedInt(payload[55]) == 36 && Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 0))) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[55]) == 37 || Byte.toUnsignedInt(payload[55]) == 38) != (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 57, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 0)) { throw new IllegalArgumentException("invalid session payload"); }
            if ((Byte.toUnsignedInt(payload[55]) == 37 || Byte.toUnsignedInt(payload[55]) == 38) && Short.toUnsignedInt(ByteBuffer.wrap(payload, 53, 2).order(ByteOrder.BIG_ENDIAN).getShort()) == 0) { throw new IllegalArgumentException("invalid session payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 57, 4).order(ByteOrder.BIG_ENDIAN).getInt()) >= Integer.toUnsignedLong(ByteBuffer.wrap(payload, 8, 4).order(ByteOrder.BIG_ENDIAN).getInt())) { throw new IllegalArgumentException("invalid session payload"); }
            return;
        case MESSAGE_QUERY_STATE:
            if (payload.length < 76 || payload.length > 76) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_SAFE_CLOSE:
            if (payload.length < 66 || payload.length > 66) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[60]) != 1 && Byte.toUnsignedInt(payload[60]) != 2) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[61]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 62, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            if ((Byte.toUnsignedInt(payload[60]) == 1) != (Byte.toUnsignedInt(payload[61]) == 0)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_CONFIG_BEGIN:
            if (payload.length < 151 || payload.length > 151) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) != 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) < 4L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) > 9L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[150]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[150]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) != Byte.toUnsignedInt(payload[150]) + 3) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_CONFIG_DEVICE_BLOCK:
            if (payload.length < 183 || payload.length > 183) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) != 2L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) < 4L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) > 9L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 150, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 154, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 158, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 158, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 600000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 162, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 5000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 166, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 30000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 166, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 45000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 170, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 170, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 5000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[174]) > 1) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 175, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 250L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 179, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 200L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_CONFIG_PORT_BLOCK:
            if (payload.length < 207 || payload.length > 207) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) < 3L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) > 8L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) < 4L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) > 9L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[150]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[150]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[151]) > 1) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 152, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[156]) != 1 && Byte.toUnsignedInt(payload[156]) != 2 && Byte.toUnsignedInt(payload[156]) != 3) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 157, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[165]) != 1 && Byte.toUnsignedInt(payload[165]) != 2) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 166, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 166, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 4000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[170]) < 3L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[170]) > 9L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[171]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[171]) > 9L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 172, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 100L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 172, 4).order(ByteOrder.BIG_ENDIAN).getInt()) > 100000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 176, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 1500L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 180, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 100L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 184, 2).order(ByteOrder.BIG_ENDIAN).getShort()) != 5L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 186, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 5000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 194, 4).order(ByteOrder.BIG_ENDIAN).getInt() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 202, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 750L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[206]) != 5L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) >= Byte.toUnsignedInt(payload[149])) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) != Byte.toUnsignedInt(payload[150]) + 2) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 190, 4).order(ByteOrder.BIG_ENDIAN).getInt() >= ByteBuffer.wrap(payload, 194, 4).order(ByteOrder.BIG_ENDIAN).getInt()) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[171]) > Byte.toUnsignedInt(payload[170])) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_CONFIG_COMMIT:
            if (payload.length < 150 || payload.length > 150) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 76, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) < 4L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) > 9L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) < 4L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[149]) > 9L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[148]) != Byte.toUnsignedInt(payload[149])) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_DEVICE_ENTRY_URL_BEGIN:
            if (payload.length < 111 || payload.length > 111) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 192L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[110]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[110]) > 3L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[110]) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) + 63) / 64) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 78, 32)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_DEVICE_ENTRY_URL_PART:
            if (payload.length < 111 || payload.length > 175) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[108]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[108]) > 3L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[109]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[109]) > 3L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[110]) == 0 || Byte.toUnsignedInt(payload[110]) > 64 || payload.length != 111 + Byte.toUnsignedInt(payload[110])) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[108]) > Byte.toUnsignedInt(payload[109])) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 76, 32)) { throw new IllegalArgumentException("invalid command payload"); }
            if (!deviceEntryUrlChunkSafe(payload, 111, Byte.toUnsignedInt(payload[110]), Byte.toUnsignedInt(payload[108]) == 1)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_DEVICE_ENTRY_URL_COMMIT:
            if (payload.length < 111 || payload.length > 111) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) > 192L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[110]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[110]) > 3L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[110]) != (Short.toUnsignedInt(ByteBuffer.wrap(payload, 76, 2).order(ByteOrder.BIG_ENDIAN).getShort()) + 63) / 64) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 78, 32)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_START_DELIVERY_SESSION:
            if (payload.length < 137 || payload.length > 137) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 77, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 77, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 117, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 121, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 125, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 129, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 133, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_START_CLEAN_OPERATION:
            if (payload.length < 125 || payload.length > 125) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 77, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 77, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 117, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 121, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_UNLOCK_CLEAN_DOOR:
            if (payload.length < 107 || payload.length > 107) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 83, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 87, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 91, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_RESUME_CLEAN_OPERATION:
            if (payload.length < 123 || payload.length > 123) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 77, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Short.toUnsignedInt(ByteBuffer.wrap(payload, 81, 2).order(ByteOrder.BIG_ENDIAN).getShort()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 83, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 83, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_END_CLEAN_BEFORE_UNLOCK:
            if (payload.length < 102 || payload.length > 102) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 77, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 97, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[101]) != 1 && Byte.toUnsignedInt(payload[101]) != 2 && Byte.toUnsignedInt(payload[101]) != 3) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_SAMPLE_FULLNESS:
            if (payload.length < 130 || payload.length > 130) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[77]) != 1 && Byte.toUnsignedInt(payload[77]) != 2 && Byte.toUnsignedInt(payload[77]) != 3) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 78, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 78, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 118, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 126, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 5000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_MEASURE_BASELINE:
            if (payload.length < 125 || payload.length > 125) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 77, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 77, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 117, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 121, 4).order(ByteOrder.BIG_ENDIAN).getInt()) != 5000L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_AUTHORIZE_DELIVERY_FIRST_OPEN:
            if (payload.length < 113 || payload.length > 113) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 60, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Byte.toUnsignedInt(payload[76]) > 6L) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 77, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 93, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 109, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        case MESSAGE_CONFIRM_NO_ACTIVE_WORK:
            if (payload.length < 100 || payload.length > 100) { throw new IllegalArgumentException("invalid command payload"); }
            if (bytesZero(payload, 0, 16)) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 48, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (Integer.toUnsignedLong(ByteBuffer.wrap(payload, 56, 4).order(ByteOrder.BIG_ENDIAN).getInt()) < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() < 1L) { throw new IllegalArgumentException("invalid command payload"); }
            if (ByteBuffer.wrap(payload, 60, 8).order(ByteOrder.BIG_ENDIAN).getLong() > 9007199254740991L) { throw new IllegalArgumentException("invalid command payload"); }
            if (!commandDigestMatches(messageType, payload)) { throw new IllegalArgumentException("invalid command payload"); }
            return;
        default: return;
        }
    }
    private static boolean resultDigestMatches(byte[] payload) {
        byte[] prefix = java.util.HexFormat.of().parseHex("45434f42494e3a554152543a574f524b2d524553554c543a7632004000c7");
        byte[] preimage = new byte[prefix.length + 167];
        System.arraycopy(prefix, 0, preimage, 0, prefix.length);
        System.arraycopy(payload, 0, preimage, prefix.length, 28);
        System.arraycopy(payload, 60, preimage, prefix.length + 28, 139);
        return Arrays.equals(sha256(preimage), Arrays.copyOfRange(payload, 28, 60));
    }
    private static boolean commandDigestMatches(int messageType, byte[] payload) {
        byte[] domain = java.util.HexFormat.of().parseHex("45434f42494e3a554152543a434f4d4d414e443a763200");
        int semanticLength = payload.length - 48;
        byte[] preimage = new byte[domain.length + 3 + semanticLength];
        System.arraycopy(domain, 0, preimage, 0, domain.length);
        preimage[domain.length] = (byte) messageType;
        preimage[domain.length + 1] = (byte) (semanticLength >>> 8);
        preimage[domain.length + 2] = (byte) semanticLength;
        System.arraycopy(payload, 48, preimage, domain.length + 3, semanticLength);
        return Arrays.equals(sha256(preimage), Arrays.copyOfRange(payload, 16, 48));
    }

    public static Boolean ackRequired(int messageType) {
        return switch (messageType) {
            case MESSAGE_QUERY_DEVICE_FACTS -> false;
            case MESSAGE_DEVICE_FACTS_REPLY -> false;
            case MESSAGE_QUERY_DEVICE_IDENTITY -> false;
            case MESSAGE_DEVICE_IDENTITY_REPLY -> false;
            case MESSAGE_ACTUATOR_EVENT_SAVED -> false;
            case MESSAGE_ACTUATOR_EVENT_SAVED_REPLY -> false;
            case MESSAGE_QUERY_ACTUATOR_EVENT -> false;
            case MESSAGE_ACTUATOR_EVENT_QUERY_REPLY -> false;
            case MESSAGE_PROCESS_EVENT_SAVED -> false;
            case MESSAGE_PROCESS_EVENT_SAVED_REPLY -> false;
            case MESSAGE_QUERY_PROCESS_EVENT -> false;
            case MESSAGE_PROCESS_EVENT_QUERY_REPLY -> false;
            case MESSAGE_QUERY_WORK -> false;
            case MESSAGE_WORK_QUERY_REPLY -> false;
            case MESSAGE_WORK_RESULT -> false;
            case MESSAGE_QUERY_RESULT -> false;
            case MESSAGE_RESULT_QUERY_REPLY -> false;
            case MESSAGE_HELLO -> false;
            case MESSAGE_HELLO_ACK -> false;
            case MESSAGE_ACK -> false;
            case MESSAGE_NACK -> false;
            case MESSAGE_QUERY_STATE -> true;
            case MESSAGE_SAFE_CLOSE -> true;
            case MESSAGE_BOOT_PROBE -> false;
            case MESSAGE_BOOT_PROBE_REPLY -> false;
            case MESSAGE_BIND_BOOT -> false;
            case MESSAGE_BIND_BOOT_REPLY -> false;
            case MESSAGE_COMMAND_DECISION -> false;
            case MESSAGE_QUERY_COMMAND -> false;
            case MESSAGE_COMMAND_QUERY_RESULT -> false;
            case MESSAGE_RESULT_SAVED -> false;
            case MESSAGE_RESULT_SAVED_REPLY -> false;
            case MESSAGE_CONFIG_BEGIN -> true;
            case MESSAGE_CONFIG_DEVICE_BLOCK -> true;
            case MESSAGE_CONFIG_PORT_BLOCK -> true;
            case MESSAGE_CONFIG_COMMIT -> true;
            case MESSAGE_CONFIG_APPLY_RESULT -> true;
            case MESSAGE_DEVICE_ENTRY_URL_BEGIN -> true;
            case MESSAGE_DEVICE_ENTRY_URL_PART -> true;
            case MESSAGE_DEVICE_ENTRY_URL_COMMIT -> true;
            case MESSAGE_DEVICE_ENTRY_URL_APPLY_RESULT -> false;
            case MESSAGE_START_DELIVERY_SESSION -> true;
            case MESSAGE_START_CLEAN_OPERATION -> true;
            case MESSAGE_UNLOCK_CLEAN_DOOR -> true;
            case MESSAGE_RESUME_CLEAN_OPERATION -> true;
            case MESSAGE_END_CLEAN_BEFORE_UNLOCK -> true;
            case MESSAGE_SAMPLE_FULLNESS -> true;
            case MESSAGE_MEASURE_BASELINE -> true;
            case MESSAGE_AUTHORIZE_DELIVERY_FIRST_OPEN -> true;
            case MESSAGE_CONFIRM_NO_ACTIVE_WORK -> true;
            case MESSAGE_WORK_PREOPEN_WEIGHT_READY -> true;
            case MESSAGE_DELIVERY_DOOR_COMMAND_RESULT -> true;
            case MESSAGE_DELIVERY_LOCAL_DOOR_RESULT -> true;
            case MESSAGE_CLEAN_OPERATION_INTERRUPTED -> true;
            case MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED -> true;
            case MESSAGE_DELIVERY_CYCLE_ABORTED -> true;
            case MESSAGE_WORK_POSTCLOSE_WEIGHT_READY -> true;
            case MESSAGE_DELIVERY_SELECTION -> true;
            case MESSAGE_WORK_PREUNLOCK_WEIGHT_READY -> true;
            case MESSAGE_CLEAN_LOCK_POWER_CHANGED -> true;
            case MESSAGE_CLEAN_UNLOCK_REQUESTED -> true;
            case MESSAGE_CLEAN_FINISH_REQUESTED -> true;
            case MESSAGE_CLEAN_FINAL_WEIGHT_READY -> true;
            case MESSAGE_FULLNESS_SAMPLE_RESULT -> true;
            case MESSAGE_BASELINE_MEASUREMENT_RESULT -> true;
            case MESSAGE_FAULT_OBSERVED -> true;
            case MESSAGE_SAFETY_SENSOR_EVENT -> true;
            case MESSAGE_SAFE_CLOSE_RESULT -> true;
            case MESSAGE_CLEAN_COMPLETION_CONFIRMED -> true;
            case MESSAGE_BOOT_RECONCILIATION_RESULT -> true;
            case MESSAGE_STATE_SNAPSHOT_BEGIN -> true;
            case MESSAGE_STATE_SNAPSHOT_PORT -> true;
            case MESSAGE_STATE_SNAPSHOT_END -> true;
            default -> null;
        };
    }

    public static Direction direction(int messageType) {
        return switch (messageType) {
            case MESSAGE_QUERY_DEVICE_FACTS -> Direction.EDGE_TO_MCU;
            case MESSAGE_DEVICE_FACTS_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_QUERY_DEVICE_IDENTITY -> Direction.EDGE_TO_MCU;
            case MESSAGE_DEVICE_IDENTITY_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_ACTUATOR_EVENT_SAVED -> Direction.EDGE_TO_MCU;
            case MESSAGE_ACTUATOR_EVENT_SAVED_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_QUERY_ACTUATOR_EVENT -> Direction.EDGE_TO_MCU;
            case MESSAGE_ACTUATOR_EVENT_QUERY_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_PROCESS_EVENT_SAVED -> Direction.EDGE_TO_MCU;
            case MESSAGE_PROCESS_EVENT_SAVED_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_QUERY_PROCESS_EVENT -> Direction.EDGE_TO_MCU;
            case MESSAGE_PROCESS_EVENT_QUERY_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_QUERY_WORK -> Direction.EDGE_TO_MCU;
            case MESSAGE_WORK_QUERY_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_WORK_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_QUERY_RESULT -> Direction.EDGE_TO_MCU;
            case MESSAGE_RESULT_QUERY_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_HELLO -> Direction.BIDIRECTIONAL;
            case MESSAGE_HELLO_ACK -> Direction.BIDIRECTIONAL;
            case MESSAGE_ACK -> Direction.BIDIRECTIONAL;
            case MESSAGE_NACK -> Direction.BIDIRECTIONAL;
            case MESSAGE_QUERY_STATE -> Direction.EDGE_TO_MCU;
            case MESSAGE_SAFE_CLOSE -> Direction.EDGE_TO_MCU;
            case MESSAGE_BOOT_PROBE -> Direction.EDGE_TO_MCU;
            case MESSAGE_BOOT_PROBE_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_BIND_BOOT -> Direction.EDGE_TO_MCU;
            case MESSAGE_BIND_BOOT_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_COMMAND_DECISION -> Direction.MCU_TO_EDGE;
            case MESSAGE_QUERY_COMMAND -> Direction.EDGE_TO_MCU;
            case MESSAGE_COMMAND_QUERY_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_RESULT_SAVED -> Direction.EDGE_TO_MCU;
            case MESSAGE_RESULT_SAVED_REPLY -> Direction.MCU_TO_EDGE;
            case MESSAGE_CONFIG_BEGIN -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_DEVICE_BLOCK -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_PORT_BLOCK -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_COMMIT -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_APPLY_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_DEVICE_ENTRY_URL_BEGIN -> Direction.EDGE_TO_MCU;
            case MESSAGE_DEVICE_ENTRY_URL_PART -> Direction.EDGE_TO_MCU;
            case MESSAGE_DEVICE_ENTRY_URL_COMMIT -> Direction.EDGE_TO_MCU;
            case MESSAGE_DEVICE_ENTRY_URL_APPLY_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_START_DELIVERY_SESSION -> Direction.EDGE_TO_MCU;
            case MESSAGE_START_CLEAN_OPERATION -> Direction.EDGE_TO_MCU;
            case MESSAGE_UNLOCK_CLEAN_DOOR -> Direction.EDGE_TO_MCU;
            case MESSAGE_RESUME_CLEAN_OPERATION -> Direction.EDGE_TO_MCU;
            case MESSAGE_END_CLEAN_BEFORE_UNLOCK -> Direction.EDGE_TO_MCU;
            case MESSAGE_SAMPLE_FULLNESS -> Direction.EDGE_TO_MCU;
            case MESSAGE_MEASURE_BASELINE -> Direction.EDGE_TO_MCU;
            case MESSAGE_AUTHORIZE_DELIVERY_FIRST_OPEN -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIRM_NO_ACTIVE_WORK -> Direction.EDGE_TO_MCU;
            case MESSAGE_WORK_PREOPEN_WEIGHT_READY -> Direction.MCU_TO_EDGE;
            case MESSAGE_DELIVERY_DOOR_COMMAND_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_DELIVERY_LOCAL_DOOR_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_CLEAN_OPERATION_INTERRUPTED -> Direction.MCU_TO_EDGE;
            case MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED -> Direction.MCU_TO_EDGE;
            case MESSAGE_DELIVERY_CYCLE_ABORTED -> Direction.MCU_TO_EDGE;
            case MESSAGE_WORK_POSTCLOSE_WEIGHT_READY -> Direction.MCU_TO_EDGE;
            case MESSAGE_DELIVERY_SELECTION -> Direction.MCU_TO_EDGE;
            case MESSAGE_WORK_PREUNLOCK_WEIGHT_READY -> Direction.MCU_TO_EDGE;
            case MESSAGE_CLEAN_LOCK_POWER_CHANGED -> Direction.MCU_TO_EDGE;
            case MESSAGE_CLEAN_UNLOCK_REQUESTED -> Direction.MCU_TO_EDGE;
            case MESSAGE_CLEAN_FINISH_REQUESTED -> Direction.MCU_TO_EDGE;
            case MESSAGE_CLEAN_FINAL_WEIGHT_READY -> Direction.MCU_TO_EDGE;
            case MESSAGE_FULLNESS_SAMPLE_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_BASELINE_MEASUREMENT_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_FAULT_OBSERVED -> Direction.MCU_TO_EDGE;
            case MESSAGE_SAFETY_SENSOR_EVENT -> Direction.MCU_TO_EDGE;
            case MESSAGE_SAFE_CLOSE_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_CLEAN_COMPLETION_CONFIRMED -> Direction.MCU_TO_EDGE;
            case MESSAGE_BOOT_RECONCILIATION_RESULT -> Direction.MCU_TO_EDGE;
            case MESSAGE_STATE_SNAPSHOT_BEGIN -> Direction.MCU_TO_EDGE;
            case MESSAGE_STATE_SNAPSHOT_PORT -> Direction.MCU_TO_EDGE;
            case MESSAGE_STATE_SNAPSHOT_END -> Direction.MCU_TO_EDGE;
            default -> null;
        };
    }

    public static int crc16CcittFalse(byte[] data) {
        return crc16CcittFalse(data, 0, data.length);
    }

    public static int crc16CcittFalse(byte[] data, int offset, int length) {
        int crc = 0xFFFF;
        for (int index = offset; index < offset + length; index++) {
            crc ^= Byte.toUnsignedInt(data[index]) << 8;
            for (int bit = 0; bit < 8; bit++) {
                crc = ((crc & 0x8000) != 0)
                    ? ((crc << 1) ^ 0x1021) & 0xFFFF
                    : (crc << 1) & 0xFFFF;
            }
        }
        return crc;
    }

    public static byte[] sha256(byte[] data) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(data);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    public static long readU64(byte[] data, int offset) {
        long value = ByteBuffer.wrap(data, offset, 8)
            .order(ByteOrder.BIG_ENDIAN)
            .getLong();
        if (value < 0) {
            throw new IllegalArgumentException("uint64 exceeds signed Java long");
        }
        return value;
    }

    public static int readI32(byte[] data, int offset) {
        return ByteBuffer.wrap(data, offset, 4)
            .order(ByteOrder.BIG_ENDIAN)
            .getInt();
    }

    public static void writeU64(byte[] data, int offset, long value) {
        if (value < 0) {
            throw new IllegalArgumentException("uint64 value must be non-negative");
        }
        ByteBuffer.wrap(data, offset, 8)
            .order(ByteOrder.BIG_ENDIAN)
            .putLong(value);
    }

    public static void writeI32(byte[] data, int offset, int value) {
        ByteBuffer.wrap(data, offset, 4)
            .order(ByteOrder.BIG_ENDIAN)
            .putInt(value);
    }

    public static UUID readUuid(byte[] data, int offset) {
        ByteBuffer buffer = ByteBuffer.wrap(data, offset, 16)
            .order(ByteOrder.BIG_ENDIAN);
        return new UUID(buffer.getLong(), buffer.getLong());
    }

    public static void writeUuid(byte[] data, int offset, UUID value) {
        ByteBuffer buffer = ByteBuffer.wrap(data, offset, 16)
            .order(ByteOrder.BIG_ENDIAN);
        buffer.putLong(value.getMostSignificantBits());
        buffer.putLong(value.getLeastSignificantBits());
    }

    public static byte[] encodeFrame(
        int messageType,
        int flags,
        long txSequence,
        byte[] payload
    ) {
        if (messageType < 0 || messageType > 0xFF) {
            throw new IllegalArgumentException("messageType must fit uint8");
        }
        if ((flags & ~ACK_REQUIRED) != 0) {
            throw new IllegalArgumentException("unsupported flags");
        }
        if (txSequence < 1 || txSequence > 0xFFFF_FFFFL) {
            throw new IllegalArgumentException("txSequence must be positive uint32");
        }
        if (payload.length > MAXIMUM_PAYLOAD_LENGTH) {
            throw new IllegalArgumentException("payload too long");
        }
        ByteBuffer buffer = ByteBuffer
            .allocate(14 + payload.length)
            .order(ByteOrder.BIG_ENDIAN);
        buffer.put((byte) 0xEC);
        buffer.put((byte) 0x42);
        buffer.put((byte) PROTOCOL_MAJOR);
        buffer.put((byte) PROTOCOL_MINOR);
        buffer.put((byte) messageType);
        buffer.put((byte) flags);
        buffer.putShort((short) payload.length);
        buffer.putInt((int) txSequence);
        buffer.put(payload);
        byte[] frame = buffer.array();
        int crc = crc16CcittFalse(frame, 2, 10 + payload.length);
        buffer.putShort((short) crc);
        return frame;
    }

    public static Frame decodeFrame(byte[] frame) {
        return decodeFrame(frame, false, null);
    }

    public static Frame decodeFrame(
        byte[] frame,
        boolean requireKnownMessage,
        SenderRole senderRole
    ) {
        if (frame.length < 14 || frame.length > MAXIMUM_FRAME_LENGTH) {
            throw new IllegalArgumentException("invalid frame length");
        }
        if (Byte.toUnsignedInt(frame[0]) != 0xEC || Byte.toUnsignedInt(frame[1]) != 0x42) {
            throw new IllegalArgumentException("invalid magic");
        }
        ByteBuffer buffer = ByteBuffer.wrap(frame).order(ByteOrder.BIG_ENDIAN);
        buffer.position(2);
        int major = Byte.toUnsignedInt(buffer.get());
        int minor = Byte.toUnsignedInt(buffer.get());
        int messageType = Byte.toUnsignedInt(buffer.get());
        int flags = Byte.toUnsignedInt(buffer.get());
        int payloadLength = Short.toUnsignedInt(buffer.getShort());
        long txSequence = Integer.toUnsignedLong(buffer.getInt());
        if (payloadLength > MAXIMUM_PAYLOAD_LENGTH || frame.length != 14 + payloadLength) {
            throw new IllegalArgumentException("invalid payload length");
        }
        int expected = (Byte.toUnsignedInt(frame[frame.length - 2]) << 8)
            | Byte.toUnsignedInt(frame[frame.length - 1]);
        int actual = crc16CcittFalse(frame, 2, frame.length - 4);
        if (actual != expected) {
            throw new IllegalArgumentException("CRC mismatch");
        }
        if (major != PROTOCOL_MAJOR || minor != PROTOCOL_MINOR) {
            throw new IllegalArgumentException("unsupported version");
        }
        if ((flags & ~ACK_REQUIRED) != 0 || txSequence == 0) {
            throw new IllegalArgumentException("invalid flags or sequence");
        }
        Boolean expectedAck = ackRequired(messageType);
        Direction messageDirection = direction(messageType);
        if (requireKnownMessage && expectedAck == null) {
            throw new IllegalArgumentException("unsupported UART message type");
        }
        if (expectedAck != null
            && ((flags & ACK_REQUIRED) != 0) != expectedAck.booleanValue()) {
            throw new IllegalArgumentException(
                "ACK_REQUIRED flag differs from the message Registry"
            );
        }
        if (senderRole != null && messageDirection != null) {
            Direction expectedDirection = senderRole == SenderRole.EDGE
                ? Direction.EDGE_TO_MCU
                : Direction.MCU_TO_EDGE;
            if (messageDirection != Direction.BIDIRECTIONAL
                && messageDirection != expectedDirection) {
                throw new IllegalArgumentException(
                    "message direction differs from the sender role"
                );
            }
        }
        byte[] payload = Arrays.copyOfRange(frame, 12, frame.length - 2);
        validateSessionPayload(messageType, payload);
        return new Frame(messageType, flags, txSequence, payload, actual);
    }

    public static final class StreamParser {
        private final SenderRole senderRole;
        private byte[] buffer = new byte[0];
        private Long candidateStartedMs;
        private final List<String> diagnostics = new ArrayList<>();

        public StreamParser(SenderRole senderRole) {
            this.senderRole = senderRole;
        }

        public List<String> diagnostics() {
            return List.copyOf(diagnostics);
        }

        public int bufferedBytes() {
            return buffer.length;
        }

        private void discardFirst(String diagnostic) {
            diagnostics.add(diagnostic);
            buffer = Arrays.copyOfRange(buffer, 1, buffer.length);
            candidateStartedMs = null;
        }

        public List<Frame> feed(byte[] data, long nowMs) {
            if (nowMs < 0) {
                throw new IllegalArgumentException("nowMs must be non-negative");
            }
            List<Frame> frames = new ArrayList<>();
            int offset = 0;
            do {
                int available = 512 - buffer.length;
                if (available <= 0) {
                    throw new IllegalStateException("stream buffer did not drain");
                }
                int take = Math.min(available, data.length - offset);
                byte[] combined = Arrays.copyOf(buffer, buffer.length + take);
                System.arraycopy(data, offset, combined, buffer.length, take);
                buffer = combined;
                offset += take;
                drain(nowMs, frames);
            } while (offset < data.length);
            return frames;
        }

        private void drain(long nowMs, List<Frame> frames) {
            while (true) {
                int magicIndex = -1;
                for (int index = 0; index + 1 < buffer.length; index++) {
                    if (Byte.toUnsignedInt(buffer[index]) == 0xEC
                        && Byte.toUnsignedInt(buffer[index + 1]) == 0x42) {
                        magicIndex = index;
                        break;
                    }
                }
                if (magicIndex < 0) {
                    boolean keep = buffer.length > 0
                        && Byte.toUnsignedInt(buffer[buffer.length - 1]) == 0xEC;
                    buffer = keep ? new byte[] {(byte) 0xEC} : new byte[0];
                    candidateStartedMs = null;
                    break;
                }
                if (magicIndex > 0) {
                    buffer = Arrays.copyOfRange(buffer, magicIndex, buffer.length);
                    candidateStartedMs = null;
                    diagnostics.add("NOISE_DISCARDED");
                }
                if (candidateStartedMs == null) {
                    candidateStartedMs = nowMs;
                }
                if (buffer.length < 12) {
                    if (nowMs - candidateStartedMs >= 100) {
                        discardFirst("FRAME_TIMEOUT");
                        continue;
                    }
                    break;
                }
                int payloadLength = Short.toUnsignedInt(
                    ByteBuffer.wrap(buffer, 6, 2)
                        .order(ByteOrder.BIG_ENDIAN)
                        .getShort()
                );
                if (payloadLength > MAXIMUM_PAYLOAD_LENGTH) {
                    discardFirst("INVALID_LENGTH");
                    continue;
                }
                int frameLength = 14 + payloadLength;
                if (buffer.length < frameLength) {
                    if (nowMs - candidateStartedMs >= 100) {
                        discardFirst("FRAME_TIMEOUT");
                        continue;
                    }
                    break;
                }
                byte[] candidate = Arrays.copyOfRange(buffer, 0, frameLength);
                int expected = (Byte.toUnsignedInt(candidate[frameLength - 2]) << 8)
                    | Byte.toUnsignedInt(candidate[frameLength - 1]);
                int actual = crc16CcittFalse(candidate, 2, frameLength - 4);
                if (expected != actual) {
                    discardFirst("CRC_INVALID");
                    continue;
                }
                try {
                    frames.add(decodeFrame(candidate, true, senderRole));
                } catch (IllegalArgumentException error) {
                    diagnostics.add("SEMANTIC_REJECTED:" + error.getMessage());
                }
                buffer = Arrays.copyOfRange(buffer, frameLength, buffer.length);
                candidateStartedMs = null;
            }
        }
    }

    public record Frame(
        int messageType,
        int flags,
        long txSequence,
        byte[] payload,
        int crc16
    ) {}
}
