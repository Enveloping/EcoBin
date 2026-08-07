import os
import subprocess
import sys
import time
from pathlib import Path

from edge_store import EdgeStore


CHILD_CODE = r"""
import sys
import time
from pathlib import Path

from edge_store import EdgeStore

db_path = sys.argv[1]
ready_path = Path(sys.argv[2])
store = EdgeStore(db_path)
store.initialize()

command_uid = "91000000-0000-4000-8000-000000000001"
work_uid = "92000000-0000-4000-8000-000000000001"
event_uid = "93000000-0000-4000-8000-000000000001"
photo_uid = "94000000-0000-4000-8000-000000000001"
confirmation_uid = "95000000-0000-4000-8000-000000000001"

assert store.receive_command(
    command_uid,
    "START_DELIVERY_SESSION",
    {
        "commandUid": command_uid,
        "commandType": "START_DELIVERY_SESSION",
    },
) == "ACCEPTED"
assert store.acquire_work_slot(
    "DELIVERY",
    work_uid,
    1,
    {"phase": "WAITING_COMPAT_DELIVERY_RESULT"},
)
assert store.create_edge_event(
    event_uid,
    "DEVICE_RUNTIME_SNAPSHOT",
    {"probe": True},
    work_uid=work_uid,
) == "ACCEPTED"
assert store.register_photo(
    photo_uid,
    "BEFORE_OUTER",
    "diagnostic-photo.jpg",
    work_uid=work_uid,
    work_type="DELIVERY_SESSION",
    device_name="SN-DIAGNOSTIC",
) == "ACCEPTED"
assert store.receive_business_confirmation(
    confirmation_uid,
    event_uid,
    "BUSINESS_APPLIED",
    {"probe": True},
) == "ACCEPTED"

ready_path.write_text("durable", encoding="utf-8")
while True:
    time.sleep(1)
"""


def test_committed_edge_state_survives_forced_process_termination(tmp_path):
    db_path = tmp_path / "edge.db"
    ready_path = tmp_path / "ready"
    hardware_dir = Path(__file__).resolve().parents[1]
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (
                str(hardware_dir),
                child_env.get("PYTHONPATH"),
            ),
        )
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            CHILD_CODE,
            str(db_path),
            str(ready_path),
        ],
        cwd=hardware_dir,
        env=child_env,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(
                    f"edge child exited before checkpoint: {process.returncode}"
                )
            time.sleep(0.02)
        assert ready_path.exists(), "edge child did not reach durable checkpoint"
        process.kill()
        assert process.wait(timeout=10) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    store = EdgeStore(str(db_path))
    store.initialize()
    try:
        assert store.get_command(
            "91000000-0000-4000-8000-000000000001"
        )
        assert store.get_work_slot()["work_uid"] == (
            "92000000-0000-4000-8000-000000000001"
        )
        assert store.get_edge_event_sequence() >= 1
        assert store.get_event(
            "93000000-0000-4000-8000-000000000001"
        )
        assert store.get_photo(
            "94000000-0000-4000-8000-000000000001"
        )
        confirmation_count = store._conn.execute(
            """
            SELECT COUNT(*)
              FROM confirmation_inbox
             WHERE confirmation_uid=?
            """,
            ("95000000-0000-4000-8000-000000000001",),
        ).fetchone()[0]
        assert confirmation_count == 1
        assert store.integrity_check()
    finally:
        store.close()
