import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
# README section heading (a distinctive part of it) -> the generated report its table summarises.
SECTIONS = {
    "on the same tests": "benchmarks/graphify/README.md",
    "Find usages:": "benchmarks/usages/README.md",
}


def table_rows(readme: str, heading_part: str) -> list[str]:
    start = re.search(r"^## [^\n]*" + re.escape(heading_part) + r"[^\n]*\n", readme, re.M)
    if start is None:
        return []
    rest = readme[start.end():]
    end = re.search(r"^## ", rest, re.M)
    body = rest[: end.start()] if end else rest
    return [line for line in body.splitlines() if line.startswith("|") and not set(line) <= set("|-: ")]


class ReadmeNumbersTest(unittest.TestCase):
    def test_summary_tables_use_numbers_of_the_generated_reports(self) -> None:
        """/* REQ-DOC-003: the comparison and find-usages tables of the README are short summaries written by hand; every number in them must be a number of the report its generator wrote from the result files, so the README cannot keep the figures of an older run. */"""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        offenders = []
        for heading_part, report_path in SECTIONS.items():
            rows = table_rows(readme, heading_part)
            self.assertTrue(rows, f"README section with '{heading_part}' and a table is missing")
            report_numbers = set(NUMBER.findall((ROOT / report_path).read_text(encoding="utf-8")))
            for row in rows:
                for number in NUMBER.findall(row):
                    if number not in report_numbers:
                        offenders.append(f"{report_path}: {number} in README row {row[:60]!r}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
