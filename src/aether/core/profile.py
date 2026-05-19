"""User profile — persistent identity and preferences (Hermes-style).

Stores user facts as structured YAML profile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aether.platform import get_config_dir


class UserProfile:
    """Persistent user profile with preferences and identity.

    Fields: name, email, language, preferences, environment, learned_facts.
    """

    def __init__(self, profile_path: str | Path | None = None):
        if profile_path is None:
            profile_path = get_config_dir() / "profile.yaml"
        self.path = Path(profile_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return {}

    def save(self) -> None:
        self.path.write_text(
            yaml.safe_dump(self._data, allow_unicode=True, indent=2),
            encoding="utf-8",
        )

    # ═══════════════════════════════════════════════════
    # Accessors
    # ═══════════════════════════════════════════════════

    @property
    def name(self) -> str:
        return self._data.get("name", "")

    @name.setter
    def name(self, value: str) -> None:
        self._data["name"] = value
        self.save()

    @property
    def email(self) -> str:
        return self._data.get("email", "")

    @email.setter
    def email(self, value: str) -> None:
        self._data["email"] = value
        self.save()

    @property
    def language(self) -> str:
        return self._data.get("language", "zh")

    @language.setter
    def language(self, value: str) -> None:
        self._data["language"] = value
        self.save()

    @property
    def preferences(self) -> dict[str, Any]:
        return self._data.get("preferences", {})

    def set_preference(self, key: str, value: Any) -> None:
        if "preferences" not in self._data:
            self._data["preferences"] = {}
        self._data["preferences"][key] = value
        self.save()

    def get_preference(self, key: str) -> Any:
        return self._data.get("preferences", {}).get(key)

    @property
    def environment(self) -> dict[str, Any]:
        return self._data.get("environment", {})

    def set_environment(self, key: str, value: Any) -> None:
        if "environment" not in self._data:
            self._data["environment"] = {}
        self._data["environment"][key] = value
        self.save()

    def add_learned_fact(self, fact: str) -> None:
        if "learned_facts" not in self._data:
            self._data["learned_facts"] = []
        if fact not in self._data["learned_facts"]:
            self._data["learned_facts"].append(fact)
        self.save()

    # ═══════════════════════════════════════════════════
    # Formatting (Hermes-style)
    # ═══════════════════════════════════════════════════

    def format_for_prompt(self) -> str:
        """Format the profile for injection into the system prompt."""
        if not self._data:
            return ""

        lines = ["USER PROFILE (who the user is)"]
        facts = []

        if self.name:
            facts.append(f"Name: {self.name}")
        if self.email:
            facts.append(f"Email: {self.email}")
        facts.append(f"Language: {self.language}")

        prefs = self.preferences
        if prefs:
            pref_str = ", ".join(f"{k}={v}" for k, v in prefs.items())
            facts.append(f"Preferences: {pref_str}")

        env = self.environment
        if env:
            env_str = ", ".join(f"{k}={v}" for k, v in env.items())
            facts.append(f"Environment: {env_str}")

        learned = self._data.get("learned_facts", [])
        if learned:
            facts.extend(learned)

        # Capacity tracking
        chars = sum(len(f) for f in facts)
        max_chars = 2000
        pct = min(round(chars / max_chars * 100), 100)

        lines.append(f"[{pct}% — {chars}/{max_chars} chars]")
        for f in facts:
            lines.append(f"§\n{f}")

        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        """Get profile statistics."""
        facts = self._data.get("learned_facts", [])
        chars = sum(len(f) for f in facts) + len(str(self.preferences)) + len(str(self.environment))
        return {
            "name": self.name or "(not set)",
            "language": self.language,
            "preferences_count": len(self.preferences),
            "learned_facts_count": len(facts),
            "total_chars": chars,
            "usage_percent": min(round(chars / 2000 * 100), 100),
        }
