"""Diagnostic only: repeat the real synthetic crash test on independent databases.

Never retry a failed database or suppress a failed attempt. All evidence remains
in a named temporary directory; no device, production DB or network access.
This driver is not collected by pytest and is not part of the runtime payload.
"""
import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hardware"))

from hardware.tests.test_process_kill_recovery import test_committed_edge_state_survives_forced_process_termination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 100:
        parser.error("attempts must be in 1..100")
    directory = Path(tempfile.mkdtemp(prefix="ecobin-sqlite-kill-probe-"))
    print(f"[DEBUG-sqlite-kill] synthetic evidence: {directory}", flush=True)
    failures = 0
    for index in range(args.attempts):
        attempt = directory / str(index + 1)
        attempt.mkdir()
        try:
            test_committed_edge_state_survives_forced_process_termination(attempt)
        except Exception as exc:
            failures += 1
            print(f"[DEBUG-sqlite-kill] {index + 1}: FAIL {type(exc).__name__}: {exc}", flush=True)
        else:
            print(f"[DEBUG-sqlite-kill] {index + 1}: PASS", flush=True)
    print(f"[DEBUG-sqlite-kill] {args.attempts - failures} passed, {failures} failed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
