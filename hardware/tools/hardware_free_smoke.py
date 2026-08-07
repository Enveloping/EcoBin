#!/usr/bin/env python3
"""Smoke the fixed-frame PTY and simulated cameras without cloud credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Optional, Sequence

HARDWARE_DIR = Path(__file__).resolve().parents[1]
if str(HARDWARE_DIR) not in sys.path:
    sys.path.insert(0, str(HARDWARE_DIR))

from edge_store import EdgeStore  # noqa: E402
from fixed_frame_mcu_adapter import FixedFrameMcuAdapter  # noqa: E402
from photo_manager import PhotoManager  # noqa: E402
from tools.fixed_frame_pty_simulator import (  # noqa: E402
    LinuxPtyFixedFrameSimulator,
    SimulatorConfig,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args(
    arguments: Optional[Sequence[str]] = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--response-delay-ms",
        type=int,
        default=10,
        help="virtual MCU DD/EF response delay",
    )
    return parser.parse_args(arguments)


def run_smoke(response_delay_ms: int = 10) -> dict:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("hardware-free smoke requires Linux PTY support")
    if response_delay_ms < 0:
        raise ValueError("response delay must be non-negative")

    with tempfile.TemporaryDirectory(
        prefix="ecobin-hardware-free-"
    ) as directory:
        root = Path(directory)
        link_path = root / "fixed-frame-mcu"
        simulator = LinuxPtyFixedFrameSimulator(
            SimulatorConfig(response_delay_ms=response_delay_ms),
            link_path,
            exit_after_responses=2,
            log=lambda message: None,
        )
        simulator.open()
        simulator_thread = threading.Thread(
            target=simulator.run,
            daemon=True,
        )
        simulator_thread.start()
        adapter = FixedFrameMcuAdapter(
            str(link_path),
            edge_boot_id=77,
            timeout_s=0.1,
            is_simulated=True,
        )
        store = EdgeStore(str(root / "edge.db"))
        store.initialize()
        photos = PhotoManager(
            store,
            photo_dir=str(root / "photos"),
            outside_camera_source="simulated://outside",
            inside_camera_source="simulated://inside",
            device_name="SN-HARDWARE-FREE",
            start_upload_worker=False,
        )
        try:
            if not adapter.open():
                raise RuntimeError("fixed-frame adapter did not open PTY")
            delivery_command = adapter.send_command(
                "START_DELIVERY_SESSION",
                {"unitPriceTenThousandths": 4500},
            )
            delivery = adapter.read_mcu_event(timeout_ms=2000)
            clean_command = adapter.send_command(
                "START_CLEAN_OPERATION",
                {},
            )
            clean = adapter.read_mcu_event(timeout_ms=2000)
            if not delivery_command.get("acked") or delivery is None:
                raise RuntimeError("delivery PTY round trip failed")
            if not clean_command.get("acked") or clean is None:
                raise RuntimeError("clean PTY round trip failed")

            work_uid = str(uuid.uuid4())
            if not photos.capture_open_photos(work_uid):
                raise RuntimeError("simulated open photo capture failed")
            if not photos.capture_close_photos(work_uid):
                raise RuntimeError("simulated close photo capture failed")
            photo_rows = store.get_photos_by_work(work_uid)
            paths = [Path(row["local_path"]) for row in photo_rows]
            hashes = [_sha256(path) for path in paths]
            if len(photo_rows) != 4 or len(set(hashes)) != 4:
                raise RuntimeError(
                    "simulated cameras did not create four unique photos"
                )
            return {
                "ok": True,
                "uart": {
                    "priceDigit": simulator.model.last_price_digit,
                    "delivery": delivery["payload"],
                    "clean": clean["payload"],
                },
                "camera": {
                    "count": len(photo_rows),
                    "slots": sorted(
                        row["slot_name"] for row in photo_rows
                    ),
                    "states": sorted(
                        {row["state"] for row in photo_rows}
                    ),
                    "uniqueSha256Count": len(set(hashes)),
                },
            }
        finally:
            photos.close()
            store.close()
            adapter.close()
            simulator.stop()
            simulator_thread.join(timeout=2)
            simulator.close()


def main(arguments: Optional[Sequence[str]] = None) -> int:
    args = parse_args(arguments)
    try:
        result = run_smoke(args.response_delay_ms)
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errorType": type(error).__name__,
                    "error": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
