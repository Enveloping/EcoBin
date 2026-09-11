"""Dependency-free weight evidence policy shared by all factory report users.

The reference is the known physical mass, not a calibration value or a copy of
the sensor reading. The protocol ceiling is not a safe mechanical load rating.
"""
from __future__ import annotations

import re

DEFAULT_REFERENCE_WEIGHT_GRAMS = 500
WEIGHT_TOLERANCE_GRAMS = 10
MIN_REFERENCE_WEIGHT_GRAMS = WEIGHT_TOLERANCE_GRAMS + 1
MAX_REFERENCE_WEIGHT_GRAMS = 350_000
MAX_RECENT_SAMPLES = 32


def valid_reference_weight(value: object) -> bool:
    return (
        type(value) is int
        and MIN_REFERENCE_WEIGHT_GRAMS <= value <= MAX_REFERENCE_WEIGHT_GRAMS
    )


def passed_weight_result_code(reference: int) -> str:
    return (
        "WEIGHT_500G_WITHIN_490_510_AND_REMOVED"
        if reference == DEFAULT_REFERENCE_WEIGHT_GRAMS
        else "WEIGHT_REFERENCE_WITHIN_TOLERANCE_AND_REMOVED"
    )


def valid_sampling(value: object) -> bool:
    if not isinstance(value, dict) or not set(value).issubset({"empty", "loaded", "removed"}):
        return False
    for trace in value.values():
        if not isinstance(trace, dict) or set(trace) != {"samplesGrams", "readCount", "resultCode"}:
            return False
        samples = trace["samplesGrams"]
        if (
            not isinstance(samples, list)
            or len(samples) > MAX_RECENT_SAMPLES
            or any(
                type(sample) is not int or not 0 <= sample <= MAX_REFERENCE_WEIGHT_GRAMS
                for sample in samples
            )
            or type(trace["readCount"]) is not int
            or not len(samples) <= trace["readCount"] <= 300_010
            or not isinstance(trace["resultCode"], str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", trace["resultCode"]) is None
        ):
            return False
    return True


def valid_passed_weight_check(check: object) -> bool:
    if not isinstance(check, dict):
        return False
    reference = check.get("targetDeltaGrams")
    if not valid_reference_weight(reference):
        return False
    if (
        check.get("status") != "PASSED"
        or check.get("resultCode") != passed_weight_result_code(reference)
    ):
        return False
    for field, expected in {
        "toleranceGrams": WEIGHT_TOLERANCE_GRAMS,
        "stableSampleCount": 3,
        "stableMaxSpreadGrams": 2,
        "sampleIntervalMs": 100,
        "sampleTimeoutMs": 3000,
    }.items():
        if type(check.get(field)) is not int or check[field] != expected:
            return False
    stages = {
        "empty": "emptyWeightGrams",
        "loaded": "loadedWeightGrams",
        "removed": "removedWeightGrams",
    }
    if any(
        type(check.get(field)) is not int
        or not 0 <= check[field] <= MAX_REFERENCE_WEIGHT_GRAMS
        for field in stages.values()
    ):
        return False
    delta = check.get("deltaGrams")
    if (
        type(delta) is not int
        or delta != check["loadedWeightGrams"] - check["emptyWeightGrams"]
        or abs(delta - reference) > WEIGHT_TOLERANCE_GRAMS
        or abs(check["removedWeightGrams"] - check["emptyWeightGrams"])
        > WEIGHT_TOLERANCE_GRAMS
    ):
        return False
    # Historical reports have no traces; a resumed legacy 500 g state may
    # publish an empty trace object. Neither case invents sampling evidence.
    if "sampling" not in check:
        return True
    sampling = check["sampling"]
    if sampling == {} and reference == DEFAULT_REFERENCE_WEIGHT_GRAMS:
        return True
    if not valid_sampling(sampling) or set(sampling) != set(stages):
        return False
    for stage, field in stages.items():
        trace = sampling[stage]
        window = trace["samplesGrams"][-3:]
        if (
            trace["resultCode"] != "STABLE_WEIGHT_CAPTURED"
            or len(window) != 3
            or max(window) - min(window) > 2
            or sorted(window)[1] != check[field]
        ):
            return False
    return True
