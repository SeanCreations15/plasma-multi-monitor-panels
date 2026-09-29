from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from tools.build_zipapp import build_zipapp


class ZipappTests(unittest.TestCase):
    def test_python_cache_files_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            package = source / "panel_sync"
            cache = package / "__pycache__"
            cache.mkdir(parents=True)
            (package / "cli.py").write_text(
                "def entrypoint():\n    pass\n",
                encoding="utf-8",
            )
            (cache / "cli.cpython-310.pyc").write_bytes(b"cached")
            target = root / "panel-sync"

            build_zipapp(source, target)

            with ZipFile(target) as archive:
                names = archive.namelist()
            self.assertIn("panel_sync/cli.py", names)
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any(name.endswith(".pyc") for name in names))


if __name__ == "__main__":
    unittest.main()
