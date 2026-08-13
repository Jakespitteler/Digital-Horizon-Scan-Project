from pydantic import ValidationError
import pytest

from scheduler.background_scheduler import SchedulerConfig


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