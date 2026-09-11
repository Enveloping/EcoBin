from __future__ import annotations

import pytest

from factory.acceptance_measurements import valid_reference_weight, valid_sampling
from factory.acceptance_service import _check_summary
from first_boot.factory_flow import _validate_acceptance_projection
from .test_factory_flow_projection import _p7_projection


@pytest.mark.parametrize("reference", [11, 400, 500, 1000, 350000])
def test_reference_protocol_bounds(reference: int) -> None:
    assert valid_reference_weight(reference)


@pytest.mark.parametrize("override", [
    {"samplesGrams": [True]}, {"samplesGrams": [-1]}, {"samplesGrams": [350001]},
    {"samplesGrams": [1.1]}, {"samplesGrams": ["secret"]}, {"samplesGrams": [0] * 33},
    {"readCount": False}, {"readCount": -1}, {"resultCode": "not a safe code"}, {"secret": "hidden"},
])
def test_untrusted_sampling_is_not_projected_or_accepted(override: dict) -> None:
    trace = {"samplesGrams": [0], "readCount": 1, "resultCode": "STABLE_WEIGHT_CAPTURED", **override}
    sampling = {"empty": trace}
    assert not valid_sampling(sampling)
    assert "sampling" not in _check_summary({"sampling": sampling})
    projection = _p7_projection()
    projection["checks"]["weight"]["sampling"] = sampling
    with pytest.raises(ValueError, match="weight samples"):
        _validate_acceptance_projection(projection)
