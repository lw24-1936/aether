"""Skill Manager — load, search, trigger-matching, CRUD.

Skills directory structure:
  ~/.aether/skills/
    coding/
      python-debugging/SKILL.md
      git-workflow/SKILL.md
    devops/
      docker-deploy/SKILL.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aether.platform import get_data_dir
from aether.skills.parser import (
    ParsedSkill,
    SkillMeta,
    parse_skill_file,
    create_skill_file,
    validate_skill_file,
)


class SkillManager:
    """Manages skill discovery, loading, and trigger matching."""

    def __init__(self, skills_dir: str | Path | None = None):
        if skills_dir is None:
            skills_dir = get_data_dir() / "skills"
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ParsedSkill] = {}
        self._index: dict[str, str] = {}  # trigger → skill_name

    # ═══════════════════════════════════════════════════
    # Discovery & Loading
    # ═══════════════════════════════════════════════════

    def discover(self) -> list[str]:
        """Discover all SKILL.md files in the skills directory. Returns skill names."""
        skill_names = []
        for skill_file in self.skills_dir.rglob("SKILL.md"):
            try:
                skill = parse_skill_file(skill_file)
                self._cache[skill.meta.name] = skill
                skill_names.append(skill.meta.name)
                # Build trigger index
                for trigger in skill.meta.triggers:
                    self._index[trigger.lower()] = skill.meta.name
            except (ValueError, FileNotFoundError):
                continue
        return skill_names

    def load(self, name: str) -> ParsedSkill | None:
        """Load a specific skill by name. Caches after first load."""
        if name in self._cache:
            return self._cache[name]

        # Search for the skill file
        for skill_file in self.skills_dir.rglob("SKILL.md"):
            try:
                skill = parse_skill_file(skill_file)
                if skill.meta.name == name:
                    self._cache[name] = skill
                    for trigger in skill.meta.triggers:
                        self._index[trigger.lower()] = name
                    return skill
            except (ValueError, FileNotFoundError):
                continue
        return None

    def list_all(self) -> list[SkillMeta]:
        """List metadata for all discovered skills."""
        if not self._cache:
            self.discover()
        return [s.meta for s in self._cache.values()]

    def reload(self) -> None:
        """Clear cache and rediscover all skills."""
        self._cache.clear()
        self._index.clear()
        self.discover()

    # ═══════════════════════════════════════════════════
    # Trigger matching
    # ═══════════════════════════════════════════════════

    def match_triggers(self, text: str) -> list[ParsedSkill]:
        """Find skills whose triggers match the given text.

        Matching: case-insensitive substring match.
        Returns skills ranked by trigger specificity (longest match first).
        """
        if not self._cache:
            self.discover()

        text_lower = text.lower()
        matches: list[tuple[int, ParsedSkill]] = []

        for name, skill in self._cache.items():
            best_score = 0
            for trigger in skill.meta.triggers:
                trigger_lower = trigger.lower()
                if trigger_lower in text_lower:
                    # Score = trigger length (longer = more specific)
                    score = len(trigger_lower)
                    if score > best_score:
                        best_score = score
            if best_score > 0:
                matches.append((best_score, skill))

        matches.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in matches]

    def search(self, query: str) -> list[ParsedSkill]:
        """Search skills by name or description (substring match)."""
        if not self._cache:
            self.discover()

        query_lower = query.lower()
        results = []
        for skill in self._cache.values():
            if (query_lower in skill.meta.name.lower() or
                query_lower in skill.meta.description.lower()):
                results.append(skill)
        return results

    # ═══════════════════════════════════════════════════
    # CRUD
    # ═══════════════════════════════════════════════════

    def create(
        self,
        name: str,
        description: str,
        body: str,
        triggers: list[str] | None = None,
        tools_required: list[str] | None = None,
        category: str = "general",
        version: str = "0.1.0",
    ) -> ParsedSkill:
        """Create a new skill and write to disk."""
        skill_dir = self.skills_dir / category
        meta = SkillMeta(
            name=name,
            description=description,
            triggers=triggers or [],
            tools_required=tools_required or [],
            version=version,
            category=category,
        )
        path = create_skill_file(skill_dir, meta, body)
        skill = parse_skill_file(path)
        self._cache[name] = skill
        for t in skill.meta.triggers:
            self._index[t.lower()] = name
        return skill

    def patch(self, name: str, old_string: str, new_string: str, replace_all: bool = False) -> bool:
        """Patch a skill's SKILL.md content (find-and-replace)."""
        skill = self.load(name)
        if not skill:
            return False

        content = skill.file_path.read_text(encoding="utf-8")
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            count = content.count(old_string)
            if count == 0:
                return False
            new_content = content.replace(old_string, new_string, 1)

        skill.file_path.write_text(new_content, encoding="utf-8")
        # Reload to update cache
        self._cache.pop(name, None)
        self.load(name)
        return True

    def delete(self, name: str) -> bool:
        """Delete a skill and its directory."""
        skill = self.load(name)
        if not skill:
            return False

        skill_dir = skill.file_path.parent
        # Remove the whole skill directory
        import shutil
        shutil.rmtree(skill_dir)
        self._cache.pop(name, None)
        # Clean trigger index
        for t, sn in list(self._index.items()):
            if sn == name:
                del self._index[t]
        return True

    def validate(self, name: str) -> list[str]:
        """Validate a skill. Returns list of errors."""
        skill = self.load(name)
        if not skill:
            return [f"Skill not found: {name}"]
        return validate_skill_file(skill.file_path)

    # ═══════════════════════════════════════════════════
    # Export
    # ═══════════════════════════════════════════════════

    def export_skill(self, name: str) -> dict[str, Any] | None:
        """Export a skill as a dict for sharing."""
        skill = self.load(name)
        if not skill:
            return None
        return {
            "meta": {
                "name": skill.meta.name,
                "description": skill.meta.description,
                "triggers": skill.meta.triggers,
                "tools_required": skill.meta.tools_required,
                "version": skill.meta.version,
                "category": skill.meta.category,
                "author": skill.meta.author,
            },
            "body": skill.body,
        }

    def import_skill(self, data: dict[str, Any]) -> ParsedSkill | None:
        """Import a skill from an exported dict."""
        try:
            meta_dict = data["meta"]
            body = data["body"]
            return self.create(
                name=meta_dict["name"],
                description=meta_dict.get("description", ""),
                body=body,
                triggers=meta_dict.get("triggers", []),
                tools_required=meta_dict.get("tools_required", []),
                category=meta_dict.get("category", "general"),
                version=meta_dict.get("version", "0.1.0"),
            )
        except (KeyError, TypeError):
            return None
