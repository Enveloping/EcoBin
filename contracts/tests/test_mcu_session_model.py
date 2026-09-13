"""Executable P0 design evidence, not golden vectors for the published UART."""
from __future__ import annotations

import unittest
import tempfile
import sqlite3
import subprocess
import sys
from dataclasses import replace
from contextlib import closing
from pathlib import Path

from mcu_session_model import (
    Binding, BindingRequest, ModelStore, PiBinding, Command, CommandFence,
    FrozenResult, ResultSlot, ResultAssembly,
)


class BindingTests(unittest.TestCase):
    def test_restart_between_probe_and_bind_rejects_old_binding(self) -> None:
        mcu = Binding()
        self.assertEqual(0, mcu.probe(11))
        request = BindingRequest(11, 101)
        mcu = Binding()  # All MCU session state is RAM-only.
        self.assertFalse(mcu.bind(request))
        self.assertEqual(0, mcu.boot_id)
        self.assertEqual(0, mcu.probe(12))
        self.assertTrue(mcu.bind(BindingRequest(12, 102)))
        self.assertEqual(102, mcu.boot_id)

    def test_lost_bind_or_pi_restart_never_reissues_used_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.sqlite"
            with ModelStore(path) as store:
                pi = PiBinding(store)
                first_probe = pi.new_probe()
                lost_bind = pi.receive_probe(first_probe, 0)
                self.assertIsNotNone(lost_bind)
                self.assertIsNone(pi.receive_probe(first_probe, 0))
            with ModelStore(path) as store:
                pi = PiBinding(store)
                self.assertIsNone(pi.receive_probe(first_probe, 0))
                second_probe = pi.new_probe()
                self.assertGreater(second_probe, first_probe)
                next_bind = pi.receive_probe(second_probe, 0)
                self.assertGreater(next_bind.boot_id, lost_bind.boot_id)
                mcu = Binding()
                mcu.probe(second_probe)
                self.assertFalse(mcu.bind(lost_bind))
                self.assertTrue(mcu.bind(next_bind))
            with ModelStore(path) as store:
                # A Pi-only restart discovers the still-running MCU; no rebind.
                pi = PiBinding(store)
                probe = pi.new_probe()
                self.assertIsNone(pi.receive_probe(probe, mcu.probe(probe)))
                self.assertEqual(next_bind.boot_id, pi.observed_boot_id)

    def test_late_binding_reply_cannot_authorize_action_after_mcu_restart(self) -> None:
        binding = Binding()
        binding.probe(1)
        self.assertTrue(binding.bind(BindingRequest(1, 101)))
        delayed_observation = binding.boot_id
        binding = Binding()
        fence = CommandFence(binding.boot_id)
        receipt = fence.receive(Command(delayed_observation, 1, "open-1", "digest-1"))
        self.assertEqual("BOOT_MISMATCH", receipt.outcome)
        self.assertFalse(receipt.execute)


class CommandTests(unittest.TestCase):
    def test_repeated_command_replies_without_executing_again(self) -> None:
        fence = CommandFence(101)
        command = Command(101, 1, "open-1", "digest-1")
        first = fence.receive(command)
        self.assertTrue(first.execute)
        self.assertEqual("ACCEPTED", first.outcome)
        duplicate = fence.receive(command)
        self.assertFalse(duplicate.execute)
        self.assertEqual(first.outcome, duplicate.outcome)
        self.assertTrue(duplicate.matches(command))
        # These fields collide under the old transport-ACK matching rule.
        other = Command(101, 1, "open-2", "digest-2")
        self.assertFalse(first.matches(other))
        self.assertEqual("IDENTITY_CONFLICT", fence.receive(other).outcome)

    def test_unused_reserved_sequence_can_be_skipped_but_never_replayed(self) -> None:
        fence = CommandFence(101)
        self.assertTrue(fence.receive(Command(101, 2, "open-2", "digest-2")).execute)
        old = fence.receive(Command(101, 1, "open-1", "digest-1"))
        self.assertEqual("OLD_DETAILS_UNAVAILABLE", old.outcome)
        self.assertFalse(old.execute)  # Never claims that the old action happened.

    def test_business_rejection_is_frozen_not_retried_when_conditions_change(self) -> None:
        fence = CommandFence(101)
        command = Command(101, 1, "open-1", "digest-1")
        self.assertEqual("BUSY", fence.receive(command, "BUSY").outcome)
        retried = fence.receive(command, "ACCEPTED")
        self.assertEqual("BUSY", retried.outcome)
        self.assertFalse(retried.execute)
        self.assertTrue(fence.receive(Command(101, 2, "open-2", "digest-2")).execute)
        self.assertEqual("OLD_DETAILS_UNAVAILABLE", fence.receive(command).outcome)

    def test_wrong_boot_does_not_consume_sequence_and_exhaustion_does_not_wrap(self) -> None:
        fence = CommandFence(102)
        self.assertFalse(fence.receive(Command(101, 1, "old", "digest")).execute)
        self.assertTrue(fence.receive(Command(102, 1, "new", "digest")).execute)
        self.assertTrue(fence.receive(Command(102, 4294967295, "last", "digest")).execute)
        self.assertEqual("INVALID_SEQUENCE", fence.receive(Command(102, 4294967296, "overflow", "digest")).outcome)
        self.assertEqual("OLD_DETAILS_UNAVAILABLE", fence.receive(Command(102, 1, "new", "digest")).outcome)


class IdentityStorageTests(unittest.TestCase):
    def test_exhausted_durable_counter_never_wraps_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.sqlite"
            with ModelStore(path) as store:
                self.assertEqual(1, store.allocate("probe", maximum=2))
                self.assertEqual(2, store.allocate("probe", maximum=2))
                with self.assertRaises(OverflowError):
                    store.allocate("probe", maximum=2)
            with ModelStore(path) as store:
                with self.assertRaises(OverflowError):
                    store.allocate("probe", maximum=2)


class ResultHandoffTests(unittest.TestCase):
    def test_missing_saved_confirmation_is_not_a_successful_ack(self) -> None:
        slot = ResultSlot(101, "delivery-1")
        self.assertFalse(slot.acknowledge(None))
        result = FrozenResult.make(101, 8, "delivery-1", b"original")
        slot.freeze(result)
        self.assertFalse(slot.acknowledge(None))
        self.assertEqual(result, slot.result)

    def test_partial_or_mixed_result_cannot_produce_complete_saved_ack(self) -> None:
        expected = FrozenResult.make(101, 8, "delivery-1", b"firstlast")
        parts = ResultAssembly(expected.identity, part_count=2, byte_count=9)
        parts.add(expected.identity, 1, b"last")
        self.assertIsNone(parts.complete())
        parts.add(expected.identity, 1, b"last")  # Exact duplicate is harmless.
        with self.assertRaises(ValueError):
            parts.add((102, *expected.identity[1:]), 0, b"first")
        with self.assertRaises(ValueError):
            parts.add(expected.identity, 1, b"else")
        parts.add(expected.identity, 0, b"first")
        self.assertEqual(expected, parts.complete())
        with tempfile.TemporaryDirectory() as directory:
            with ModelStore(Path(directory) / "model.sqlite") as store:
                self.assertEqual(expected.identity, store.save_result(parts.complete()))

    def test_collection_bounds_and_full_digest_are_enforced(self) -> None:
        expected = FrozenResult.make(101, 8, "delivery-1", b"original")
        for count, length in ((0, 8), (5, 8), (1, 0), (1, 1025)):
            with self.assertRaises(ValueError):
                ResultAssembly(expected.identity, count, length)
        parts = ResultAssembly(expected.identity, 1, 8)
        with self.assertRaises(ValueError):
            parts.add(expected.identity, 0, b"overlong-data")
        self.assertIsNone(parts.complete())
        parts.add(expected.identity, 0, b"changed!")
        with self.assertRaises(ValueError):
            parts.complete()
    def test_commit_then_pi_restart_before_ack_retains_same_result_and_one_outbox_item(self) -> None:
        result = FrozenResult.make(101, 8, "delivery-1", b"complete-result-bytes")
        slot = ResultSlot(101, "delivery-1")
        slot.freeze(result)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.sqlite"
            with ModelStore(path) as store:
                ack = store.save_result(slot.result)
                self.assertEqual(result.identity, ack)
                self.assertEqual(result, slot.result)  # COMMIT is not MCU ACK.
            with ModelStore(path) as store:
                ack = store.save_result(slot.result)
                self.assertEqual([result], store.pending_results())
                self.assertTrue(slot.acknowledge(ack))
                self.assertIsNone(slot.result)
                self.assertEqual("RELEASED", slot.state)
                self.assertTrue(slot.acknowledge(ack))  # Lost ACK response is safe.

    def test_outbox_failure_rolls_back_result_and_mcu_keeps_it(self) -> None:
        result = FrozenResult.make(101, 8, "delivery-1", b"complete-result-bytes")
        slot = ResultSlot(101, "delivery-1")
        slot.freeze(result)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.sqlite"
            with ModelStore(path) as store:
                # Inject a real SQLite write failure at the external DB boundary.
                with closing(sqlite3.connect(path)) as fault:
                    fault.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'injected'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    store.save_result(result)
                self.assertIsNone(store.saved_result(101, 8))
                self.assertEqual([], store.pending_results())
                self.assertEqual(result, slot.result)
                with closing(sqlite3.connect(path)) as fault:
                    fault.execute("DROP TRIGGER fail_outbox")
                self.assertTrue(slot.acknowledge(store.save_result(result)))

    def test_wrong_saved_ack_or_conflicting_result_cannot_release_or_replace(self) -> None:
        result = FrozenResult.make(101, 8, "delivery-1", b"original")
        slot = ResultSlot(101, "delivery-1")
        slot.freeze(result)
        for bad in (replace(result, boot_id=102), replace(result, sequence=9),
                    replace(result, work_uid="delivery-2"), replace(result, digest="wrong")):
            self.assertFalse(slot.acknowledge(bad.identity))
            self.assertEqual(result, slot.result)
        conflict = FrozenResult.make(101, 8, "delivery-1", b"different")
        with self.assertRaises(ValueError):
            slot.freeze(conflict)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.sqlite"
            with ModelStore(path) as store:
                store.save_result(result)
            with ModelStore(path) as store:
                with self.assertRaises(ValueError):
                    store.save_result(conflict)
                self.assertEqual([result], store.pending_results())

    def test_delayed_ack_for_previous_result_cannot_clear_next_work(self) -> None:
        old = FrozenResult.make(101, 8, "delivery-1", b"original")
        new = FrozenResult.make(101, 9, "clean-2", b"next-result")
        slot = ResultSlot(101, "clean-2")
        slot.freeze(new)
        self.assertFalse(slot.acknowledge(old.identity))
        self.assertEqual(new, slot.result)


class CrashBoundaryTests(unittest.TestCase):
    def test_process_exit_before_and_after_commit_for_id_and_result(self) -> None:
        worker = """
import os, sys
from pathlib import Path
from mcu_session_model import ModelStore, FrozenResult
def checkpoint(stage):
    if stage == sys.argv[2]:
        os._exit(17)
with ModelStore(Path(sys.argv[1]), checkpoint=checkpoint) as store:
    if sys.argv[3] == 'allocate':
        store.allocate('probe')
    else:
        store.save_result(FrozenResult.make(101, 8, 'delivery-1', b'original'))
raise AssertionError('exit checkpoint not reached')
"""
        for operation in ("allocate", "result"):
            for stage in ("before_commit", "after_commit"):
                with self.subTest(operation=operation, stage=stage):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "model.sqlite"
                        exited = subprocess.run(
                            [sys.executable, "-c", worker, str(path), stage, operation],
                            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10,
                        )
                        self.assertEqual(17, exited.returncode, exited.stderr)
                        with ModelStore(path) as store:
                            committed = stage == "after_commit"
                            if operation == "allocate":
                                self.assertEqual(2 if committed else 1, store.allocate("probe"))
                            else:
                                result = FrozenResult.make(101, 8, "delivery-1", b"original")
                                self.assertEqual(result if committed else None, store.saved_result(101, 8))
                                self.assertEqual([result] if committed else [], store.pending_results())
                                store.save_result(result)
                                self.assertEqual([result], store.pending_results())


if __name__ == "__main__":
    unittest.main()
