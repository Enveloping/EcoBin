"""Offline factory-network and portal support for EcoBin devices.

P4 deliberately contains no production EdgeStore, OneNet, COS, enrollment, or
physical-action integration.  The hardware executor is added by P7 behind a
separate, bounded interface.
"""

from .network import FACTORY_ADDRESS, FACTORY_INTERFACE, FACTORY_PORT

__all__ = ["FACTORY_ADDRESS", "FACTORY_INTERFACE", "FACTORY_PORT"]
