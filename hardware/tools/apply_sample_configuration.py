"""Queue the local one-port UART HIL configuration through the edge gateway."""

from __future__ import annotations

import sys

from edge_local_command import main


if __name__ == "__main__":
    raise SystemExit(
        main(["apply-sample-configuration", *sys.argv[1:]])
    )
