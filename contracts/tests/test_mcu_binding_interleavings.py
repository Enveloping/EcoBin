"""Bounded exploration under an explicit non-duplicating channel premise.

Not a proof of the deployed UART or a claim about unbounded arbitrary faults.
Frame tuples contain an oracle epoch for assertions ONLY; it is never passed
to the MCU model and is not a proposed wire field or MCU-generated boot ID.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import unittest

from mcu_session_model import Binding, BindingRequest


@dataclass(frozen=True)
class State:
    boot_id: int = 0
    pending_probe: int = 0
    pi_pending: int = 0
    probes_allocated: int = 0
    boots_allocated: int = 0
    mcu_epoch: int = 0
    pi_restarts: int = 0
    down: tuple = ()
    up: tuple = ()
    bindings: tuple = ()


def successors(state: State):
    # Endpoint resets do NOT clear either channel: old kernel/line buffers stay.
    if state.mcu_epoch < 2:
        yield replace(state, boot_id=0, pending_probe=0, mcu_epoch=state.mcu_epoch + 1)
    if state.pi_restarts < 1:
        yield replace(state, pi_pending=0, pi_restarts=state.pi_restarts + 1)
    if state.probes_allocated < 3:
        probe = state.probes_allocated + 1
        yield replace(state, probes_allocated=probe, pi_pending=probe,
                      down=state.down + (("PROBE", probe, 0, -1),))

    # Allow even reordering, stronger than the intended direct FIFO UART.
    # Each queued frame is delivered OR lost, never cloned/re-delivered.
    for index, frame in enumerate(state.down):
        remaining = state.down[:index] + state.down[index + 1:]
        yield replace(state, down=remaining)
        mcu = Binding(state.boot_id, state.pending_probe)
        kind, probe, boot, observed_epoch = frame
        if kind == "PROBE":
            observed = mcu.probe(probe)
            yield replace(state, down=remaining, pending_probe=mcu.pending_probe_id,
                          up=state.up + (("REPLY", probe, observed, state.mcu_epoch),))
        else:
            accepted = mcu.bind(BindingRequest(probe, boot))
            bindings = state.bindings
            if accepted:
                assert observed_epoch == state.mcu_epoch, ("bind crossed reset", state, frame)
                assert all(old_boot != boot for old_boot, _ in bindings), ("boot reused", state, frame)
                bindings += ((boot, state.mcu_epoch),)
            yield replace(state, down=remaining, boot_id=mcu.boot_id,
                          pending_probe=mcu.pending_probe_id, bindings=bindings)

    for index, frame in enumerate(state.up):
        remaining = state.up[:index] + state.up[index + 1:]
        yield replace(state, up=remaining)
        _, probe, boot, epoch = frame
        if probe != state.pi_pending:
            continue  # Retired requests and pre-Pi-restart replies are ignored.
        if boot != 0:
            yield replace(state, up=remaining, pi_pending=0)
        else:
            candidate = state.boots_allocated + 1
            yield replace(state, up=remaining, pi_pending=0, boots_allocated=candidate,
                          down=state.down + (("BIND", probe, candidate, epoch),))


def explore() -> tuple[int, int]:
    initial = State()
    seen = {initial}
    stack = [initial]
    bound_states = 0
    while stack:
        state = stack.pop()
        if state.bindings:
            bound_states += 1
        for next_state in successors(state):
            if next_state not in seen:
                seen.add(next_state)
                stack.append(next_state)
        if len(seen) > 500000:
            raise AssertionError("bounded exploration unexpectedly exceeded its state budget")
    return len(seen), bound_states


class BindingInterleavingTests(unittest.TestCase):
    def test_three_probes_two_mcu_resets_and_one_pi_restart(self) -> None:
        states, bound_states = explore()
        self.assertGreater(states, 1000)
        self.assertGreater(bound_states, 100)

    def test_replayed_whole_handshake_is_a_counterexample_outside_the_premise(self) -> None:
        request = BindingRequest(1, 101)
        mcu = Binding()
        mcu.probe(1)
        self.assertTrue(mcu.bind(request))
        # Intentionally violate the premise by cloning both old requests.
        mcu = Binding()
        mcu.probe(1)
        self.assertTrue(mcu.bind(request))
        self.assertEqual(101, mcu.boot_id)
        # Therefore a retrying transport / replaying bridge MUST NOT use this
        # scheme without additional freshness evidence or a different protocol.


if __name__ == "__main__":
    unittest.main()
