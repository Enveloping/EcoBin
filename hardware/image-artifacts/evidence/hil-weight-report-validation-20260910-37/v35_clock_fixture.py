"""Supply the SYNCED precondition of six existing clock-dependent tests.

This plugin is test-only. It does not change the source snapshot, operating
system clock, production time checks, or dedicated untrusted-clock tests.
"""
import pytest

SYNCED_TESTS = {
    'test_expired_first_factory_seal_delivery_is_rejected_without_persisting',
    'test_expired_factory_seal_inserted_locally_is_terminally_rejected',
    'test_other_expired_command_still_uses_execution_time',
    'test_expired_photo_becomes_permanently_missing_without_grant',
    'test_agent_store_owns_session_and_durable_status_outbox',
    'test_status_event_projects_nullable_failure_code_and_fixed_enum',
}

@pytest.fixture(autouse=True)
def known_synced_precondition(request, monkeypatch):
    if request.node.name in SYNCED_TESTS:
        import trusted_clock
        monkeypatch.setattr(trusted_clock, '_clock_quality', lambda: 'SYNCED')
