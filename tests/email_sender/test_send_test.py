"""The safety gate on send_test.py -- the thing standing between a
half-configured .env and a real email going to the client by accident."""

import pytest

from email_sender import notifier, send_test


@pytest.fixture
def configured(monkeypatch):
    """A .env that is fully filled in and cleared to send."""
    monkeypatch.setattr(notifier, "DRY_RUN", False)
    monkeypatch.setattr(notifier, "SMTP_USER", "scan@gmail.com")
    monkeypatch.setattr(notifier, "SMTP_PASS", "app password")
    monkeypatch.setattr(notifier, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(notifier, "CLIENT_TO", ["me@gmail.com"])


def test_a_fully_configured_setup_is_cleared_to_send(configured):
    assert send_test.check_configured() == []


def test_dry_run_blocks_sending(configured, monkeypatch):
    monkeypatch.setattr(notifier, "DRY_RUN", True)
    assert any("DRY_RUN" in p for p in send_test.check_configured())


def test_a_missing_password_is_caught(configured, monkeypatch):
    monkeypatch.setattr(notifier, "SMTP_PASS", "")
    assert any("EMAIL_PASSWORD" in p for p in send_test.check_configured())


def test_a_missing_account_is_caught(configured, monkeypatch):
    monkeypatch.setattr(notifier, "SMTP_USER", "")
    assert any("EMAIL" in p for p in send_test.check_configured())


def test_the_placeholder_recipient_is_caught(configured, monkeypatch):
    # This is the one that matters: example.com left in CLIENT_TO means the
    # report goes somewhere nobody reads, and it looks like it worked.
    monkeypatch.setattr(notifier, "CLIENT_TO", ["client@example.com"])
    assert any("CLIENT_TO" in p for p in send_test.check_configured())


def test_the_placeholder_mail_server_is_caught(configured, monkeypatch):
    monkeypatch.setattr(notifier, "SMTP_HOST", "smtp.example.com")
    assert any("SMTP_HOST" in p for p in send_test.check_configured())


def test_an_unconfigured_setup_reports_every_problem_at_once(monkeypatch):
    # Listing them all beats fixing one, re-running, and finding the next.
    monkeypatch.setattr(notifier, "DRY_RUN", True)
    monkeypatch.setattr(notifier, "SMTP_USER", "")
    monkeypatch.setattr(notifier, "SMTP_PASS", "")
    monkeypatch.setattr(notifier, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifier, "CLIENT_TO", ["client@example.com"])

    assert len(send_test.check_configured()) == 5


def test_main_refuses_and_exits_nonzero_when_unconfigured(monkeypatch, capsys):
    monkeypatch.setattr(notifier, "DRY_RUN", True)
    monkeypatch.setattr(send_test.notifier, "notify", _must_not_be_called)

    assert send_test.main() == 1
    assert "Not configured to send" in capsys.readouterr().out


def test_main_sends_when_configured(configured, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        send_test.notifier, "notify", lambda *a, **k: calls.append((a, k)) or "digest"
    )

    assert send_test.main() == 0
    assert len(calls) == 1
    assert calls[0][1]["dry_run"] is False, "the point of send_test is a real send"
    assert "Sent" in capsys.readouterr().out


def test_main_reports_a_failed_send(configured, monkeypatch):
    monkeypatch.setattr(send_test.notifier, "notify", lambda *a, **k: "failed")
    assert send_test.main() == 1


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("notify() was called despite the safety check failing")
