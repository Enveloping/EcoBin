"""Real OneNet MQTT reconnect diagnostic without printing credentials.

Run from ``hardware/`` with a development device environment:

    uv run --python 3.11 python tools/mqtt_reconnect_smoke.py --env-file .env

The probe opens one real MQTT connection, closes its underlying socket to
simulate an unexpected TCP disconnect, and waits for Paho's automatic
reconnect. It does not subscribe, publish online state, or relay outbox data.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv


HARDWARE_ROOT = Path(__file__).resolve().parents[1]
if str(HARDWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(HARDWARE_ROOT))

from mqtt_client import MqttClient  # noqa: E402


class _ProbeStore:
    def save_mqtt_persistent_state(self, *args) -> None:
        pass


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def run_probe(deadline_seconds: float) -> float:
    if deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")

    mqtt_client = MqttClient(
        product_id=_required_environment("ECOBIN_PRODUCT_ID"),
        device_name=_required_environment("ECOBIN_DEVICE_NAME"),
        device_key=_required_environment("ECOBIN_DEVICE_KEY"),
        edge_store=_ProbeStore(),
        mqtt_host=os.environ.get("ECOBIN_MQTT_HOST", "mqtts.heclouds.com"),
        mqtt_port=int(os.environ.get("ECOBIN_MQTT_PORT", "1883")),
    )
    mqtt_client._subscribe_topics = lambda: None
    mqtt_client._publish_online = lambda: None
    mqtt_client._relay_pending_events = lambda: None
    mqtt_client._start_relay_loop = lambda: None

    reconnect_event = threading.Event()
    connect_count = 0
    original_on_connect = mqtt_client._on_connect

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        nonlocal connect_count
        original_on_connect(client, userdata, flags, reason_code, properties)
        if mqtt_client._reason_code_int(reason_code) == 0:
            connect_count += 1
            if connect_count >= 2:
                reconnect_event.set()

    mqtt_client.client.on_connect = on_connect
    try:
        if not mqtt_client.connect():
            raise RuntimeError("initial MQTT connection failed")
        started = time.monotonic()
        mqtt_client.client._sock_close()
        if not reconnect_event.wait(deadline_seconds):
            raise TimeoutError(
                f"MQTT did not reconnect within {deadline_seconds:g} seconds"
            )
        return time.monotonic() - started
    finally:
        mqtt_client.disconnect()
        if mqtt_client._network_loop_started:
            mqtt_client.client.loop_stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure one real unexpected-disconnect MQTT recovery."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=HARDWARE_ROOT / ".env",
        help="development device .env file (values are never printed)",
    )
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=10.0,
        help="maximum accepted recovery time after closing the connected socket",
    )
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error(f"env file not found: {args.env_file}")
    load_dotenv(args.env_file, override=False)
    try:
        elapsed = run_probe(args.deadline_seconds)
    except (RuntimeError, TimeoutError, ValueError) as error:
        print(f"MQTT reconnect smoke failed: {error}", file=sys.stderr)
        return 1
    print(
        "MQTT reconnect smoke passed: "
        f"{elapsed:.3f}s <= {args.deadline_seconds:g}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
