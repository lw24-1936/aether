"""Multi-agent orchestrator — spawn, parallelize, aggregate.

Four orchestration modes:
  1. Sequential: A → B → C
  2. Parallel: A simultaneously spawns B, C, D
  3. Hierarchical: Master → Foreman → Worker
  4. Conversational: Agents chat to reach consensus
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from aether.core.config import AetherConfig
from aether.core.llm import LLMClient, ChatMessage


# ═══════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════

class SubAgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SubAgentTask:
    """A task assigned to a sub-agent."""
    id: str
    goal: str
    context: str = ""
    role: str = "leaf"                     # "leaf" | "orchestrator"
    toolsets: list[str] = field(default_factory=list)
    status: SubAgentStatus = SubAgentStatus.PENDING
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class OrchestrationResult:
    """Aggregated result from an orchestration run."""
    success: bool
    mode: str                            # "parallel" | "sequential" | "hierarchical"
    tasks: list[SubAgentTask]
    aggregated: str | None = None        # LLM-aggregated summary
    errors: list[str] = field(default_factory=list)
    total_duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════
# Sub-agent worker
# ═══════════════════════════════════════════════════════════

class SubAgentWorker:
    """A lightweight worker that executes a single task via LLM."""

    def __init__(self, config: AetherConfig):
        self.config = config
        self.llm = LLMClient(config)

    async def execute(self, task: SubAgentTask) -> str:
        """Execute a sub-agent task and return the result text."""
        task.status = SubAgentStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)

        system_prompt = (
            f"You are a sub-agent worker. Your goal: {task.goal}\n"
            f"Context: {task.context}\n\n"
            "Complete the task and return your result. Be concise and direct.\n"
            "If the task requires multiple steps, think through them systematically.\n"
            "Output ONLY the final result — no chit-chat."
        )

        messages = [ChatMessage(role="user", content=task.goal)]

        try:
            response = await self.llm.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=2000,
            )
            task.result = response.content
            task.status = SubAgentStatus.COMPLETED
            return response.content
        except Exception as e:
            task.status = SubAgentStatus.FAILED
            task.error = str(e)
            return f"[FAILED: {e}]"
        finally:
            task.completed_at = datetime.now(timezone.utc)

    async def close(self) -> None:
        await self.llm.close()


# ═══════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════

class Orchestrator:
    """Multi-agent task orchestrator."""

    MAX_PARALLEL = 5
    MAX_HIERARCHY_DEPTH = 2

    def __init__(self, config: AetherConfig):
        self.config = config
        self.workers: list[SubAgentWorker] = []

    async def close(self) -> None:
        for w in self.workers:
            await w.close()

    # ═══════════════════════════════════════════════════
    # Parallel mode
    # ═══════════════════════════════════════════════════

    async def parallel(
        self,
        tasks: list[dict[str, str]],  # [{goal, context}]
        aggregator_prompt: str | None = None,
    ) -> OrchestrationResult:
        """Execute multiple tasks in parallel, then aggregate.

        Args:
            tasks: List of {"goal": ..., "context": ...} dicts
            aggregator_prompt: Optional prompt for aggregating results
        """
        import time
        start = time.monotonic()

        sub_tasks = []
        for t in tasks:
            sub_tasks.append(SubAgentTask(
                id=uuid.uuid4().hex[:8],
                goal=t["goal"],
                context=t.get("context", ""),
            ))

        # Parallel execution
        workers = []
        futures = []
        for task in sub_tasks:
            worker = SubAgentWorker(self.config)
            workers.append(worker)
            futures.append(worker.execute(task))

        await asyncio.gather(*futures, return_exceptions=True)
        for w in workers:
            await w.close()

        # Aggregate results
        aggregated = None
        if aggregator_prompt and sub_tasks:
            aggregated = await self._aggregate(sub_tasks, aggregator_prompt)

        elapsed = (time.monotonic() - start) * 1000
        errors = [t.error for t in sub_tasks if t.error]
        success = all(t.status == SubAgentStatus.COMPLETED for t in sub_tasks)

        return OrchestrationResult(
            success=success,
            mode="parallel",
            tasks=sub_tasks,
            aggregated=aggregated,
            errors=errors,
            total_duration_ms=elapsed,
        )

    # ═══════════════════════════════════════════════════
    # Sequential mode
    # ═══════════════════════════════════════════════════

    async def sequential(
        self,
        tasks: list[dict[str, str]],
    ) -> OrchestrationResult:
        """Execute tasks one after another, each building on previous results."""
        import time
        start = time.monotonic()

        sub_tasks = []
        accumulated_context = ""

        for t in tasks:
            context = t.get("context", "")
            if accumulated_context:
                context = f"{context}\n\nPrevious results:\n{accumulated_context}"

            task = SubAgentTask(
                id=uuid.uuid4().hex[:8],
                goal=t["goal"],
                context=context,
            )
            sub_tasks.append(task)

            worker = SubAgentWorker(self.config)
            result = await worker.execute(task)
            await worker.close()
            accumulated_context += f"\nTask '{task.goal[:50]}': {result}"

        elapsed = (time.monotonic() - start) * 1000
        errors = [t.error for t in sub_tasks if t.error]
        success = all(t.status == SubAgentStatus.COMPLETED for t in sub_tasks)

        return OrchestrationResult(
            success=success,
            mode="sequential",
            tasks=sub_tasks,
            errors=errors,
            total_duration_ms=elapsed,
        )

    # ═══════════════════════════════════════════════════
    # Hierarchical mode
    # ═══════════════════════════════════════════════════

    async def hierarchical(
        self,
        master_goal: str,
        worker_goals: list[str],
        master_context: str = "",
    ) -> OrchestrationResult:
        """Master agent decomposes goal → workers execute → master aggregates.

        Args:
            master_goal: The high-level goal for the master agent
            worker_goals: Sub-goals for worker agents
            master_context: Additional context for the master
        """
        import time
        start = time.monotonic()

        # Step 1: Master plans
        decomposition_prompt = (
            f"Goal: {master_goal}\n"
            f"Sub-goals to delegate: {worker_goals}\n\n"
            "For each sub-goal, write what context to give the worker. "
            "Output JSON: [{goal, context}]"
        )

        master_worker = SubAgentWorker(self.config)
        plan_task = SubAgentTask(
            id=uuid.uuid4().hex[:8],
            goal=f"Decompose: {master_goal}",
            context=master_context,
            role="orchestrator",
        )
        plan_result = await master_worker.execute(plan_task)
        await master_worker.close()

        # Step 2: Workers execute in parallel
        tasks = [{"goal": g, "context": master_context} for g in worker_goals]
        parallel_result = await self.parallel(
            tasks,
            aggregator_prompt=f"Synthesize these results for the goal: {master_goal}",
        )

        elapsed = (time.monotonic() - start) * 1000
        all_tasks = [plan_task] + parallel_result.tasks
        errors = [t.error for t in all_tasks if t.error]

        return OrchestrationResult(
            success=parallel_result.success,
            mode="hierarchical",
            tasks=all_tasks,
            aggregated=parallel_result.aggregated,
            errors=errors,
            total_duration_ms=elapsed,
        )

    # ═══════════════════════════════════════════════════
    # Conversational mode
    # ═══════════════════════════════════════════════════

    async def conversational(
        self,
        topic: str,
        personas: list[dict[str, str]],  # [{name, role, perspective}]
        rounds: int = 2,
    ) -> OrchestrationResult:
        """Simulate a conversation between multiple agent personas.

        Args:
            topic: The discussion topic
            personas: List of agent personas with name, role, perspective
            rounds: Number of conversation rounds
        """
        import time
        start = time.monotonic()

        conversation: list[dict] = []
        tasks = []

        for r in range(rounds):
            for persona in personas:
                conversation_history = "\n".join(
                    f"{c['name']}({c['role']}): {c['message']}"
                    for c in conversation[-6:]  # Last 6 messages for context
                )

                context = (
                    f"You are {persona['name']}, a {persona['role']}.\n"
                    f"Your perspective: {persona['perspective']}\n\n"
                    f"Topic: {topic}\n\n"
                    f"Conversation so far:\n{conversation_history}\n\n"
                    "Add your perspective. Be concise (1-3 sentences). "
                    "Build on others' points or respectfully disagree."
                )

                worker = SubAgentWorker(self.config)
                task = SubAgentTask(
                    id=uuid.uuid4().hex[:8],
                    goal=f"Discuss: {topic}",
                    context=context,
                )
                result = await worker.execute(task)
                await worker.close()
                tasks.append(task)

                conversation.append({
                    "name": persona["name"],
                    "role": persona["role"],
                    "message": result,
                    "round": r + 1,
                })

        # Aggregate
        agg_prompt = f"Summarize this conversation about '{topic}' into key insights:"
        agg_worker = SubAgentWorker(self.config)
        agg_task = SubAgentTask(
            id=uuid.uuid4().hex[:8],
            goal=agg_prompt,
            context=json.dumps(conversation[-10:], indent=2),
        )
        aggregated = await agg_worker.execute(agg_task)
        await agg_worker.close()
        tasks.append(agg_task)

        elapsed = (time.monotonic() - start) * 1000

        return OrchestrationResult(
            success=True,
            mode="conversational",
            tasks=tasks,
            aggregated=aggregated,
            total_duration_ms=elapsed,
        )

    # ═══════════════════════════════════════════════════
    # Aggregation helper
    # ═══════════════════════════════════════════════════

    async def _aggregate(
        self,
        tasks: list[SubAgentTask],
        prompt: str,
    ) -> str:
        """Use an LLM to aggregate multiple sub-agent results."""
        results_text = "\n\n---\n\n".join(
            f"Task {i+1}: {t.goal[:60]}\nResult: {t.result or t.error}"
            for i, t in enumerate(tasks)
        )
        worker = SubAgentWorker(self.config)
        agg_task = SubAgentTask(
            id=uuid.uuid4().hex[:8],
            goal=prompt,
            context=results_text,
        )
        result = await worker.execute(agg_task)
        await worker.close()
        return result
