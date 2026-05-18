"""Circuit breaker for tool execution.

Prevents cascading failures by temporarily disabling
tools that repeatedly fail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BreakerState(str, Enum):
    CLOSED = "closed"          # Normal operation
    OPEN = "open"              # Failing, reject calls
    HALF_OPEN = "half_open"    # Testing recovery


@dataclass
class CircuitBreaker:
    """Circuit breaker for a single tool/endpoint.

    States:
      CLOSED → (failures >= threshold) → OPEN
      OPEN → (cooldown elapsed) → HALF_OPEN
      HALF_OPEN → (success) → CLOSED
      HALF_OPEN → (failure) → OPEN
    """

    name: str
    failure_threshold: int = 5
    cooldown_seconds: float = 60.0
    half_open_max: int = 1  # Max requests in half-open state

    state: BreakerState = BreakerState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.monotonic)
    half_open_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0

    @property
    def is_open(self) -> bool:
        return self.state == BreakerState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state == BreakerState.CLOSED

    def _transition(self, new_state: BreakerState) -> None:
        old = self.state
        self.state = new_state
        self.last_state_change = time.monotonic()
        if new_state == BreakerState.HALF_OPEN:
            self.half_open_requests = 0

    def before_call(self) -> bool:
        """Check if call is allowed. Returns True if allowed."""
        if self.state == BreakerState.CLOSED:
            return True

        if self.state == BreakerState.OPEN:
            elapsed = time.monotonic() - self.last_state_change
            if elapsed >= self.cooldown_seconds:
                self._transition(BreakerState.HALF_OPEN)
                return self.before_call()
            return False

        if self.state == BreakerState.HALF_OPEN:
            if self.half_open_requests < self.half_open_max:
                self.half_open_requests += 1
                return True
            return False

        return False

    def on_success(self) -> None:
        """Record a successful call."""
        self.total_successes += 1
        if self.state == BreakerState.HALF_OPEN:
            self.failure_count = 0
            self._transition(BreakerState.CLOSED)

    def on_failure(self, error: str = "") -> None:
        """Record a failed call."""
        self.total_failures += 1
        self.failure_count += 1
        self.last_failure_time = time.monotonic()

        if self.state == BreakerState.HALF_OPEN:
            self._transition(BreakerState.OPEN)
        elif self.state == BreakerState.CLOSED and self.failure_count >= self.failure_threshold:
            self._transition(BreakerState.OPEN)

    def reset(self) -> None:
        """Force reset to closed state."""
        self.failure_count = 0
        self.half_open_requests = 0
        self._transition(BreakerState.CLOSED)

    def status(self) -> dict[str, Any]:
        """Get current status for monitoring."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "cooldown_remaining": max(0, self.cooldown_seconds - (time.monotonic() - self.last_state_change))
            if self.state == BreakerState.OPEN else 0,
        }


class BreakerRegistry:
    """Registry of circuit breakers for tools."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        """Get or create a breaker for a tool."""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name)
        return self._breakers[name]

    def status_all(self) -> list[dict[str, Any]]:
        return [b.status() for b in self._breakers.values()]

    def reset_all(self) -> None:
        for b in self._breakers.values():
            b.reset()
