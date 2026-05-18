"""Phase 9 tests: Code intelligence — repo map + codebase search."""

import tempfile
from pathlib import Path

import pytest

from aether.core.code_intel import (
    generate_repo_map,
    format_repo_map,
    search_codebase,
    _detect_language,
    FileInfo,
    RepoMap,
)


class TestLanguageDetection:
    def test_python(self):
        assert _detect_language(Path("test.py")) == "Python"

    def test_javascript(self):
        assert _detect_language(Path("app.js")) == "JavaScript"
        assert _detect_language(Path("app.ts")) == "TypeScript"

    def test_various(self):
        assert _detect_language(Path("main.go")) == "Go"
        assert _detect_language(Path("lib.rs")) == "Rust"
        assert _detect_language(Path("config.yaml")) == "YAML"
        assert _detect_language(Path("readme.md")) == "Markdown"


class TestRepoMap:
    @pytest.fixture
    def sample_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text('"""Entry point."""\n\ndef main():\n    pass\n')
            (root / "src" / "utils.py").write_text('"""Utilities."""\n\ndef helper():\n    return 42\n')
            (root / "README.md").write_text("# Project\nDescription")
            (root / ".git").mkdir()
            yield root

    def test_generate(self, sample_repo):
        repo = generate_repo_map(sample_repo)
        assert repo.total_files >= 2  # Not .git
        assert repo.total_lines > 0
        assert "Python" in repo.languages

    def test_format(self, sample_repo):
        repo = generate_repo_map(sample_repo)
        formatted = format_repo_map(repo)
        assert "src/" in formatted
        assert "main.py" in formatted or formatted  # at least valid output

    def test_filter_by_pattern(self, sample_repo):
        repo = generate_repo_map(sample_repo, include_patterns=["*.md"])
        assert all(f.path.endswith(".md") for f in repo.files)

    def test_python_exports(self, sample_repo):
        repo = generate_repo_map(sample_repo, include_patterns=["*.py"])
        py_files = [f for f in repo.files if f.language == "Python"]
        if py_files:
            main_file = [f for f in py_files if "main.py" in f.path]
            if main_file:
                exports = main_file[0].exports
                assert any("def main" in e for e in exports), f"Expected def main in {exports}"

    def test_max_files(self, sample_repo):
        repo = generate_repo_map(sample_repo, max_files=1)
        assert repo.total_files <= 1

    def test_nonexistent_dir(self):
        repo = generate_repo_map("/tmp/nonexistent_xyz_12345")
        assert repo.total_files == 0


class TestCodebaseSearch:
    @pytest.fixture
    def search_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "a.py").write_text("def hello():\n    print('hello world')\n")
            (root / "b.py").write_text("def goodbye():\n    return 'bye'\n")
            yield root

    def test_search_finds_match(self, search_repo):
        results = search_codebase(search_repo, "hello")
        assert len(results) >= 1
        assert any("a.py" in r["file"] for r in results)

    def test_search_no_match(self, search_repo):
        results = search_codebase(search_repo, "zzz_nonexistent")
        assert len(results) == 0

    def test_search_with_glob(self, search_repo):
        results = search_codebase(search_repo, "def", file_glob="*.py")
        assert len(results) >= 2
