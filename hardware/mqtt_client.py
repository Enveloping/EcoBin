"""Compatibility import for the direct OneNet cloud transport.

Business command admission and event-outbox access intentionally do not live
in this module.  New runtime code should import ``DirectOneNetTransport``;
``MqttClient`` remains temporarily available for field diagnostics while the
single-process bridge is rolled out.
"""

from direct_onenet_transport import (
    DirectOneNetTransport,
    MAX_INBOUND_MESSAGE_BYTES,
    ONENET_TOKEN_METHOD,
    ONENET_TOKEN_TTL,
    ONENET_TOKEN_VERSION,
    build_onenet_token,
)


MqttClient = DirectOneNetTransport
_build_onenet_token = build_onenet_token


__all__ = [
    "DirectOneNetTransport",
    "MAX_INBOUND_MESSAGE_BYTES",
    "MqttClient",
    "ONENET_TOKEN_METHOD",
    "ONENET_TOKEN_TTL",
    "ONENET_TOKEN_VERSION",
    "build_onenet_token",
]
