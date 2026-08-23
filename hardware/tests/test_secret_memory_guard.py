from __future__ import annotations

from pathlib import Path

import pytest

from secret_memory_guard import SecretMemoryGuardError, require_no_active_swap


HEADER = "Filename\tType\tSize\tUsed\tPriority\n"


def test_empty_proc_swaps_is_accepted(tmp_path: Path) -> None:
    proc_swaps = tmp_path / "swaps"
    proc_swaps.write_text(HEADER, encoding="ascii")

    require_no_active_swap(proc_swaps)


@pytest.mark.parametrize(
    "document",
    [
        HEADER + "/swapfile file 1048572 0 -2\n",
        HEADER + "/dev/zram0 partition 524284 0 100\n",
    ],
)
def test_any_active_swap_is_rejected(tmp_path: Path, document: str) -> None:
    proc_swaps = tmp_path / "swaps"
    proc_swaps.write_text(document, encoding="ascii")

    with pytest.raises(SecretMemoryGuardError, match="active swap"):
        require_no_active_swap(proc_swaps)


def test_missing_or_malformed_swap_evidence_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SecretMemoryGuardError, match="cannot prove"):
        require_no_active_swap(tmp_path / "missing")

    malformed = tmp_path / "malformed"
    malformed.write_text("not proc swaps\n", encoding="ascii")
    with pytest.raises(SecretMemoryGuardError, match="header"):
        require_no_active_swap(malformed)
