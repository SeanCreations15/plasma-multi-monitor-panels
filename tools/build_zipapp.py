#!/usr/bin/env python3
"""Build the self-contained Plasma Taskbar Sync executable."""

from __future__ import annotations

from pathlib import Path
import sys
import zipapp


def _include(path: Path) -> bool:
    return "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}


def build_zipapp(source: Path, target: Path) -> None:
    """Build a zipapp without local Python cache files."""

    zipapp.create_archive(
        source,
        target=target,
        interpreter="/usr/bin/env python3",
        main="plasma_taskbar_sync.cli:entrypoint",
        filter=_include,
        compressed=True,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"Usage: {argv[0]} SOURCE OUTPUT", file=sys.stderr)
        return 2
    build_zipapp(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
