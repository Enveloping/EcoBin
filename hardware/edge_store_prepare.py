"""Prepare the EdgeStore schema before first-boot seal facts are inspected."""

from __future__ import annotations

import argparse
import os

from edge_store import EdgeStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.getenv(
            "ECOBIN_EDGE_STORE_PATH",
            "/var/lib/ecobin/hardware/edge.db",
        ),
    )
    args = parser.parse_args(argv)
    EdgeStore(args.database).prepare_schema()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
