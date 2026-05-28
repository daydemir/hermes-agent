"""Regression coverage for Rolly canonical user IDs in gateway auth."""

from unittest.mock import patch

from gateway.pairing import PairingStore
from gateway.run import GatewayRunner
from gateway.session import Platform, SessionSource


def _runner_with_pairing_store(tmp_path):
    with patch("gateway.pairing.PAIRING_DIR", tmp_path):
        store = PairingStore()
        runner = GatewayRunner.__new__(GatewayRunner)
        runner.pairing_store = store
        return runner, store


def _telegram_source(user_id="deniz", user_id_alt="7249456219"):
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="7249456219",
        chat_type="dm",
        user_id=user_id,
        user_id_alt=user_id_alt,
        user_name="Deniz Aydemir",
    )


def test_canonical_telegram_user_is_authorized_by_raw_pairing_approval(tmp_path, monkeypatch):
    """A source stored as slug should still match its raw Telegram approval."""
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOW_ALL_USERS", "false")
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "false")

    runner, store = _runner_with_pairing_store(tmp_path)
    store._approve_user("telegram", "7249456219", "Deniz Aydemir")

    assert runner._is_user_authorized(_telegram_source()) is True


def test_canonical_telegram_user_is_authorized_by_raw_allowlist(tmp_path, monkeypatch):
    """Allowlist checks should include the preserved raw platform ID too."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "7249456219")
    monkeypatch.delenv("GATEWAY_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOW_ALL_USERS", "false")
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "false")

    runner, _store = _runner_with_pairing_store(tmp_path)

    assert runner._is_user_authorized(_telegram_source()) is True
