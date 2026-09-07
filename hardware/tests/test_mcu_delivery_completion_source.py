from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MCU_MAIN_SOURCE = REPOSITORY_ROOT / "hardware_mcu" / "USER" / "main.c"


def _delivery_completion_block() -> str:
    source = MCU_MAIN_SOURCE.read_text(encoding="utf-8")
    start = source.index("case 0x02:   /* 测重结束")
    end = source.index("case 0x06:", start)
    return source[start:end]


def test_delivery_completion_accepts_zero_preweight_only_for_active_flow() -> None:
    block = re.sub(r"\s+", "", _delivery_completion_block())

    assert "if(delivery_flow_active)" in block
    assert "pre_w=delivery_pre_weight;" in block
    assert "delivery_pre_weight>0" not in block
    assert "unsignedlongpost_w=g_weight;" in block
    assert block.index("Vision_SendDeliveryResult") < block.index(
        "baseline_total=post_w;"
    )
