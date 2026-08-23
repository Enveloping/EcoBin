from __future__ import annotations

import re


_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class FactorySealError(RuntimeError):
    """A fail-closed factory-seal invariant with a stable public code."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or _CODE.fullmatch(code) is None:
            raise ValueError("factory seal error code is invalid")
        self.code = code
        super().__init__(code)


class FactorySealPortalError(FactorySealError):
    """A bounded error returned by the root-owned local portal endpoint."""
