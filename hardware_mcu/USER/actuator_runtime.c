#include "actuator_runtime.h"
#include "clean_lock.h"
#include "runtime_clock.h"

static ActuatorHardware io;
static volatile DoorControl door;
static volatile CleanLock lock;
static volatile uint8_t update_latched;
static volatile DoorControlSnapshot applied_door;
static volatile uint8_t applied_pinch;
static volatile uint64_t applied_uptime_ms;
static volatile ActuatorDeliveryCycle delivery;
static volatile uint32_t delivery_token;
static volatile uint32_t delivery_auto_close_ms;
static volatile uint64_t delivery_due_ms;
static volatile uint8_t delivery_phase;
static volatile uint8_t native_mode;
static volatile ActuatorCleanPulse clean_pulse;
static volatile uint32_t clean_token;
static volatile ActuatorRecoveryClose recovery_close;
static volatile uint32_t recovery_token;

static void update_recovery_close(uint64_t now)
{
    if(!recovery_close.present || recovery_close.completed || update_latched) return;
    if(now >= recovery_close.deadline_ms)
        recovery_close.rejected = 1U;
    else if(now >= recovery_close.due_ms)
        DoorControl_SetTarget(&door, MCU_DIRECTION_CLOSE);
    else return;
    recovery_close.completed = 1U;
    recovery_close.terminal_at_ms = now;
}

static void update_delivery(uint64_t now)
{
    if(!delivery.present || update_latched) return;
    if(delivery_phase == 1U)
    {
        if(now >= delivery.execution_deadline_ms)
        {
            delivery.expired = 1U;
            delivery.terminal_at_ms = now;
            delivery_phase = 4U;
        }
        else if(now >= delivery_due_ms)
        {
            DoorControl_SetTarget(&door, MCU_DIRECTION_OPEN);
            delivery.opened = 1U;
            delivery.opened_at_ms = now;
            delivery_due_ms = now + delivery_auto_close_ms;
            delivery_phase = 2U;
        }
    }
    else if(delivery_phase == 2U && now >= delivery_due_ms)
    {
        DoorControl_Stop(&door);
        delivery_due_ms = now + 100U;
        delivery_phase = 3U;
    }
    else if(delivery_phase == 3U && now >= delivery_due_ms)
    {
        DoorControl_SetTarget(&door, MCU_DIRECTION_CLOSE);
        delivery.closed = 1U;
        delivery.closed_at_ms = now;
        delivery.terminal_at_ms = now;
        delivery_phase = 4U;
    }
}

static void refresh(void)
{
    DoorControlSnapshot state;
    uint8_t mask = 0U;
    uint8_t pinch = io.read_pinch() != 0U;
    update_delivery(RuntimeClock_Now64Locked());
    update_recovery_close(RuntimeClock_Now64Locked());
    CleanLock_Update(&lock, RuntimeClock_Now());
    if(clean_pulse.present && !clean_pulse.completed && !CleanLock_IsPowered(&lock))
    {
        clean_pulse.off_at_ms = RuntimeClock_Now64Locked();
        clean_pulse.completed = 1U;
    }
    state = DoorControl_Read(&door, pinch);
    if(!update_latched)
    {
        if(state.pb6) mask |= ACTUATOR_PB6;
        if(state.pb7) mask |= ACTUATOR_PB7;
        if(CleanLock_IsPowered(&lock)) mask |= ACTUATOR_PB8;
    }
    io.write_outputs(mask);
    applied_door = state;
    applied_pinch = pinch;
    applied_uptime_ms = RuntimeClock_Now64Locked();
}

void ActuatorRuntime_Init(const ActuatorHardware *hardware)
{
    ActuatorDeliveryCycle empty = {0};
    ActuatorCleanPulse empty_clean = {0};
    ActuatorRecoveryClose empty_close = {0};
    io = *hardware;
    DoorControl_Init(&door);
    CleanLock_Init(&lock);
    update_latched = 0U;
    delivery = empty;
    delivery_token = delivery_auto_close_ms = 0U;
    delivery_due_ms = 0U;
    delivery_phase = 0U;
    native_mode = 0U;
    clean_pulse = empty_clean;
    clean_token = 0U;
    recovery_close = empty_close;
    recovery_token = 0U;
    refresh();
}

uint8_t ActuatorRuntime_SetDoorTarget(uint8_t target)
{
    uint32_t previous = io.enter_critical();
    uint8_t accepted = 0U;
    if(!update_latched && !native_mode)
        accepted = DoorControl_SetTarget(&door, target);
    if(!native_mode) refresh();
    io.leave_critical(previous);
    return accepted;
}

uint8_t ActuatorRuntime_Unlock(uint32_t duration_ms)
{
    uint32_t previous = io.enter_critical();
    uint8_t accepted = 0U;
    if(!update_latched && !native_mode)
        accepted = CleanLock_Start(&lock, RuntimeClock_Now(), duration_ms);
    if(!native_mode) refresh();
    io.leave_critical(previous);
    return accepted;
}

void ActuatorRuntime_StopForUpdate(void)
{
    uint32_t previous = io.enter_critical();
    if(recovery_close.present && !recovery_close.completed)
    {
        recovery_close.rejected = recovery_close.completed = 1U;
        recovery_close.terminal_at_ms = RuntimeClock_Now64Locked();
    }
    if(delivery.present && delivery_phase != 4U)
    {
        delivery.interrupted = 1U;
        delivery.terminal_at_ms = RuntimeClock_Now64Locked();
        delivery_phase = 4U;
    }
    if(clean_pulse.present && !clean_pulse.completed)
    {
        clean_pulse.interrupted = 1U;
        clean_pulse.off_at_ms = RuntimeClock_Now64Locked();
        clean_pulse.completed = 1U;
    }
    update_latched = 1U;
    DoorControl_Stop(&door);
    CleanLock_Stop(&lock);
    refresh(); /* F2 returns only after the hardware outputs have been cleared. */
    io.leave_critical(previous);
}

uint32_t ActuatorRuntime_BeginDeliveryCycle(uint64_t execute_before_ms, uint32_t auto_close_ms)
{
    uint32_t previous = io.enter_critical();
    uint64_t now = RuntimeClock_Now64Locked();
    ActuatorDeliveryCycle next = {0};
    uint32_t token = 0U;
    if(!update_latched && !delivery.present && !clean_pulse.present && !recovery_close.present && !CleanLock_IsPowered(&lock)
        && delivery_token != UINT32_MAX && auto_close_ms != 0U
        && now <= UINT64_MAX - auto_close_ms - 200U
        && execute_before_ms > now + 100U)
    {
        token = ++delivery_token;
        native_mode = 1U;
        next.token = token;
        next.present = 1U;
        next.requested_at_ms = now;
        next.execution_deadline_ms = execute_before_ms;
        delivery = next;
        delivery_auto_close_ms = auto_close_ms;
        delivery_due_ms = now + 100U;
        delivery_phase = 1U;
        DoorControl_Stop(&door);
        refresh();
    }
    io.leave_critical(previous);
    return token;
}

ActuatorDeliveryCycle ActuatorRuntime_DeliveryCycle(void)
{
    uint32_t previous = io.enter_critical();
    ActuatorDeliveryCycle copy = delivery;
    io.leave_critical(previous);
    return copy;
}

uint8_t ActuatorRuntime_ReleaseDeliveryCycle(uint32_t token)
{
    uint32_t previous = io.enter_critical();
    uint8_t released = 0U;
    if(delivery.present && delivery.token == token && delivery_phase == 4U)
    {
        delivery.present = 0U;
        released = 1U;
    }
    io.leave_critical(previous);
    return released;
}

uint32_t ActuatorRuntime_BeginCleanPulse(uint64_t execute_before_ms, uint32_t duration_ms)
{
    uint32_t previous = io.enter_critical();
    uint64_t now = RuntimeClock_Now64Locked();
    ActuatorCleanPulse next = {0};
    DoorControlSnapshot state = DoorControl_Read(&door, io.read_pinch());
    uint32_t token = 0U;
    if(!update_latched && !delivery.present && !clean_pulse.present && !recovery_close.present && !CleanLock_IsPowered(&lock)
        && state.target_valid && state.action_active && state.target == MCU_DIRECTION_CLOSE
        && clean_token != UINT32_MAX && duration_ms != 0U && duration_ms < 0x80000000UL
        && now <= UINT64_MAX - duration_ms && now < execute_before_ms)
    {
        token = ++clean_token;
        native_mode = 1U;
        next.token = token;
        next.present = 1U;
        next.powered_at_ms = now;
        next.off_deadline_ms = now + duration_ms;
        clean_pulse = next;
        CleanLock_Start(&lock, RuntimeClock_Now(), duration_ms);
        refresh();
    }
    io.leave_critical(previous);
    return token;
}

ActuatorCleanPulse ActuatorRuntime_CleanPulse(void)
{
    uint32_t previous = io.enter_critical();
    ActuatorCleanPulse copy = clean_pulse;
    io.leave_critical(previous);
    return copy;
}

uint8_t ActuatorRuntime_AbortCleanPulse(uint32_t token)
{
    uint32_t previous = io.enter_critical();
    uint8_t accepted = 0U;
    if(clean_pulse.present && clean_pulse.token == token)
    {
        if(!clean_pulse.completed)
        {
            CleanLock_Stop(&lock);
            clean_pulse.interrupted = 1U;
            clean_pulse.off_at_ms = RuntimeClock_Now64Locked();
            clean_pulse.completed = 1U;
            refresh();
        }
        accepted = 1U;
    }
    io.leave_critical(previous);
    return accepted;
}

uint8_t ActuatorRuntime_ReleaseCleanPulse(uint32_t token)
{
    uint32_t previous = io.enter_critical();
    uint8_t released = 0U;
    if(clean_pulse.present && clean_pulse.token == token && clean_pulse.completed)
    {
        clean_pulse.present = 0U;
        released = 1U;
    }
    io.leave_critical(previous);
    return released;
}

uint32_t ActuatorRuntime_BeginRecoveryClose(uint64_t execute_before_ms)
{
    uint32_t previous = io.enter_critical();
    uint64_t now = RuntimeClock_Now64Locked();
    DoorControlSnapshot state = DoorControl_Read(&door, io.read_pinch());
    ActuatorRecoveryClose next = {0};
    uint8_t coalesced = state.target_valid && state.action_active && state.target == MCU_DIRECTION_CLOSE;
    uint32_t token = 0U;
    if(!update_latched && !delivery.present && !clean_pulse.present && !recovery_close.present
        && !CleanLock_IsPowered(&lock) && recovery_token != UINT32_MAX
        && now <= UINT64_MAX - 100U && execute_before_ms > now
        && (coalesced || execute_before_ms > now + 100U))
    {
        token = ++recovery_token;
        native_mode = 1U;
        next.token = token;
        next.present = 1U;
        next.deadline_ms = execute_before_ms;
        next.due_ms = now + 100U;
        next.coalesced = next.completed = coalesced;
        next.terminal_at_ms = coalesced ? now : 0U;
        recovery_close = next;
        if(!coalesced) DoorControl_Stop(&door);
        refresh();
    }
    io.leave_critical(previous);
    return token;
}

ActuatorRecoveryClose ActuatorRuntime_RecoveryClose(void)
{
    uint32_t previous = io.enter_critical();
    ActuatorRecoveryClose copy = recovery_close;
    io.leave_critical(previous);
    return copy;
}

uint8_t ActuatorRuntime_ReleaseRecoveryClose(uint32_t token)
{
    uint32_t previous = io.enter_critical();
    uint8_t released = 0U;
    if(recovery_close.present && recovery_close.token == token && recovery_close.completed)
    {
        recovery_close.present = 0U;
        released = 1U;
    }
    io.leave_critical(previous);
    return released;
}

void ActuatorRuntime_Tick(void) { refresh(); }

ActuatorSnapshot ActuatorRuntime_Snapshot(void)
{
    uint32_t previous = io.enter_critical();
    ActuatorSnapshot state;
    state.door = applied_door;
    state.pinch_input_active = applied_pinch;
    state.lock_powered = CleanLock_IsPowered(&lock);
    state.update_latched = update_latched;
    state.control_uptime_ms = applied_uptime_ms;
    state.captured_uptime_ms = RuntimeClock_Now64Locked();
    io.leave_critical(previous);
    return state;
}
