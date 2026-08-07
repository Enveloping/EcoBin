// Generated from contracts/uart/uart-registry.yaml.
// DO NOT EDIT. Registry SHA-256: d2b73386e99e7f5be3b05129617afcfe4499cbc4d3f2e8401a4ce5342ca90b75

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

public final class EcobinUartProtocol {
    public static final String REGISTRY_SHA256 = "d2b73386e99e7f5be3b05129617afcfe4499cbc4d3f2e8401a4ce5342ca90b75";
    public static final int BAUD_RATE = 115200;
    public static final int DATA_BITS = 8;
    public static final int STOP_BITS = 1;
    public static final String PARITY = "NONE";
    public static final String FLOW_CONTROL = "NONE";
    public static final int PROTOCOL_MAJOR = 1;
    public static final int PROTOCOL_MINOR = 0;
    public static final int MAXIMUM_FRAME_LENGTH = 256;
    public static final int MAXIMUM_PAYLOAD_LENGTH = 242;
    public static final int ACK_REQUIRED = 0x01;
    public static final int MESSAGE_HELLO = 0x01;
    public static final int MESSAGE_HELLO_ACK = 0x02;
    public static final int MESSAGE_ACK = 0x03;
    public static final int MESSAGE_NACK = 0x04;
    public static final int MESSAGE_QUERY_STATE = 0x05;
    public static final int MESSAGE_SAFE_CLOSE = 0x06;
    public static final int MESSAGE_CONFIG_BEGIN = 0x10;
    public static final int MESSAGE_CONFIG_DEVICE_BLOCK = 0x11;
    public static final int MESSAGE_CONFIG_PORT_BLOCK = 0x12;
    public static final int MESSAGE_CONFIG_COMMIT = 0x13;
    public static final int MESSAGE_CONFIG_APPLY_RESULT = 0x14;
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
    public static final int QUERY_STATE_PAYLOAD_MIN_LENGTH = 64;
    public static final int QUERY_STATE_PAYLOAD_MAX_LENGTH = 64;
    public static final int QUERY_STATE_MCU_COMMAND_UID_OFFSET = 0;
    public static final int QUERY_STATE_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int QUERY_STATE_SNAPSHOT_UID_OFFSET = 48;
    public static final int SAFE_CLOSE_PAYLOAD_MIN_LENGTH = 54;
    public static final int SAFE_CLOSE_PAYLOAD_MAX_LENGTH = 54;
    public static final int SAFE_CLOSE_MCU_COMMAND_UID_OFFSET = 0;
    public static final int SAFE_CLOSE_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int SAFE_CLOSE_SCOPE_OFFSET = 48;
    public static final int SAFE_CLOSE_PORT_NO_OFFSET = 49;
    public static final int SAFE_CLOSE_EXECUTION_DEADLINE_MS_OFFSET = 50;
    public static final int CONFIG_BEGIN_PAYLOAD_MIN_LENGTH = 139;
    public static final int CONFIG_BEGIN_PAYLOAD_MAX_LENGTH = 139;
    public static final int CONFIG_BEGIN_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_BEGIN_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_BEGIN_APPLICATION_UID_OFFSET = 48;
    public static final int CONFIG_BEGIN_CONFIG_VERSION_OFFSET = 64;
    public static final int CONFIG_BEGIN_CONTENT_SHA256_OFFSET = 72;
    public static final int CONFIG_BEGIN_MCU_PAYLOAD_SHA256_OFFSET = 104;
    public static final int CONFIG_BEGIN_PART_INDEX_OFFSET = 136;
    public static final int CONFIG_BEGIN_PART_COUNT_OFFSET = 137;
    public static final int CONFIG_BEGIN_EXPECTED_PORT_COUNT_OFFSET = 138;
    public static final int CONFIG_DEVICE_BLOCK_PAYLOAD_MIN_LENGTH = 163;
    public static final int CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH = 163;
    public static final int CONFIG_DEVICE_BLOCK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_DEVICE_BLOCK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_DEVICE_BLOCK_APPLICATION_UID_OFFSET = 48;
    public static final int CONFIG_DEVICE_BLOCK_CONFIG_VERSION_OFFSET = 64;
    public static final int CONFIG_DEVICE_BLOCK_CONTENT_SHA256_OFFSET = 72;
    public static final int CONFIG_DEVICE_BLOCK_MCU_PAYLOAD_SHA256_OFFSET = 104;
    public static final int CONFIG_DEVICE_BLOCK_PART_INDEX_OFFSET = 136;
    public static final int CONFIG_DEVICE_BLOCK_PART_COUNT_OFFSET = 137;
    public static final int CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET = 138;
    public static final int CONFIG_DEVICE_BLOCK_NEGATIVE_WEIGHT_THRESHOLD_GRAMS_OFFSET = 142;
    public static final int CONFIG_DEVICE_BLOCK_DELIVERY_AUTO_CLOSE_MS_OFFSET = 146;
    public static final int CONFIG_DEVICE_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET = 150;
    public static final int CONFIG_DEVICE_BLOCK_DELIVERY_DOOR_TRAVEL_WAIT_MS_OFFSET = 154;
    public static final int CONFIG_DEVICE_BLOCK_CLEAN_SOLENOID_PULSE_MS_OFFSET = 158;
    public static final int CONFIG_DEVICE_BLOCK_SMOKE_MONITORING_ENABLED_OFFSET = 162;
    public static final int CONFIG_PORT_BLOCK_PAYLOAD_MIN_LENGTH = 190;
    public static final int CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH = 190;
    public static final int CONFIG_PORT_BLOCK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_PORT_BLOCK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_PORT_BLOCK_APPLICATION_UID_OFFSET = 48;
    public static final int CONFIG_PORT_BLOCK_CONFIG_VERSION_OFFSET = 64;
    public static final int CONFIG_PORT_BLOCK_CONTENT_SHA256_OFFSET = 72;
    public static final int CONFIG_PORT_BLOCK_MCU_PAYLOAD_SHA256_OFFSET = 104;
    public static final int CONFIG_PORT_BLOCK_PART_INDEX_OFFSET = 136;
    public static final int CONFIG_PORT_BLOCK_PART_COUNT_OFFSET = 137;
    public static final int CONFIG_PORT_BLOCK_PORT_NO_OFFSET = 138;
    public static final int CONFIG_PORT_BLOCK_ENABLED_OFFSET = 139;
    public static final int CONFIG_PORT_BLOCK_UNIT_PRICE_TEN_THOUSANDTHS_OFFSET = 140;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_MODE_OFFSET = 144;
    public static final int CONFIG_PORT_BLOCK_CONFIGURED_FULL_WEIGHT_GRAMS_OFFSET = 145;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_SETTLE_WAIT_MS_OFFSET = 149;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_SENSOR_KIND_OFFSET = 153;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_DISTANCE_THRESHOLD_MM_OFFSET = 154;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_SAMPLE_COUNT_OFFSET = 158;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_MINIMUM_VALID_SAMPLE_COUNT_OFFSET = 159;
    public static final int CONFIG_PORT_BLOCK_FULLNESS_ECHO_TIMEOUT_US_OFFSET = 160;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_STABLE_WINDOW_MS_OFFSET = 164;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_FLUCTUATION_GRAMS_OFFSET = 168;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_REQUIRED_SAMPLE_COUNT_OFFSET = 172;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET = 174;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MINIMUM_GRAMS_OFFSET = 178;
    public static final int CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_GRAMS_OFFSET = 182;
    public static final int CONFIG_PORT_BLOCK_CALIBRATION_VERSION_OFFSET = 186;
    public static final int CONFIG_COMMIT_PAYLOAD_MIN_LENGTH = 138;
    public static final int CONFIG_COMMIT_PAYLOAD_MAX_LENGTH = 138;
    public static final int CONFIG_COMMIT_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIG_COMMIT_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIG_COMMIT_APPLICATION_UID_OFFSET = 48;
    public static final int CONFIG_COMMIT_CONFIG_VERSION_OFFSET = 64;
    public static final int CONFIG_COMMIT_CONTENT_SHA256_OFFSET = 72;
    public static final int CONFIG_COMMIT_MCU_PAYLOAD_SHA256_OFFSET = 104;
    public static final int CONFIG_COMMIT_PART_INDEX_OFFSET = 136;
    public static final int CONFIG_COMMIT_PART_COUNT_OFFSET = 137;
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
    public static final int START_DELIVERY_SESSION_PAYLOAD_MIN_LENGTH = 125;
    public static final int START_DELIVERY_SESSION_PAYLOAD_MAX_LENGTH = 125;
    public static final int START_DELIVERY_SESSION_MCU_COMMAND_UID_OFFSET = 0;
    public static final int START_DELIVERY_SESSION_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int START_DELIVERY_SESSION_SESSION_UID_OFFSET = 48;
    public static final int START_DELIVERY_SESSION_PORT_NO_OFFSET = 64;
    public static final int START_DELIVERY_SESSION_CONFIG_VERSION_OFFSET = 65;
    public static final int START_DELIVERY_SESSION_CONFIG_CONTENT_SHA256_OFFSET = 73;
    public static final int START_DELIVERY_SESSION_UNIT_PRICE_TEN_THOUSANDTHS_OFFSET = 105;
    public static final int START_DELIVERY_SESSION_CONTINUE_DELIVERY_WAIT_MS_OFFSET = 109;
    public static final int START_DELIVERY_SESSION_NEGATIVE_WEIGHT_THRESHOLD_GRAMS_OFFSET = 113;
    public static final int START_DELIVERY_SESSION_START_EXECUTION_WINDOW_MS_OFFSET = 117;
    public static final int START_DELIVERY_SESSION_DELIVERY_AUTO_CLOSE_MS_OFFSET = 121;
    public static final int START_CLEAN_OPERATION_PAYLOAD_MIN_LENGTH = 113;
    public static final int START_CLEAN_OPERATION_PAYLOAD_MAX_LENGTH = 113;
    public static final int START_CLEAN_OPERATION_MCU_COMMAND_UID_OFFSET = 0;
    public static final int START_CLEAN_OPERATION_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int START_CLEAN_OPERATION_OPERATION_UID_OFFSET = 48;
    public static final int START_CLEAN_OPERATION_PORT_NO_OFFSET = 64;
    public static final int START_CLEAN_OPERATION_CONFIG_VERSION_OFFSET = 65;
    public static final int START_CLEAN_OPERATION_CONFIG_CONTENT_SHA256_OFFSET = 73;
    public static final int START_CLEAN_OPERATION_START_EXECUTION_WINDOW_MS_OFFSET = 105;
    public static final int START_CLEAN_OPERATION_OPERATION_WINDOW_MS_OFFSET = 109;
    public static final int UNLOCK_CLEAN_DOOR_PAYLOAD_MIN_LENGTH = 95;
    public static final int UNLOCK_CLEAN_DOOR_PAYLOAD_MAX_LENGTH = 95;
    public static final int UNLOCK_CLEAN_DOOR_MCU_COMMAND_UID_OFFSET = 0;
    public static final int UNLOCK_CLEAN_DOOR_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int UNLOCK_CLEAN_DOOR_OPERATION_UID_OFFSET = 48;
    public static final int UNLOCK_CLEAN_DOOR_PORT_NO_OFFSET = 64;
    public static final int UNLOCK_CLEAN_DOOR_CLEAN_ACTION_SEQUENCE_OFFSET = 65;
    public static final int UNLOCK_CLEAN_DOOR_RECOVERY_GENERATION_OFFSET = 67;
    public static final int UNLOCK_CLEAN_DOOR_UNLOCK_PULSE_MS_OFFSET = 71;
    public static final int UNLOCK_CLEAN_DOOR_REMAINING_OPERATION_WINDOW_MS_OFFSET = 75;
    public static final int UNLOCK_CLEAN_DOOR_PARENT_COMMAND_UID_OFFSET = 79;
    public static final int RESUME_CLEAN_OPERATION_PAYLOAD_MIN_LENGTH = 111;
    public static final int RESUME_CLEAN_OPERATION_PAYLOAD_MAX_LENGTH = 111;
    public static final int RESUME_CLEAN_OPERATION_MCU_COMMAND_UID_OFFSET = 0;
    public static final int RESUME_CLEAN_OPERATION_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int RESUME_CLEAN_OPERATION_OPERATION_UID_OFFSET = 48;
    public static final int RESUME_CLEAN_OPERATION_PORT_NO_OFFSET = 64;
    public static final int RESUME_CLEAN_OPERATION_RECOVERY_GENERATION_OFFSET = 65;
    public static final int RESUME_CLEAN_OPERATION_NEXT_CLEAN_ACTION_SEQUENCE_OFFSET = 69;
    public static final int RESUME_CLEAN_OPERATION_CONFIG_VERSION_OFFSET = 71;
    public static final int RESUME_CLEAN_OPERATION_CONFIG_CONTENT_SHA256_OFFSET = 79;
    public static final int END_CLEAN_BEFORE_UNLOCK_PAYLOAD_MIN_LENGTH = 90;
    public static final int END_CLEAN_BEFORE_UNLOCK_PAYLOAD_MAX_LENGTH = 90;
    public static final int END_CLEAN_BEFORE_UNLOCK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int END_CLEAN_BEFORE_UNLOCK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int END_CLEAN_BEFORE_UNLOCK_OPERATION_UID_OFFSET = 48;
    public static final int END_CLEAN_BEFORE_UNLOCK_PORT_NO_OFFSET = 64;
    public static final int END_CLEAN_BEFORE_UNLOCK_PARENT_COMMAND_UID_OFFSET = 65;
    public static final int END_CLEAN_BEFORE_UNLOCK_RECOVERY_GENERATION_OFFSET = 81;
    public static final int END_CLEAN_BEFORE_UNLOCK_EXECUTION_DEADLINE_MS_OFFSET = 85;
    public static final int END_CLEAN_BEFORE_UNLOCK_REASON_OFFSET = 89;
    public static final int SAMPLE_FULLNESS_PAYLOAD_MIN_LENGTH = 118;
    public static final int SAMPLE_FULLNESS_PAYLOAD_MAX_LENGTH = 118;
    public static final int SAMPLE_FULLNESS_MCU_COMMAND_UID_OFFSET = 0;
    public static final int SAMPLE_FULLNESS_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int SAMPLE_FULLNESS_DETECTION_UID_OFFSET = 48;
    public static final int SAMPLE_FULLNESS_PORT_NO_OFFSET = 64;
    public static final int SAMPLE_FULLNESS_SAMPLE_ROLE_OFFSET = 65;
    public static final int SAMPLE_FULLNESS_CONFIG_VERSION_OFFSET = 66;
    public static final int SAMPLE_FULLNESS_CONFIG_CONTENT_SHA256_OFFSET = 74;
    public static final int SAMPLE_FULLNESS_START_EXECUTION_WINDOW_MS_OFFSET = 106;
    public static final int SAMPLE_FULLNESS_SETTLE_WAIT_MS_OFFSET = 110;
    public static final int SAMPLE_FULLNESS_MEASUREMENT_TIMEOUT_MS_OFFSET = 114;
    public static final int MEASURE_BASELINE_PAYLOAD_MIN_LENGTH = 113;
    public static final int MEASURE_BASELINE_PAYLOAD_MAX_LENGTH = 113;
    public static final int MEASURE_BASELINE_MCU_COMMAND_UID_OFFSET = 0;
    public static final int MEASURE_BASELINE_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int MEASURE_BASELINE_MEASUREMENT_UID_OFFSET = 48;
    public static final int MEASURE_BASELINE_PORT_NO_OFFSET = 64;
    public static final int MEASURE_BASELINE_CONFIG_VERSION_OFFSET = 65;
    public static final int MEASURE_BASELINE_CONFIG_CONTENT_SHA256_OFFSET = 73;
    public static final int MEASURE_BASELINE_START_EXECUTION_WINDOW_MS_OFFSET = 105;
    public static final int MEASURE_BASELINE_MEASUREMENT_TIMEOUT_MS_OFFSET = 109;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PAYLOAD_MIN_LENGTH = 101;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PAYLOAD_MAX_LENGTH = 101;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_MCU_COMMAND_UID_OFFSET = 0;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_SESSION_UID_OFFSET = 48;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PORT_NO_OFFSET = 64;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_FIRST_PRE_OPEN_MEASUREMENT_UID_OFFSET = 65;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_PARENT_START_COMMAND_UID_OFFSET = 81;
    public static final int AUTHORIZE_DELIVERY_FIRST_OPEN_REMAINING_START_AUTHORIZATION_MS_OFFSET = 97;
    public static final int CONFIRM_NO_ACTIVE_WORK_PAYLOAD_MIN_LENGTH = 88;
    public static final int CONFIRM_NO_ACTIVE_WORK_PAYLOAD_MAX_LENGTH = 88;
    public static final int CONFIRM_NO_ACTIVE_WORK_MCU_COMMAND_UID_OFFSET = 0;
    public static final int CONFIRM_NO_ACTIVE_WORK_COMMAND_DIGEST_SHA256_OFFSET = 16;
    public static final int CONFIRM_NO_ACTIVE_WORK_CONFIG_VERSION_OFFSET = 48;
    public static final int CONFIRM_NO_ACTIVE_WORK_CONFIG_CONTENT_SHA256_OFFSET = 56;
    public static final int WORK_PREOPEN_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 91;
    public static final int WORK_PREOPEN_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 91;
    public static final int WORK_PREOPEN_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int WORK_PREOPEN_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int WORK_PREOPEN_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int WORK_PREOPEN_WEIGHT_READY_MCU_COMMAND_UID_OFFSET = 20;
    public static final int WORK_PREOPEN_WEIGHT_READY_SESSION_UID_OFFSET = 36;
    public static final int WORK_PREOPEN_WEIGHT_READY_PORT_NO_OFFSET = 52;
    public static final int WORK_PREOPEN_WEIGHT_READY_ROUND_INDEX_OFFSET = 53;
    public static final int WORK_PREOPEN_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 55;
    public static final int WORK_PREOPEN_WEIGHT_READY_MEASUREMENT_STATUS_OFFSET = 71;
    public static final int WORK_PREOPEN_WEIGHT_READY_WEIGHT_VALUE_PRESENT_OFFSET = 72;
    public static final int WORK_PREOPEN_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 73;
    public static final int WORK_PREOPEN_WEIGHT_READY_WEIGHT_VALUE_KIND_OFFSET = 77;
    public static final int WORK_PREOPEN_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 78;
    public static final int WORK_PREOPEN_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 82;
    public static final int WORK_PREOPEN_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 84;
    public static final int WORK_PREOPEN_WEIGHT_READY_WEIGHT_SENSOR_HEALTH_OFFSET = 88;
    public static final int WORK_PREOPEN_WEIGHT_READY_FAULT_CODE_OFFSET = 89;
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
    public static final int WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 91;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 91;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MCU_COMMAND_UID_OFFSET = 20;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_SESSION_UID_OFFSET = 36;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_PORT_NO_OFFSET = 52;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_ROUND_INDEX_OFFSET = 53;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 55;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_STATUS_OFFSET = 71;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_WEIGHT_VALUE_PRESENT_OFFSET = 72;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 73;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_WEIGHT_VALUE_KIND_OFFSET = 77;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 78;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 82;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 84;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_WEIGHT_SENSOR_HEALTH_OFFSET = 88;
    public static final int WORK_POSTCLOSE_WEIGHT_READY_FAULT_CODE_OFFSET = 89;
    public static final int DELIVERY_SELECTION_PAYLOAD_MIN_LENGTH = 56;
    public static final int DELIVERY_SELECTION_PAYLOAD_MAX_LENGTH = 56;
    public static final int DELIVERY_SELECTION_MCU_BOOT_ID_OFFSET = 0;
    public static final int DELIVERY_SELECTION_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int DELIVERY_SELECTION_UPTIME_MS_OFFSET = 12;
    public static final int DELIVERY_SELECTION_SESSION_UID_OFFSET = 20;
    public static final int DELIVERY_SELECTION_PORT_NO_OFFSET = 36;
    public static final int DELIVERY_SELECTION_ROUND_INDEX_OFFSET = 37;
    public static final int DELIVERY_SELECTION_POST_CLOSE_MEASUREMENT_UID_OFFSET = 39;
    public static final int DELIVERY_SELECTION_SELECTION_OFFSET = 55;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 89;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 89;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MCU_COMMAND_UID_OFFSET = 20;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_OPERATION_UID_OFFSET = 36;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_PORT_NO_OFFSET = 52;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 53;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MEASUREMENT_STATUS_OFFSET = 69;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_WEIGHT_VALUE_PRESENT_OFFSET = 70;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 71;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_WEIGHT_VALUE_KIND_OFFSET = 75;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 76;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 80;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 82;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_WEIGHT_SENSOR_HEALTH_OFFSET = 86;
    public static final int WORK_PREUNLOCK_WEIGHT_READY_FAULT_CODE_OFFSET = 87;
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
    public static final int CLEAN_UNLOCK_REQUESTED_PAYLOAD_MIN_LENGTH = 39;
    public static final int CLEAN_UNLOCK_REQUESTED_PAYLOAD_MAX_LENGTH = 39;
    public static final int CLEAN_UNLOCK_REQUESTED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_UNLOCK_REQUESTED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_UNLOCK_REQUESTED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_UNLOCK_REQUESTED_OPERATION_UID_OFFSET = 20;
    public static final int CLEAN_UNLOCK_REQUESTED_PORT_NO_OFFSET = 36;
    public static final int CLEAN_UNLOCK_REQUESTED_CLEAN_ACTION_SEQUENCE_OFFSET = 37;
    public static final int CLEAN_FINISH_REQUESTED_PAYLOAD_MIN_LENGTH = 39;
    public static final int CLEAN_FINISH_REQUESTED_PAYLOAD_MAX_LENGTH = 39;
    public static final int CLEAN_FINISH_REQUESTED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_FINISH_REQUESTED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_FINISH_REQUESTED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_FINISH_REQUESTED_OPERATION_UID_OFFSET = 20;
    public static final int CLEAN_FINISH_REQUESTED_PORT_NO_OFFSET = 36;
    public static final int CLEAN_FINISH_REQUESTED_CLEAN_ACTION_SEQUENCE_OFFSET = 37;
    public static final int CLEAN_FINAL_WEIGHT_READY_PAYLOAD_MIN_LENGTH = 75;
    public static final int CLEAN_FINAL_WEIGHT_READY_PAYLOAD_MAX_LENGTH = 75;
    public static final int CLEAN_FINAL_WEIGHT_READY_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_FINAL_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_FINAL_WEIGHT_READY_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_FINAL_WEIGHT_READY_OPERATION_UID_OFFSET = 20;
    public static final int CLEAN_FINAL_WEIGHT_READY_PORT_NO_OFFSET = 36;
    public static final int CLEAN_FINAL_WEIGHT_READY_CLEAN_ACTION_SEQUENCE_OFFSET = 37;
    public static final int CLEAN_FINAL_WEIGHT_READY_MEASUREMENT_UID_OFFSET = 39;
    public static final int CLEAN_FINAL_WEIGHT_READY_MEASUREMENT_STATUS_OFFSET = 55;
    public static final int CLEAN_FINAL_WEIGHT_READY_WEIGHT_VALUE_PRESENT_OFFSET = 56;
    public static final int CLEAN_FINAL_WEIGHT_READY_REPORTED_WEIGHT_GRAMS_OFFSET = 57;
    public static final int CLEAN_FINAL_WEIGHT_READY_WEIGHT_VALUE_KIND_OFFSET = 61;
    public static final int CLEAN_FINAL_WEIGHT_READY_MEASUREMENT_ELAPSED_MS_OFFSET = 62;
    public static final int CLEAN_FINAL_WEIGHT_READY_SAMPLE_COUNT_OFFSET = 66;
    public static final int CLEAN_FINAL_WEIGHT_READY_CALIBRATION_VERSION_OFFSET = 68;
    public static final int CLEAN_FINAL_WEIGHT_READY_WEIGHT_SENSOR_HEALTH_OFFSET = 72;
    public static final int CLEAN_FINAL_WEIGHT_READY_FAULT_CODE_OFFSET = 73;
    public static final int FULLNESS_SAMPLE_RESULT_PAYLOAD_MIN_LENGTH = 100;
    public static final int FULLNESS_SAMPLE_RESULT_PAYLOAD_MAX_LENGTH = 100;
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
    public static final int FULLNESS_SAMPLE_RESULT_MEASUREMENT_STATUS_OFFSET = 80;
    public static final int FULLNESS_SAMPLE_RESULT_WEIGHT_VALUE_PRESENT_OFFSET = 81;
    public static final int FULLNESS_SAMPLE_RESULT_REPORTED_WEIGHT_GRAMS_OFFSET = 82;
    public static final int FULLNESS_SAMPLE_RESULT_WEIGHT_VALUE_KIND_OFFSET = 86;
    public static final int FULLNESS_SAMPLE_RESULT_MEASUREMENT_ELAPSED_MS_OFFSET = 87;
    public static final int FULLNESS_SAMPLE_RESULT_SAMPLE_COUNT_OFFSET = 91;
    public static final int FULLNESS_SAMPLE_RESULT_CALIBRATION_VERSION_OFFSET = 93;
    public static final int FULLNESS_SAMPLE_RESULT_WEIGHT_SENSOR_HEALTH_OFFSET = 97;
    public static final int FULLNESS_SAMPLE_RESULT_FAULT_CODE_OFFSET = 98;
    public static final int BASELINE_MEASUREMENT_RESULT_PAYLOAD_MIN_LENGTH = 89;
    public static final int BASELINE_MEASUREMENT_RESULT_PAYLOAD_MAX_LENGTH = 89;
    public static final int BASELINE_MEASUREMENT_RESULT_MCU_BOOT_ID_OFFSET = 0;
    public static final int BASELINE_MEASUREMENT_RESULT_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int BASELINE_MEASUREMENT_RESULT_UPTIME_MS_OFFSET = 12;
    public static final int BASELINE_MEASUREMENT_RESULT_MCU_COMMAND_UID_OFFSET = 20;
    public static final int BASELINE_MEASUREMENT_RESULT_MEASUREMENT_UID_OFFSET = 36;
    public static final int BASELINE_MEASUREMENT_RESULT_PORT_NO_OFFSET = 52;
    public static final int BASELINE_MEASUREMENT_RESULT_WEIGHT_MEASUREMENT_UID_OFFSET = 53;
    public static final int BASELINE_MEASUREMENT_RESULT_MEASUREMENT_STATUS_OFFSET = 69;
    public static final int BASELINE_MEASUREMENT_RESULT_WEIGHT_VALUE_PRESENT_OFFSET = 70;
    public static final int BASELINE_MEASUREMENT_RESULT_REPORTED_WEIGHT_GRAMS_OFFSET = 71;
    public static final int BASELINE_MEASUREMENT_RESULT_WEIGHT_VALUE_KIND_OFFSET = 75;
    public static final int BASELINE_MEASUREMENT_RESULT_MEASUREMENT_ELAPSED_MS_OFFSET = 76;
    public static final int BASELINE_MEASUREMENT_RESULT_SAMPLE_COUNT_OFFSET = 80;
    public static final int BASELINE_MEASUREMENT_RESULT_CALIBRATION_VERSION_OFFSET = 82;
    public static final int BASELINE_MEASUREMENT_RESULT_WEIGHT_SENSOR_HEALTH_OFFSET = 86;
    public static final int BASELINE_MEASUREMENT_RESULT_FAULT_CODE_OFFSET = 87;
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
    public static final int CLEAN_COMPLETION_CONFIRMED_PAYLOAD_MIN_LENGTH = 59;
    public static final int CLEAN_COMPLETION_CONFIRMED_PAYLOAD_MAX_LENGTH = 59;
    public static final int CLEAN_COMPLETION_CONFIRMED_MCU_BOOT_ID_OFFSET = 0;
    public static final int CLEAN_COMPLETION_CONFIRMED_MCU_EVENT_SEQUENCE_OFFSET = 8;
    public static final int CLEAN_COMPLETION_CONFIRMED_UPTIME_MS_OFFSET = 12;
    public static final int CLEAN_COMPLETION_CONFIRMED_OPERATION_UID_OFFSET = 20;
    public static final int CLEAN_COMPLETION_CONFIRMED_PORT_NO_OFFSET = 36;
    public static final int CLEAN_COMPLETION_CONFIRMED_CLEAN_ACTION_SEQUENCE_OFFSET = 37;
    public static final int CLEAN_COMPLETION_CONFIRMED_FINAL_MEASUREMENT_UID_OFFSET = 39;
    public static final int CLEAN_COMPLETION_CONFIRMED_LOCK_POWER_STATE_OFFSET = 55;
    public static final int CLEAN_COMPLETION_CONFIRMED_SOLENOID_HEALTH_OFFSET = 56;
    public static final int CLEAN_COMPLETION_CONFIRMED_CLEAN_DOOR_STATE_BASIS_OFFSET = 57;
    public static final int CLEAN_COMPLETION_CONFIRMED_CLEANER_PHYSICAL_CLOSE_CONFIRMED_OFFSET = 58;
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

    public static Boolean ackRequired(int messageType) {
        return switch (messageType) {
            case MESSAGE_HELLO -> false;
            case MESSAGE_HELLO_ACK -> false;
            case MESSAGE_ACK -> false;
            case MESSAGE_NACK -> false;
            case MESSAGE_QUERY_STATE -> true;
            case MESSAGE_SAFE_CLOSE -> true;
            case MESSAGE_CONFIG_BEGIN -> true;
            case MESSAGE_CONFIG_DEVICE_BLOCK -> true;
            case MESSAGE_CONFIG_PORT_BLOCK -> true;
            case MESSAGE_CONFIG_COMMIT -> true;
            case MESSAGE_CONFIG_APPLY_RESULT -> true;
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
            case MESSAGE_HELLO -> Direction.BIDIRECTIONAL;
            case MESSAGE_HELLO_ACK -> Direction.BIDIRECTIONAL;
            case MESSAGE_ACK -> Direction.BIDIRECTIONAL;
            case MESSAGE_NACK -> Direction.BIDIRECTIONAL;
            case MESSAGE_QUERY_STATE -> Direction.EDGE_TO_MCU;
            case MESSAGE_SAFE_CLOSE -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_BEGIN -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_DEVICE_BLOCK -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_PORT_BLOCK -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_COMMIT -> Direction.EDGE_TO_MCU;
            case MESSAGE_CONFIG_APPLY_RESULT -> Direction.MCU_TO_EDGE;
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
            byte[] combined = Arrays.copyOf(buffer, buffer.length + data.length);
            System.arraycopy(data, 0, combined, buffer.length, data.length);
            buffer = combined;
            if (buffer.length > 512) {
                buffer = Arrays.copyOfRange(buffer, buffer.length - 512, buffer.length);
                candidateStartedMs = null;
                diagnostics.add("BUFFER_OVERFLOW");
            }
            List<Frame> frames = new ArrayList<>();
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
            return frames;
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
