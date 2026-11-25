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

    def test_the_sentences_around_the_comparison_table_follow_the_result_files(self) -> None:
        """/* REQ-DOC-9965: beside its table, the comparison section of the README names the Wingbox versions that benchmarks/graphify/results/versions.txt records for the tests the table summarises (`wingbox_test_b` for the FastAPI row, `wingbox_test_c` and `wingbox_test_d` for the others) and no other, and its sentence on the size of the answers gives the two numbers of the same sentence of the generated report, which are the means of `tokens_chars_div_4` over the questions CodeGraph answers in the result files. */"""
        import json

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = re.search(r"^## [^\n]*on the same tests[^\n]*\n", readme, re.M)
        body = readme[start.end():]
        body = body[: re.search(r"^## ", body, re.M).start()]
        versions = dict(
            line.split(None, 1) for line in (ROOT / "benchmarks/graphify/results/versions.txt").read_text(encoding="utf-8").splitlines()
        )
        recorded = {versions[key] for key in ("wingbox_test_b", "wingbox_test_c", "wingbox_test_d")}
        intro = body.split("\n|", 1)[0]
        self.assertEqual(recorded, set(re.findall(r"Wingbox (\d+\.\d+\.\d+)", intro)), intro[-400:])
        self.assertIn(f"FastAPI row with Wingbox {versions['wingbox_test_b']}", " ".join(intro.split()))

        sizes = re.compile(r"\(([\d,]+) tokens against ([\d,]+) for Wingbox")
        report = (ROOT / "benchmarks/graphify/README.md").read_text(encoding="utf-8")
        in_report, in_readme = sizes.search(report), sizes.search(" ".join(body.split()))
        self.assertIsNotNone(in_report)
        self.assertIsNotNone(in_readme, "the sentence on the size of the answers is missing")
        self.assertEqual(in_report.groups(), in_readme.groups())
        results = ROOT / "benchmarks/graphify/results"
        wingbox = json.loads((results / "command_check.json").read_text(encoding="utf-8"))
        codegraph = json.loads((results / "command_check.codegraph.json").read_text(encoding="utf-8"))
        rows = list(codegraph.values()) if isinstance(codegraph, dict) else codegraph
        answered = {row["question"] for row in rows if row["codegraph"]["files_named"]}
        mean = lambda tool, rows: round(sum(row[tool]["tokens_chars_div_4"] for row in rows) / len(rows))  # noqa: E731
        measured = (mean("codegraph", [row for row in rows if row["question"] in answered]),
                    mean("wingbox", [row for row in wingbox if row["question"] in answered]))
        self.assertEqual(tuple(f"{value:,}" for value in measured), in_readme.groups())


if __name__ == "__main__":
    unittest.main()
