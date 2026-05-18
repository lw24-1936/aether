"""Phase 7 tests: Cron engine — scheduling, jobs, delivery."""

import tempfile
from pathlib import Path

import pytest

from aether.core.cron import (
    CronEngine,
    CronJob,
    JobType,
    JobStatus,
    JobResult,
    parse_schedule,
    NL_SCHEDULE_MAP,
)


class TestScheduleParsing:
    """Schedule string parsing tests."""

    def test_cron_expression_passthrough(self):
        assert parse_schedule("0 9 * * *") == "0 9 * * *"
        assert parse_schedule("*/5 * * * *") == "*/5 * * * *"
        assert parse_schedule("0 0 1 * *") == "0 0 1 * *"

    def test_natural_language(self):
        assert parse_schedule("every day") == "0 0 * * *"
        assert parse_schedule("every hour") == "0 * * * *"
        assert parse_schedule("every monday") == "0 9 * * 1"
        assert parse_schedule("daily") == "0 0 * * *"
        assert parse_schedule("hourly") == "0 * * * *"

    def test_interval_shorthand(self):
        assert parse_schedule("30m") == "*/30 * * * *"
        assert parse_schedule("2h") == "0 */2 * * *"

    def test_invalid_schedule(self):
        with pytest.raises(ValueError):
            parse_schedule("invalid schedule string")

    def test_all_nl_keys_valid(self):
        """Verify all natural language keys parse to valid cron."""
        for key in NL_SCHEDULE_MAP:
            result = parse_schedule(key)
            parts = result.split()
            assert len(parts) == 5, f"'{key}' → '{result}' not valid cron"


class TestCronJob:
    """Job model tests."""

    def test_creation(self):
        job = CronJob(
            id="j1",
            name="Test Job",
            schedule="0 9 * * *",
            prompt="Summarize news",
        )
        assert job.id == "j1"
        assert job.job_type == JobType.AGENT
        assert job.status == JobStatus.ACTIVE
        assert job.run_count == 0

    def test_script_job(self):
        job = CronJob(
            id="j2",
            name="Script Job",
            schedule="0 * * * *",
            job_type=JobType.SCRIPT,
            script_path="/path/to/script.sh",
        )
        assert job.job_type == JobType.SCRIPT

    def test_chain_job(self):
        job = CronJob(
            id="j3",
            name="Chain Job",
            schedule="0 9 * * *",
            job_type=JobType.CHAIN,
            upstream_jobs=["j1", "j2"],
        )
        assert job.job_type == JobType.CHAIN
        assert len(job.upstream_jobs) == 2


class TestCronEngine:
    """Cron engine tests."""

    @pytest.fixture
    def engine(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield CronEngine(data_dir=tmpdir)

    def test_create_job(self, engine):
        job = engine.create("test", "0 9 * * *", prompt="Hello")
        assert job.id
        assert job.name == "test"
        assert job.schedule == "0 9 * * *"

    def test_list_jobs(self, engine):
        engine.create("a", "0 0 * * *")
        engine.create("b", "0 12 * * *")
        assert len(engine.list_all()) == 2

    def test_get_job(self, engine):
        job = engine.create("test", "0 9 * * *")
        found = engine.get(job.id)
        assert found is not None
        assert found.name == "test"
        assert engine.get("nonexistent") is None

    def test_pause_resume(self, engine):
        job = engine.create("test", "0 9 * * *")
        assert engine.pause(job.id)
        assert job.status == JobStatus.PAUSED
        assert engine.resume(job.id)
        assert job.status == JobStatus.ACTIVE

    def test_remove(self, engine):
        job = engine.create("test", "0 9 * * *")
        assert engine.remove(job.id)
        assert engine.get(job.id) is None
        assert not engine.remove("nonexistent")

    def test_persistence(self, engine):
        job = engine.create("persistent", "0 9 * * *", prompt="test")
        engine._save()

        # Load into a new engine
        new_engine = CronEngine(data_dir=engine.data_dir)
        new_engine.load()
        loaded = new_engine.get(job.id)
        assert loaded is not None
        assert loaded.name == "persistent"

    def test_deliver_to_file(self, engine):
        job = engine.create("test", "0 9 * * *", deliver_to=["file"])
        result = JobResult(
            job_id=job.id,
            run_id="r1",
            status="success",
            output="test output",
        )
        engine._deliver(job, result)

        # Check output file
        out_dir = engine.data_dir / "output"
        files = list(out_dir.glob("*.txt"))
        assert len(files) >= 1

    def test_schedule_validation(self, engine):
        """All jobs created should have valid cron expressions."""
        job = engine.create("valid", "every day")
        parts = job.schedule.split()
        assert len(parts) == 5

    def test_max_runs(self, engine):
        job = engine.create("limited", "0 9 * * *", max_runs=3)
        assert job.max_runs == 3

    @pytest.mark.asyncio
    async def test_start_stop(self, engine):
        """Engine should start and stop cleanly."""
        await engine.start(poll_interval=60)
        assert engine._running
        await engine.stop()
        assert not engine._running
