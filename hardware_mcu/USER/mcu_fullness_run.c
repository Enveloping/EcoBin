#include "mcu_fullness_run.h"
#include "actuator_runtime.h"
#include <string.h>

typedef char fullness_run_ram_budget[(sizeof(McuFullnessRun) <= 256u) ? 1 : -1];

void McuFullnessRun_Init(McuFullnessRun *run) { memset(run, 0, sizeof(*run)); }

uint32_t McuFullnessRun_Begin(McuFullnessRun *run, McuDeviceFacts *facts, const McuConfiguration *configuration) {
    McuConfigFullnessPolicy policy;
    uint32_t sequence;
    if (run == NULL || run->present || run->result.sequence == UINT32_MAX
        || !McuEnvironmentMonitor_ReadUltrasonicPolicy(facts, configuration, &policy)
        || !UltrasonicReader_Claim(run)) return 0u;
    sequence = run->result.sequence + 1u;
    memset(&run->result, 0, sizeof(run->result));
    run->result.sequence = sequence;
    run->result.config_version = policy.config_version;
    run->result.started_ms = ActuatorRuntime_Snapshot().captured_uptime_ms;
    memcpy(run->result.content_sha256, facts->content_sha256, 32u);
    memcpy(run->result.mcu_sha256, facts->mcu_sha256, 32u);
    run->result.port_no = policy.port_no;
    run->result.sensor_kind = policy.sensor_kind;
    run->result.requested_count = policy.sample_count;
    run->policy = policy;
    run->facts = facts;
    run->configuration = configuration;
    run->attempt_sequence = 0u;
    run->present = 1u;
    return sequence;
}

static uint8_t consume(McuFullnessRun *run) {
    UltrasonicObservation observation;
    uint16_t distance;
    uint8_t index;
    if (!run->attempt_sequence || !UltrasonicReader_CopyOwned(run, &observation)
        || observation.attempt_sequence != run->attempt_sequence
        || !McuEnvironmentMonitor_PublishUltrasonic(run->facts, &observation)) return 0u;
    if (!UltrasonicReader_RetireOwned(run, observation.attempt_sequence)) return 0u;
    run->attempt_sequence = 0u;
    run->result.last_captured_ms = observation.captured_ms;
    ++run->result.completed_count;
    if (observation.status == ULTRASONIC_VALID) {
        distance = (uint16_t)(observation.pulse_us * 10u / 58u);
        index = run->result.valid_count++;
        while (index && run->distances[index - 1u] > distance) {
            run->distances[index] = run->distances[index - 1u]; --index;
        }
        run->distances[index] = distance;
    }
    return 1u;
}

static uint8_t same_configuration(const McuFullnessRun *run) {
    McuConfigFullnessPolicy policy;
    return McuEnvironmentMonitor_ReadUltrasonicPolicy(run->facts, run->configuration, &policy)
        && policy.config_version == run->result.config_version
        && memcmp(run->facts->content_sha256, run->result.content_sha256, 32u) == 0
        && memcmp(run->facts->mcu_sha256, run->result.mcu_sha256, 32u) == 0;
}

uint8_t McuFullnessRun_Poll(McuFullnessRun *run) {
    uint64_t now;
    uint8_t count;
    if (run == NULL || !run->present) return 0u;
    if (run->result.status) return 1u;
    now = ActuatorRuntime_Snapshot().captured_uptime_ms;
    if (!run->result.stop_reason && !same_configuration(run)) run->result.stop_reason = MCU_FULLNESS_STOP_CONFIGURATION;
    consume(run);
    if (run->result.stop_reason) {
        /* If an IRQ completed between consume and cancel, cancellation refuses
         * to discard it; the next foreground poll publishes that exact sample.
         * A rejected raw-facts publication also keeps the real observation. */
        if (!UltrasonicReader_CancelOwned(run) || !UltrasonicReader_Release(run)) return 0u;
        run->attempt_sequence = 0u;
        run->result.completed_ms = ActuatorRuntime_Snapshot().captured_uptime_ms;
        run->result.status = MCU_FULLNESS_RUN_INTERRUPTED;
        return 1u;
    }
    if (run->result.completed_count == run->result.requested_count) {
        count = run->result.valid_count;
        run->result.sensor_value = ECOBIN_UART_FULLNESS_SENSOR_VALUE_CLEAR;
        if (count >= run->policy.minimum_valid_count) {
            run->result.distance_mm = run->distances[count / 2u];
            /* Even groups: average the middle two integer-mm observations,
             * nearest mm, half up. The transmitted representative is compared
             * strictly below the configured threshold; equality is CLEAR. */
            if (!(count & 1u)) run->result.distance_mm = (run->result.distance_mm + run->distances[count / 2u - 1u] + 1u) / 2u;
            run->result.distance_present = 1u;
            run->result.basis = ECOBIN_UART_FULLNESS_SAMPLE_BASIS_MEASURED_MEDIAN;
            if (run->result.distance_mm < run->policy.distance_threshold_mm)
                run->result.sensor_value = ECOBIN_UART_FULLNESS_SENSOR_VALUE_BLOCKED;
        } else run->result.basis = count == 0u ? ECOBIN_UART_FULLNESS_SAMPLE_BASIS_NO_ECHO_CLEAR_FALLBACK
            : ECOBIN_UART_FULLNESS_SAMPLE_BASIS_INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK;
        if (!UltrasonicReader_Release(run)) return 0u;
        run->result.completed_ms = ActuatorRuntime_Snapshot().captured_uptime_ms;
        run->result.status = MCU_FULLNESS_RUN_COMPLETE;
        return 1u;
    }
    /* The board's capture clock advances in 10-ms ticks. A long echo timeout
     * followed immediately by a short pulse could otherwise produce different
     * real observations with the same timestamp, rejected by immutable facts.
     * Wait for a NEW actual clock tick; never relabel/freshen either sample. */
    if (!run->attempt_sequence && now - run->result.started_ms >= run->policy.settle_wait_ms
        && (!run->facts->fullness_status || now > run->facts->fullness_captured_ms))
        run->attempt_sequence = UltrasonicReader_BeginOwned(run, run->policy.echo_timeout_us);
    return 0u;
}

uint8_t McuFullnessRun_Copy(const McuFullnessRun *run, McuFullnessResult *output) {
    if (run == NULL || output == NULL || !run->present || !run->result.status) return 0u;
    *output = run->result;
    return 1u;
}

uint8_t McuFullnessRun_Interrupt(McuFullnessRun *run) {
    if (run == NULL || !run->present) return 0u;
    if (!run->result.status && !run->result.stop_reason) run->result.stop_reason = MCU_FULLNESS_STOP_CALLER;
    return McuFullnessRun_Poll(run);
}

uint8_t McuFullnessRun_Retire(McuFullnessRun *run, uint32_t sequence) {
    if (run == NULL || !run->present || !run->result.status || run->result.sequence != sequence) return 0u;
    run->present = 0u;
    return 1u;
}
