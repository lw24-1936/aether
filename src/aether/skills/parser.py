"""Skill file parser — YAML frontmatter + Markdown body.

Format (SKILL.md):
  ---
  name: skill-name
  description: What this skill does
  triggers: ["keyword1", "keyword2"]
  tools_required: ["terminal", "file"]
  version: "1.0.0"
  category: "coding"
  ---
  # Skill content in Markdown
  ## Steps
  1. ...
  ## Pitfalls
  - ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Regex to extract YAML frontmatter + Markdown body
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


@dataclass
class SkillMeta:
    """Skill metadata from YAML frontmatter."""
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    tools_required: list[str] = field(default_factory=list)
    version: str = "0.1.0"
    category: str = "general"
    author: str | None = None


@dataclass
class ParsedSkill:
    """A fully parsed skill."""
    meta: SkillMeta
    body: str                          # Markdown body
    file_path: Path                    # Source file
    linked_files: dict[str, str] = field(default_factory=dict)  # name → path


def parse_skill_file(file_path: str | Path) -> ParsedSkill:
    """Parse a SKILL.md file into a ParsedSkill.

    Raises ValueError if the file doesn't have valid YAML frontmatter.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {path}")

    content = path.read_text(encoding="utf-8")

    match = FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError(
            f"Invalid skill file: {path}. "
            "Must start with YAML frontmatter between --- markers."
        )

    frontmatter_str = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter in {path}: {e}")

    if not isinstance(frontmatter, dict):
        raise ValueError(f"Frontmatter must be a YAML mapping in {path}")

    if "name" not in frontmatter:
        raise ValueError(f"Missing required field 'name' in {path}")

    meta = SkillMeta(
        name=frontmatter["name"],
        description=frontmatter.get("description", ""),
        triggers=frontmatter.get("triggers", []),
        tools_required=frontmatter.get("tools_required", []),
        version=str(frontmatter.get("version", "0.1.0")),
        category=frontmatter.get("category", "general"),
        author=frontmatter.get("author"),
    )

    # Discover linked files (scripts/, templates/, references/, assets/)
    linked_files = {}
    skill_dir = path.parent
    for subdir in ("scripts", "templates", "references", "assets"):
        subpath = skill_dir / subdir
        if subpath.is_dir():
            for f in subpath.iterdir():
                if f.is_file():
                    linked_files[f"{subdir}/{f.name}"] = str(f)

    return ParsedSkill(
        meta=meta,
        body=body,
        file_path=path,
        linked_files=linked_files,
    )


def create_skill_file(
    directory: str | Path,
    meta: SkillMeta,
    body: str,
) -> Path:
    """Create a new SKILL.md file from metadata and body."""
    skill_dir = Path(directory) / meta.name
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "name": meta.name,
        "description": meta.description,
        "triggers": meta.triggers,
        "tools_required": meta.tools_required,
        "version": meta.version,
        "category": meta.category,
    }
    if meta.author:
        frontmatter["author"] = meta.author

    yaml_str = yaml.safe_dump(frontmatter, default_flow_style=False, allow_unicode=True, indent=2).strip()
    content = f"---\n{yaml_str}\n---\n\n{body.strip()}\n"

    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content, encoding="utf-8")
    return skill_path


def validate_skill_file(file_path: str | Path) -> list[str]:
    """Validate a SKILL.md file. Returns list of errors (empty = valid)."""
    errors = []
    try:
        skill = parse_skill_file(file_path)
    except (FileNotFoundError, ValueError) as e:
        return [str(e)]

    if not skill.meta.name:
        errors.append("Missing 'name' field")
    if not skill.body.strip():
        errors.append("Skill body is empty (no steps/instructions)")
    if not skill.meta.triggers:
        errors.append("No triggers defined — skill will never be auto-loaded")
    if not skill.meta.description:
        errors.append("Missing description")
    if skill.meta.version and not re.match(r"^\d+\.\d+\.\d+", skill.meta.version):
        errors.append(f"Invalid version format: {skill.meta.version} (use X.Y.Z)")

    return errors
