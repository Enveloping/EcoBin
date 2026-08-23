from __future__ import annotations

import argparse
from typing import Sequence

from .facts import SystemFactsProvider
from .state_machine import gate_allows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recheck an EcoBin first-boot gate")
    parser.add_argument(
        "--require",
        required=True,
        choices=("factory-test", "factory-test-passed", "enrollment", "handoff", "runtime"),
    )
    args = parser.parse_args(argv)
    facts = SystemFactsProvider().collect()
    return 0 if gate_allows(args.require, facts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
