from pathlib import Path


HARDWARE = Path(__file__).resolve().parents[1]


def test_hardware_gateway_only_uses_local_remote_support_control():
    source = (HARDWARE / "main.py").read_text(encoding="utf-8")

    assert "RemoteSupportControlClient" in source
    assert "RemoteSupportStatusBridge" in source
    assert "RemoteSupportManager" not in source
    assert "self.remote_support.start()" not in source
    assert "self.remote_support.stop()" not in source


def test_tunnel_agent_has_no_mqtt_uart_or_camera_dependency():
    source = (HARDWARE / "remote_support_agent.py").read_text(
        encoding="utf-8"
    )

    assert "mqtt_client" not in source
    assert "uart" not in source.lower()
    assert "camera" not in source.lower()
