"""Cron Engine — scheduled LLM tasks, script jobs, and chain jobs.

Three job types:
  1. Agent Job: LLM-driven task at schedule
  2. Script Job: Run a script, stdout is the message
  3. Chain Job: Job A completes → Job B triggers → Job C

Schedule: Cron expressions + natural language aliases.
Delivery: file, stdout, webhook callback.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from aether.core.config import AetherConfig
from aether.core.llm import LLMClient, ChatMessage
from aether.platform import get_data_dir


# ═══════════════════════════════════════════════════════════
# Natural language → Cron
# ═══════════════════════════════════════════════════════════

NL_SCHEDULE_MAP: dict[str, str] = {
    "every minute": "* * * * *",
    "every hour": "0 * * * *",
    "every day": "0 0 * * *",
    "every morning": "0 8 * * *",
    "every evening": "0 20 * * *",
    "every monday": "0 9 * * 1",
    "every tuesday": "0 9 * * 2",
    "every wednesday": "0 9 * * 3",
    "every thursday": "0 9 * * 4",
    "every friday": "0 9 * * 5",
    "every saturday": "0 10 * * 6",
    "every sunday": "0 10 * * 0",
    "every weekday": "0 9 * * 1-5",
    "every weekend": "0 10 * * 6,0",
    "every week": "0 9 * * 1",
    "every month": "0 0 1 * *",
    "hourly": "0 * * * *",
    "daily": "0 0 * * *",
    "weekly": "0 9 * * 1",
    "monthly": "0 0 1 * *",
    "midnight": "0 0 * * *",
    "noon": "0 12 * * *",
}


def parse_schedule(schedule: str) -> str:
    """Parse a schedule string (cron or natural language) into a cron expression.

    Examples:
        "0 9 * * *"   → "0 9 * * *"
        "every day"   → "0 0 * * *"
        "30m"         → "*/30 * * * *"
        "2h"          → "0 */2 * * *"
    """
    # Already a cron expression (5 parts)
    parts = schedule.strip().split()
    if len(parts) == 5:
        return schedule.strip()

    # Natural language lookup
    lower = schedule.strip().lower()
    if lower in NL_SCHEDULE_MAP:
        return NL_SCHEDULE_MAP[lower]

    # Interval shorthand: "30m", "2h"
    import re
    match = re.match(r"^(\d+)([mhd])$", lower)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "m":
            return f"*/{value} * * * *"
        elif unit == "h":
            return f"0 */{value} * * *"
        elif unit == "d":
            return f"0 0 */{value} * *"

    raise ValueError(f"Unrecognized schedule: '{schedule}'. Use cron format or natural language.")


# ═══════════════════════════════════════════════════════════
# Job types
# ═══════════════════════════════════════════════════════════

class JobType(str, Enum):
    AGENT = "agent"       # LLM-driven
    SCRIPT = "script"     # Run a script
    CHAIN = "chain"       # Trigger after another job


class JobStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobResult:
    """Result of a single job run."""
    job_id: str
    run_id: str
    status: str                     # "success" | "failed"
    output: str = ""
    error: str = ""
    started_at: str = ""
    completed_at: str = ""


@dataclass
class CronJob:
    """A scheduled job definition."""
    id: str
    name: str
    schedule: str                  # Cron expression
    job_type: JobType = JobType.AGENT
    prompt: str | None = None      # For agent jobs
    script_path: str | None = None # For script jobs
    upstream_jobs: list[str] = field(default_factory=list)  # For chain jobs
    status: JobStatus = JobStatus.ACTIVE
    deliver_to: list[str] = field(default_factory=list)  # "stdout", "file", "webhook"
    max_runs: int = 0              # 0 = unlimited
    run_count: int = 0
    last_run: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ═══════════════════════════════════════════════════════════
# Cron Engine
# ═══════════════════════════════════════════════════════════

class CronEngine:
    """Scheduled job engine.

    Uses a simple polling loop with croniter for schedule matching.
    No external scheduler dependency — lightweight and cross-platform.
    """

    def __init__(
        self,
        config: AetherConfig | None = None,
        data_dir: str | Path | None = None,
    ):
        self.config = config or AetherConfig()
        if data_dir is None:
            data_dir = get_data_dir() / "cron"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.jobs: dict[str, CronJob] = {}
        self.history: dict[str, list[JobResult]] = {}  # job_id → [results]
        self._running = False
        self._task: asyncio.Task | None = None
        self._callbacks: dict[str, Callable] = {}  # job_id → callback

    # ═══════════════════════════════════════════════════
    # Job management
    # ═══════════════════════════════════════════════════

    def create(
        self,
        name: str,
        schedule: str,
        prompt: str | None = None,
        job_type: JobType = JobType.AGENT,
        script_path: str | None = None,
        upstream_jobs: list[str] | None = None,
        deliver_to: list[str] | None = None,
        max_runs: int = 0,
    ) -> CronJob:
        """Create a new scheduled job."""
        cron_expr = parse_schedule(schedule)
        job = CronJob(
            id=uuid.uuid4().hex[:12],
            name=name,
            schedule=cron_expr,
            job_type=job_type,
            prompt=prompt,
            script_path=script_path,
            upstream_jobs=upstream_jobs or [],
            deliver_to=deliver_to or ["stdout"],
            max_runs=max_runs,
        )
        self.jobs[job.id] = job
        self._save()
        return job

    def get(self, job_id: str) -> CronJob | None:
        return self.jobs.get(job_id)

    def list_all(self) -> list[CronJob]:
        return list(self.jobs.values())

    def pause(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job:
            job.status = JobStatus.PAUSED
            self._save()
            return True
        return False

    def resume(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job:
            job.status = JobStatus.ACTIVE
            self._save()
            return True
        return False

    def remove(self, job_id: str) -> bool:
        if job_id in self.jobs:
            del self.jobs[job_id]
            self._save()
            return True
        return False

    def on_complete(self, job_id: str, callback: Callable) -> None:
        """Register a callback for when a job completes."""
        self._callbacks[job_id] = callback

    # ═══════════════════════════════════════════════════
    # Execution
    # ═══════════════════════════════════════════════════

    async def start(self, poll_interval: float = 30.0) -> None:
        """Start the cron engine polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(poll_interval))

    async def stop(self) -> None:
        """Stop the cron engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def run_now(self, job_id: str) -> JobResult | None:
        """Synchronously run a job immediately."""
        return asyncio.run(self._execute_job(job_id))

    async def _poll_loop(self, interval: float) -> None:
        """Poll loop — checks schedules every `interval` seconds."""
        from croniter import croniter
        while self._running:
            now = datetime.now(timezone.utc)
            for job in list(self.jobs.values()):
                if job.status != JobStatus.ACTIVE:
                    continue
                if job.max_runs > 0 and job.run_count >= job.max_runs:
                    job.status = JobStatus.COMPLETED
                    continue

                # Check chain dependencies
                if job.job_type == JobType.CHAIN and job.upstream_jobs:
                    all_done = all(
                        self.jobs.get(uid) and self.jobs[uid].status == JobStatus.COMPLETED
                        for uid in job.upstream_jobs
                    )
                    if not all_done:
                        continue

                # Check schedule
                try:
                    cron = croniter(job.schedule, now)
                    prev = cron.get_prev(datetime)
                    if job.last_run:
                        last = datetime.fromisoformat(job.last_run)
                        if prev <= last:
                            continue
                except Exception:
                    continue

                # Execute
                asyncio.create_task(self._execute_job_async(job.id))

            await asyncio.sleep(interval)

    async def _execute_job_async(self, job_id: str) -> JobResult:
        """Execute a job asynchronously (spawned from poll loop)."""
        job = self.jobs.get(job_id)
        if not job:
            return JobResult(job_id=job_id, run_id="", status="failed", error="Job not found")

        run_id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc).isoformat()

        try:
            if job.job_type == JobType.AGENT and job.prompt:
                output = await self._run_agent_job(job.prompt)
            elif job.job_type == JobType.SCRIPT and job.script_path:
                output = await self._run_script_job(job.script_path)
            else:
                output = f"[{job.job_type.value} job executed]"

            result = JobResult(
                job_id=job_id,
                run_id=run_id,
                status="success",
                output=output,
                started_at=now,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            result = JobResult(
                job_id=job_id,
                run_id=run_id,
                status="failed",
                error=str(e),
                started_at=now,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        # Update job
        job.run_count += 1
        job.last_run = result.completed_at
        if job.max_runs > 0 and job.run_count >= job.max_runs:
            job.status = JobStatus.COMPLETED

        # Record history
        if job_id not in self.history:
            self.history[job_id] = []
        self.history[job_id].append(result)

        # Deliver
        self._deliver(job, result)

        # Trigger callback
        if job_id in self._callbacks:
            try:
                self._callbacks[job_id](result)
            except Exception:
                pass

        # Trigger chain
        for downstream in self.jobs.values():
            if downstream.job_type == JobType.CHAIN and job_id in downstream.upstream_jobs:
                # Check if all upstream jobs are done
                pass

        self._save()
        return result

    async def _execute_job(self, job_id: str) -> JobResult | None:
        """Execute a single job (for run_now)."""
        return await self._execute_job_async(job_id)

    async def _run_agent_job(self, prompt: str) -> str:
        """Run an LLM-driven agent job."""
        client = LLMClient(self.config)
        try:
            messages = [ChatMessage(role="user", content=prompt)]
            response = await client.chat(
                messages=messages,
                system_prompt="You are Aether cron agent. Complete the task concisely.",
                temperature=0.5,
                max_tokens=1000,
            )
            return response.content
        finally:
            await client.close()

    async def _run_script_job(self, script_path: str) -> str:
        """Run a script job and capture output."""
        proc = await asyncio.create_subprocess_exec(
            "bash", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        if stderr:
            output += "\n[stderr]\n" + stderr.decode("utf-8", errors="replace")
        return output.strip()

    # ═══════════════════════════════════════════════════════
    # Delivery
    # ═══════════════════════════════════════════════════════

    def _deliver(self, job: CronJob, result: JobResult) -> None:
        """Deliver job result to configured channels."""
        for channel in job.deliver_to:
            if channel == "stdout":
                print(f"[Aether Cron] {job.name}: {result.status}")
                if result.output:
                    print(result.output[:500])
            elif channel == "file":
                out_dir = self.data_dir / "output"
                out_dir.mkdir(exist_ok=True)
                path = out_dir / f"{job.id}-{result.run_id}.txt"
                path.write_text(result.output or result.error)
            elif channel.startswith("webhook:"):
                # Webhook delivery (fire-and-forget)
                url = channel.replace("webhook:", "")
                self._deliver_webhook(url, job, result)
            elif channel.startswith("file:"):
                custom_path = channel.replace("file:", "")
                Path(custom_path).parent.mkdir(parents=True, exist_ok=True)
                Path(custom_path).write_text(result.output or result.error)

    def _deliver_webhook(self, url: str, job: CronJob, result: JobResult) -> None:
        """Fire-and-forget webhook delivery."""
        import httpx
        try:
            httpx.post(url, json={
                "job_name": job.name,
                "status": result.status,
                "output": result.output[:2000],
                "run_id": result.run_id,
            }, timeout=10)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════
    # Persistence
    # ═══════════════════════════════════════════════════════

    def _save(self) -> None:
        """Save jobs state to disk."""
        path = self.data_dir / "jobs.json"
        data = {
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "schedule": j.schedule,
                    "job_type": j.job_type.value,
                    "prompt": j.prompt,
                    "script_path": j.script_path,
                    "upstream_jobs": j.upstream_jobs,
                    "status": j.status.value,
                    "deliver_to": j.deliver_to,
                    "max_runs": j.max_runs,
                    "run_count": j.run_count,
                    "last_run": j.last_run,
                    "created_at": j.created_at,
                }
                for j in self.jobs.values()
            ]
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def load(self) -> None:
        """Load jobs state from disk."""
        path = self.data_dir / "jobs.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        for jd in data.get("jobs", []):
            job = CronJob(
                id=jd["id"],
                name=jd["name"],
                schedule=jd["schedule"],
                job_type=JobType(jd["job_type"]),
                prompt=jd.get("prompt"),
                script_path=jd.get("script_path"),
                upstream_jobs=jd.get("upstream_jobs", []),
                status=JobStatus(jd.get("status", "active")),
                deliver_to=jd.get("deliver_to", ["stdout"]),
                max_runs=jd.get("max_runs", 0),
                run_count=jd.get("run_count", 0),
                last_run=jd.get("last_run"),
                created_at=jd.get("created_at", ""),
            )
            self.jobs[job.id] = job
