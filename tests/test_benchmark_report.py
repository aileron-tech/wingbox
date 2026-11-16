import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "graphify"
LOCAL_PATH = re.compile(r'(?:^|["\s:=])/(?:Users|home|private|tmp|var)/')


class BenchmarkReportTest(unittest.TestCase):
    def test_report_is_the_output_of_the_report_script_on_the_committed_results(self) -> None:
        """/* REQ-BENCH-001: benchmarks/graphify/README.md must equal what tools/make_report.py writes from benchmarks/graphify/results. */"""
        with tempfile.TemporaryDirectory() as directory:
            written = Path(directory) / "README.md"
            subprocess.run(
                [sys.executable, "-I", str(BENCHMARK / "tools" / "make_report.py"), str(BENCHMARK / "results"), str(written)],
                check=True,
                capture_output=True,
            )
            self.assertEqual(written.read_text(encoding="utf-8"), (BENCHMARK / "README.md").read_text(encoding="utf-8"))

    def test_committed_benchmark_files_name_no_path_of_the_machine_that_ran_them(self) -> None:
        """/* REQ-BENCH-002: benchmark results, reference lists and scripts must not contain absolute paths of a local machine. */"""
        offenders = []
        for path in sorted(BENCHMARK.rglob("*")):
            if path.is_file() and path.suffix in {".json", ".txt", ".md", ".py", ".sh"}:
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if LOCAL_PATH.search(line):
                        offenders.append(f"{path.relative_to(BENCHMARK)}:{number}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
