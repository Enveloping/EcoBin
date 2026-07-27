"""Queue a local diagnostic START_DELIVERY_SESSION command.

Run this file directly on the Orange Pi while the normal EcoBin gateway is
running.  All arguments are forwarded to ``edge_local_command.py
start-delivery``.
"""

from __future__ import annotations

import sys

from edge_local_command import main


if __name__ == "__main__":
    raise SystemExit(main(["start-delivery", *sys.argv[1:]]))
