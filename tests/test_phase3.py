"""Phase 3 tests: Skills system (parser, manager, trigger matching)."""

import tempfile
from pathlib import Path

import pytest

from aether.skills.parser import (
    parse_skill_file,
    create_skill_file,
    validate_skill_file,
    SkillMeta,
)
from aether.skills.manager import SkillManager


SAMPLE_SKILL = """---
name: test-skill
description: A test skill for unit testing
triggers:
  - "test"
  - "debug"
  - "error"
tools_required:
  - terminal
  - read_file
version: "1.0.0"
category: testing
---

# Test Skill

## Steps
1. Read the error
2. Find the cause
3. Fix it

## Pitfalls
- Don't rush
"""


@pytest.fixture
def skill_file():
    """Create a temporary SKILL.md file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test-skill"
        skill_dir.mkdir()
        path = skill_dir / "SKILL.md"
        path.write_text(SAMPLE_SKILL)
        yield path


@pytest.fixture
def skill_manager():
    """Create a SkillManager with a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SkillManager(tmpdir)
        yield mgr


class TestSkillParser:
    """Skill file parsing tests."""

    def test_parse_valid_skill(self, skill_file):
        skill = parse_skill_file(skill_file)
        assert skill.meta.name == "test-skill"
        assert skill.meta.description == "A test skill for unit testing"
        assert skill.meta.triggers == ["test", "debug", "error"]
        assert skill.meta.tools_required == ["terminal", "read_file"]
        assert skill.meta.version == "1.0.0"
        assert skill.meta.category == "testing"
        assert "Steps" in skill.body
        assert "Pitfalls" in skill.body

    def test_parse_missing_name(self):
        bad = """---
description: no name
---

Content
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "SKILL.md"
            path.write_text(bad)
            with pytest.raises(ValueError):
                parse_skill_file(path)

    def test_parse_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "SKILL.md"
            path.write_text("# Just markdown\n\nNo frontmatter")
            with pytest.raises(ValueError):
                parse_skill_file(path)

    def test_create_skill_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meta = SkillMeta(
                name="new-skill",
                description="Created via API",
                triggers=["create"],
                category="utils",
            )
            path = create_skill_file(tmpdir, meta, "## Steps\n1. Do it")
            assert path.exists()
            assert path.name == "SKILL.md"

            # Verify it parses back
            skill = parse_skill_file(path)
            assert skill.meta.name == "new-skill"

    def test_validate_valid(self, skill_file):
        errors = validate_skill_file(skill_file)
        assert errors == []

    def test_validate_empty_body(self):
        bad = """---
name: empty
---
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "empty"
            skill_dir.mkdir()
            path = skill_dir / "SKILL.md"
            path.write_text(bad)
            errors = validate_skill_file(path)
            assert len(errors) > 0
            assert any("empty" in e.lower() for e in errors)

    def test_validate_no_triggers(self):
        bad = """---
name: no-triggers
---
# Body content here
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "no-triggers"
            skill_dir.mkdir()
            path = skill_dir / "SKILL.md"
            path.write_text(bad)
            errors = validate_skill_file(path)
            assert len(errors) > 0


class TestSkillManager:
    """Skill manager tests."""

    def test_create_and_load(self, skill_manager):
        skill = skill_manager.create(
            name="test-1",
            description="First test skill",
            body="## Steps\n1. Step one\n2. Step two",
            triggers=["python", "debug"],
            category="coding",
        )
        assert skill.meta.name == "test-1"

        loaded = skill_manager.load("test-1")
        assert loaded is not None
        assert loaded.meta.name == "test-1"

    def test_discover_all(self, skill_manager):
        skill_manager.create("s1", "Skill 1", "body", ["trigger1"], category="a")
        skill_manager.create("s2", "Skill 2", "body", ["trigger2"], category="b")

        names = skill_manager.discover()
        assert "s1" in names
        assert "s2" in names

        all_skills = skill_manager.list_all()
        assert len(all_skills) == 2

    def test_match_triggers(self, skill_manager):
        skill_manager.create(
            "python-help",
            "Python help",
            "body",
            triggers=["python", "debug"],
            category="coding",
        )
        skill_manager.create(
            "git-help",
            "Git help",
            "body",
            triggers=["git", "commit"],
            category="coding",
        )

        # Should match python-help
        matches = skill_manager.match_triggers("help me debug this python error")
        assert len(matches) >= 1
        assert any(s.meta.name == "python-help" for s in matches)

        # Should match git-help
        matches = skill_manager.match_triggers("how to git commit")
        assert len(matches) >= 1
        assert any(s.meta.name == "git-help" for s in matches)

        # No match
        matches = skill_manager.match_triggers("what is the weather")
        assert len(matches) == 0

    def test_match_longest_trigger_first(self, skill_manager):
        skill_manager.create("s1", "S1", "body", triggers=["python"], category="a")
        skill_manager.create("s2", "S2", "body", triggers=["python error"], category="b")

        matches = skill_manager.match_triggers("I have a python error")
        assert len(matches) >= 2
        # Longer trigger should come first
        assert matches[0].meta.name == "s2"

    def test_search(self, skill_manager):
        skill_manager.create("python-debug", "Debug Python code", "body", ["debug"])
        skill_manager.create("node-server", "Node.js server setup", "body", ["node"])

        results = skill_manager.search("python")
        assert len(results) == 1
        assert results[0].meta.name == "python-debug"

        results = skill_manager.search("server")
        assert len(results) == 1

    def test_patch(self, skill_manager):
        skill = skill_manager.create("patchable", "A skill", "## Steps\n1. Old step", ["trigger"])
        assert skill_manager.patch("patchable", "Old step", "New step")
        updated = skill_manager.load("patchable")
        assert "New step" in updated.body

    def test_patch_not_found(self, skill_manager):
        skill_manager.create("x", "X", "body", ["x"])
        assert not skill_manager.patch("x", "nonexistent string", "replacement")

    def test_delete(self, skill_manager):
        skill_manager.create("to-delete", "Will be deleted", "body", ["trigger"])
        assert skill_manager.delete("to-delete")
        assert skill_manager.load("to-delete") is None
        assert not skill_manager.delete("nonexistent")

    def test_export_import(self, skill_manager):
        skill_manager.create("exportable", "For export", "# Body", ["test"], category="test")
        data = skill_manager.export_skill("exportable")
        assert data is not None
        assert data["meta"]["name"] == "exportable"

        # Import into new manager
        skill_manager.import_skill(data)
        imported = skill_manager.load("exportable")
        assert imported is not None

    def test_reload(self, skill_manager):
        skill_manager.create("r1", "R1", "# body", ["t1"])
        assert len(skill_manager.list_all()) == 1

        skill_manager.reload()
        assert len(skill_manager.list_all()) == 1

    def test_validate_skill(self, skill_manager):
        skill = skill_manager.create("valid", "Valid skill", "# Steps\n1. Do it", ["test"])
        errors = skill_manager.validate("valid")
        assert errors == []
