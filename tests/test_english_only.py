import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile("[Ѐ-ӿ]")
TEXT = {".md", ".py", ".toml", ".yml", ".yaml", ".json", ".txt", ".sh", ".html", ".js", ".mjs", ".ts", ".sexp", ".cfg"}


def tracked_files() -> list[Path]:
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
    if listed.returncode == 0 and listed.stdout.strip():
        return [ROOT / line for line in listed.stdout.splitlines()]
    return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and ".wingbox" not in p.parts]


class EnglishOnlyTest(unittest.TestCase):
    def test_no_russian_text_in_the_repository(self) -> None:
        """/* REQ-DOC-001: the repository is written in English only: no tracked text file may contain Cyrillic letters, in documentation, logs, comments or analyses (benchmark result files and test fixtures excepted, which quote other projects' code). */"""
        offenders = []
        for path in tracked_files():
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix not in TEXT or "/results/" in f"/{relative}" or relative.startswith("tests/fixtures/"):
                continue
            if relative == "tests/test_english_only.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if CYRILLIC.search(line):
                    offenders.append(f"{relative}:{number}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
