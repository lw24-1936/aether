"""Code intelligence — repo map generator and codebase analysis.

Generates structured file tree summaries (Aider-style Repo Map)
and provides basic codebase understanding capabilities.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════
# Repo Map
# ═══════════════════════════════════════════════════════════

@dataclass
class FileInfo:
    """Information about a single source file."""
    path: str
    language: str = ""
    lines: int = 0
    summary: str = ""           # First docstring or comment
    exports: list[str] = field(default_factory=list)  # Functions/classes defined


@dataclass
class RepoMap:
    """Structured summary of a codebase."""
    root: str
    files: list[FileInfo] = field(default_factory=list)
    total_files: int = 0
    total_lines: int = 0
    languages: dict[str, int] = field(default_factory=dict)  # lang → file count


def _detect_language(filepath: Path) -> str:
    """Detect programming language from file extension."""
    ext_map = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript React",
        ".jsx": "JavaScript React",
        ".java": "Java",
        ".go": "Go",
        ".rs": "Rust",
        ".cpp": "C++",
        ".c": "C",
        ".h": "C/C++ Header",
        ".rb": "Ruby",
        ".php": "PHP",
        ".swift": "Swift",
        ".kt": "Kotlin",
        ".sh": "Shell",
        ".bash": "Shell",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".json": "JSON",
        ".toml": "TOML",
        ".md": "Markdown",
        ".sql": "SQL",
        ".html": "HTML",
        ".css": "CSS",
        ".vue": "Vue",
        ".dockerfile": "Dockerfile",
    }
    suffix = filepath.suffix.lower()
    if suffix in ext_map:
        return ext_map[suffix]
    if filepath.name.lower() == "dockerfile":
        return "Dockerfile"
    if filepath.name.lower() == "makefile":
        return "Makefile"
    return "Unknown"


def _extract_python_summary(filepath: Path) -> tuple[str, list[str]]:
    """Extract docstring summary and exports from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8", errors="replace"))
        docstring = ast.get_docstring(tree) or ""

        exports = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                exports.append(f"def {node.name}()")
            elif isinstance(node, ast.ClassDef):
                exports.append(f"class {node.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    exports.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = ", ".join(a.name for a in node.names)
                exports.append(f"from {module} import {names}")

        # Truncate docstring
        summary = docstring.split("\n")[0][:100] if docstring else ""
        return summary, exports[:15]
    except SyntaxError:
        return "", []


IGNORE_PATTERNS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".egg-info", ".eggs", "coverage", "htmlcov",
    ".DS_Store", "Thumbs.db",
}


def generate_repo_map(
    root: str | Path,
    max_files: int = 200,
    include_patterns: list[str] | None = None,
) -> RepoMap:
    """Generate a structured repo map (Aider-style).

    Args:
        root: Root directory of the codebase
        max_files: Maximum number of files to include
        include_patterns: Glob patterns to include (e.g., ["*.py", "*.js"])

    Returns a RepoMap with file summaries.
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        return RepoMap(root=str(root_path))

    repo = RepoMap(root=str(root_path))
    files = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Skip ignored directories
        dirnames[:] = [d for d in dirnames if d not in IGNORE_PATTERNS and not d.startswith(".")]

        for filename in filenames:
            if len(files) >= max_files:
                break

            filepath = Path(dirpath) / filename
            relpath = filepath.relative_to(root_path)

            # Filter by include patterns
            if include_patterns:
                if not any(filepath.match(p) for p in include_patterns):
                    continue

            lang = _detect_language(filepath)

            # Count lines
            try:
                lines = sum(1 for _ in open(filepath, encoding="utf-8", errors="replace"))
            except (OSError, PermissionError):
                continue

            # Extract summary
            summary = ""
            exports = []
            if lang == "Python":
                summary, exports = _extract_python_summary(filepath)

            info = FileInfo(
                path=str(relpath),
                language=lang,
                lines=lines,
                summary=summary,
                exports=exports,
            )
            files.append(info)
            repo.total_lines += lines
            repo.languages[lang] = repo.languages.get(lang, 0) + 1

    repo.files = files
    repo.total_files = len(files)
    return repo


def format_repo_map(repo: RepoMap, max_depth: int = 3) -> str:
    """Format a RepoMap as a readable tree string (like Aider).

    Example output:
        src/
        ├── main.py ............... Entry point, calls agent.run()
        ├── core/
        │   ├── loop.py .......... Agent main loop
        │   └── models.py ........ Data models
        └── tools/
            └── terminal.py ...... Shell execution
    """
    lines = []

    # Header
    lines.append(f"Repository: {repo.root}")
    lines.append(f"Files: {repo.total_files} | Lines: {repo.total_lines}")
    lang_summary = ", ".join(f"{lang}: {count}" for lang, count in
                             sorted(repo.languages.items(), key=lambda x: -x[1])[:5])
    lines.append(f"Languages: {lang_summary}")
    lines.append("")

    # Build tree structure
    tree: dict[str, Any] = {}
    for f in repo.files:
        parts = Path(f.path).parts
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            node = node[part]
        node[parts[-1]] = f

    def _render(node: dict, prefix: str = "", depth: int = 0) -> list[str]:
        if depth > max_depth:
            return [f"{prefix}..."]
        result = []
        items = sorted(node.items(), key=lambda x: (
            isinstance(x[1], dict),  # Directories last
            x[0].lower()
        ))
        for i, (name, value) in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")

            if isinstance(value, dict):
                result.append(f"{prefix}{connector}{name}/")
                result.extend(_render(value, next_prefix, depth + 1))
            else:
                summary = value.summary[:50] if value.summary else ""
                line = f"{prefix}{connector}{name}"
                if summary:
                    line += f"  # {summary}"
                result.append(line)
        return result

    lines.extend(_render(tree))
    return "\n".join(lines)


def search_codebase(
    root: str | Path,
    query: str,
    file_glob: str = "*",
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """Simple regex search across a codebase."""
    import re
    root_path = Path(root)
    pattern = re.compile(query, re.IGNORECASE)
    results = []

    for filepath in root_path.rglob(file_glob):
        if not filepath.is_file():
            continue
        if any(p in filepath.parts for p in IGNORE_PATTERNS):
            continue

        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if pattern.search(line):
                        results.append({
                            "file": str(filepath.relative_to(root_path)),
                            "line": i,
                            "content": line.strip()[:200],
                        })
                        if len(results) >= max_results:
                            return results
        except (OSError, PermissionError):
            continue

    return results
