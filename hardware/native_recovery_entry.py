"""Explicit, foreground, recovery-only candidate. Not a deployable gateway.

No legacy boot, cloud consumer, new work, camera, readiness notification,
mechanical command, automatic reconnect or protocol fallback is enabled here.
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import threading
from time import monotonic_ns

from edge_store import EdgeStore
from job_safety import PermanentJobSafety, UPDATER_PROTOCOL_NAME
from local_control import LocalControlClient
from uart2_transport import NativeUartTransport


logger = logging.getLogger("native-recovery-candidate")


def _serial_port(**kwargs):
    if os.name != "posix":
        raise RuntimeError("native recovery live serial requires POSIX exclusive ownership")
    import serial
    return serial.Serial(**kwargs)


def main(argv=None, *, port_factory=None, stop=None, wait=None,
         clock=lambda: monotonic_ns() // 1000000, driver_factory=None):
    parser = argparse.ArgumentParser(prog="main.py --native-recovery-candidate", allow_abbrev=False)
    parser.add_argument("--store-path", required=True)
    parser.add_argument("--serial-device", required=True)
    parser.add_argument("--device-name", required=True)
    parser.add_argument("--updater-socket", required=True)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--write-timeout", type=float, default=1)
    args = parser.parse_args(argv)
    path = Path(args.store_path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("native recovery requires an existing absolute database path")
    socket_path = Path(args.updater_socket)
    if not socket_path.is_absolute():
        raise ValueError("native recovery updater socket must be absolute")
    if not args.device_name.strip() or not args.serial_device.strip():
        raise ValueError("native recovery device identity and serial endpoint are required")
    if args.baudrate <= 0 or not 0 < args.write_timeout <= 1:
        raise ValueError("native recovery requires positive baudrate and bounded write timeout <= 1 second")
    if port_factory is None and os.name != "posix":
        raise RuntimeError("native recovery live serial requires POSIX exclusive ownership")
    stopping = threading.Event()
    should_stop = stop or stopping.is_set
    wait_once = wait or stopping.wait
    previous_handlers = {}
    store, port = EdgeStore(str(path)), None
    last = {"state": "STOPPED", "admissionAllowed": False}
    try:
        store.initialize_existing_recovery()
        if driver_factory is None:
            from native_recovery_runtime import NativeRecoveryRuntime
            driver_factory = NativeRecoveryRuntime
        safety = PermanentJobSafety(LocalControlClient(socket_path, protocol_name=UPDATER_PROTOCOL_NAME))
        if stop is None:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda *_: stopping.set())
        port = (port_factory or _serial_port)(port=args.serial_device, baudrate=args.baudrate,
            timeout=0, write_timeout=args.write_timeout, exclusive=True)
        transport = NativeUartTransport(port)
        driver = driver_factory(store, safety, transport, device_name=args.device_name, clock=clock)
        logger.warning("recovery-only candidate: no new work, mechanical dispatch, cloud or READY notification")
        while not should_stop():
            current = driver.poll()
            if not isinstance(current, dict) or current.get("admissionAllowed") is not False:
                raise RuntimeError("recovery-only candidate cannot enable admission")
            if current != last:
                logger.info("recovery candidate state: %s; admission disabled", current.get("state", "UNKNOWN"))
            last = current
            wait_once(0.05)
        return last
    finally:
        try:
            if port is not None:
                port.close()
        finally:
            try:
                store.close()
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)
