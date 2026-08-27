import asyncio
import contextlib
import logging
import time

import pytest
from pydantic import ValidationError

from email_sender import notifier
from scheduler.background_scheduler import SchedulerConfig, check_notifications, run_loop


def test_scheduler_config_accepts_positive_intervals():
    config = SchedulerConfig(
        scrape_interval_seconds=5,
        notification_interval_seconds=10,
    )

    assert config.scrape_interval_seconds == 5
    assert config.notification_interval_seconds == 10


def test_scheduler_config_rejects_zero_interval():
    with pytest.raises(ValidationError):
        SchedulerConfig(
            scrape_interval_seconds=0,
            notification_interval_seconds=10,
        )

#  run_loop robustness
# Written with asyncio.run() inside sync tests rather than async test
# functions, so this needs no pytest-asyncio.

def test_run_loop_survives_a_task_that_raises(caplog):
    # One bad morning -- a network blip, a database hiccup -- must not end
    # monitoring for good. Before this, the exception unwound out of the loop
    # and nothing restarted it.
    runs = 0

    async def flaky():
        nonlocal runs
        runs += 1
        if runs == 2:
            raise RuntimeError("site unreachable")

    async def drive():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run_loop(0.01, flaky), timeout=0.2)

    asyncio.run(drive())

    assert runs > 2, "loop stopped at the exception instead of carrying on"


def test_run_loop_logs_the_failure_rather_than_swallowing_it(caplog):
    async def always_fails():
        raise RuntimeError("site unreachable")

    async def drive():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run_loop(0.01, always_fails), timeout=0.05)

    with caplog.at_level(logging.ERROR):
        asyncio.run(drive())

    assert "always_fails" in caplog.text
    assert "site unreachable" in caplog.text


def test_run_loop_is_still_cancellable():
    # Only Exception is caught, so cancellation and Ctrl-C keep working.
    async def noop():
        pass

    async def drive():
        task = asyncio.create_task(run_loop(0.01, noop))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(drive())


#  the notification run

def test_check_notifications_keeps_a_blocking_send_off_the_event_loop(monkeypatch):
    # smtplib waits up to 30s on an unresponsive server. Run inline, that
    # freezes every other task -- including the scrape loop once it's added.
    monkeypatch.setattr(
        notifier, "notify", lambda *a, **k: (time.sleep(0.2), "digest")[1]
    )

    ticks = []

    async def ticker():
        for _ in range(4):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    async def drive():
        await asyncio.gather(ticker(), check_notifications())

    asyncio.run(drive())

    spread = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
    assert spread < 0.15, f"event loop stalled for {spread:.2f}s during the send"


def test_check_notifications_reports_a_dropped_send(monkeypatch, caplog):
    # notify() returns "failed" precisely so the caller can notice. If nobody
    # looks at it, a dropped report is invisible.
    monkeypatch.setattr(notifier, "notify", lambda *a, **k: "failed")

    with caplog.at_level(logging.ERROR):
        asyncio.run(check_notifications())

    assert "send failed" in caplog.text


def test_check_notifications_logs_a_normal_run(monkeypatch, caplog):
    monkeypatch.setattr(notifier, "notify", lambda *a, **k: "nothing")

    with caplog.at_level(logging.INFO):
        asyncio.run(check_notifications())

    assert "notification run: nothing" in caplog.text
