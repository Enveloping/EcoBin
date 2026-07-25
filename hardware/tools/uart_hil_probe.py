"""Minimal UART 1.0 hardware-in-the-loop probe.

This tool deliberately bypasses MQTT, SQLite, cameras, and COS so a failed
result identifies the Edge <-> MCU serial/protocol boundary.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

HARDWARE_DIR = Path(__file__).resolve().parents[1]
if str(HARDWARE_DIR) not in sys.path:
    sys.path.insert(0, str(HARDWARE_DIR))

from uart_link import EDGE_CAPABILITY_BITMAP, UartLink  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyS5")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--edge-boot-id", type=int, default=1)
    parser.add_argument("--port-count", type=int, default=1)
    parser.add_argument(
        "--required-capabilities",
        type=lambda value: int(value, 0),
        default=EDGE_CAPABILITY_BITMAP,
        help="required MCU capability bitmap; defaults to strict P0 mask 0x1fff",
    )
    parser.add_argument(
        "--query-state",
        action="store_true",
        help="run QUERY_STATE after a successful HELLO handshake",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    link = UartLink(
        port=args.port,
        edge_boot_id=args.edge_boot_id,
        port_count=args.port_count,
        baudrate=args.baudrate,
        required_capability_bitmap=args.required_capabilities,
    )
    if not link.open():
        print(json.dumps({"stage": "OPEN", "ok": False}, sort_keys=True))
        return 2

    try:
        mcu_info = link.handshake()
        result: dict[str, object] = {
            "stage": "HELLO",
            "ok": True,
            "mcu": mcu_info,
        }
        if args.query_state:
            result["snapshots"] = link.query_state()
            result["stage"] = "QUERY_STATE"
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "stage": "UART",
                    "ok": False,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
