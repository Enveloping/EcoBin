"""EcoBin fact-driven first-boot orchestration.

The package deliberately does not own factory actions, enrollment secrets, or
factory sealing.  It only coordinates those independently recoverable stages
and publishes a strictly reduced status view for the local portal.
"""

from .model import FactoryTestStatus, FirstBootFacts, FirstBootStage

__all__ = ["FactoryTestStatus", "FirstBootFacts", "FirstBootStage"]
