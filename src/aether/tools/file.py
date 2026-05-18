"""Built-in file tools — read, write, search, patch files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileReadResult:
    path: str
    content: str
    total_lines: int
    offset: int
    limit: int


@dataclass
class FileWriteResult:
    path: str
    bytes_written: int


@dataclass
class SearchResult:
    matches: list[dict]  # [{path, line_num, content, ...}]
    total_matches: int


class FileTools:
    """File operations: read, write, search, patch."""

    name: str = "file"
    description: str = "Read, write, and search files."

    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()

    def _resolve(self, path: str | Path) -> Path:
        """Resolve path relative to workdir."""
        p = Path(path)
        if not p.is_absolute():
            p = self.workdir / p
        return p.resolve()

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "search", "patch"],
                    "description": "File operation to perform",
                },
                "path": {
                    "type": "string",
                    "description": "File path (relative or absolute)",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (for write action)",
                },
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (for search action)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line offset for read (1-indexed)",
                    "default": 1,
                },
                "limit": {
                    "type": "integer", 
                    "description": "Max lines to read",
                    "default": 500,
                },
            },
            "required": ["action", "path"],
        }

    def read(self, path: str, offset: int = 1, limit: int = 500) -> FileReadResult:
        """Read a file with pagination."""
        filepath = self._resolve(path)
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        if filepath.is_dir():
            raise IsADirectoryError(f"Path is a directory: {filepath}")

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        start = max(0, offset - 1)
        end = min(start + limit, total)
        selected = lines[start:end]

        # Add line numbers
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i}|{line.rstrip()}")

        return FileReadResult(
            path=str(filepath),
            content="\n".join(numbered),
            total_lines=total,
            offset=offset,
            limit=limit,
        )

    def write(self, path: str, content: str) -> FileWriteResult:
        """Write content to a file (overwrites)."""
        filepath = self._resolve(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return FileWriteResult(path=str(filepath), bytes_written=len(content.encode("utf-8")))

    def search(
        self, pattern: str, path: str = ".", file_glob: str | None = None, limit: int = 50
    ) -> SearchResult:
        """Search file contents using regex."""
        import re

        search_dir = self._resolve(path)
        if not search_dir.exists():
            raise FileNotFoundError(f"Directory not found: {search_dir}")

        matches = []
        pattern_re = re.compile(pattern)

        glob_pattern = file_glob or "*"
        files = list(search_dir.rglob(glob_pattern)) if search_dir.is_dir() else [search_dir]

        for filepath in files:
            if not filepath.is_file():
                continue
            if len(matches) >= limit:
                break
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if pattern_re.search(line):
                            matches.append({
                                "path": str(filepath),
                                "line_num": i,
                                "content": line.rstrip()[:200],
                            })
                            if len(matches) >= limit:
                                break
            except (PermissionError, OSError):
                continue

        return SearchResult(matches=matches, total_matches=len(matches))

    def patch(
        self, path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> FileWriteResult:
        """Replace text in a file."""
        filepath = self._resolve(path)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            count = content.count(old_string)
            if count == 0:
                raise ValueError(f"Old string not found in {path}")
            if count > 1:
                raise ValueError(
                    f"Found {count} matches. Use replace_all=true or make old_string more specific."
                )
            new_content = content.replace(old_string, new_string, 1)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

        return FileWriteResult(path=str(filepath), bytes_written=len(new_content.encode("utf-8")))
