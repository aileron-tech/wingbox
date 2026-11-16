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

    def test_build_time_summary_takes_the_median_of_the_rounds(self) -> None:
        """/* REQ-BENCH-003: the build-time summary must give, per corpus, the median wall time of each step and the ratio of Graphify extract to Wingbox build. */"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("time_builds", BENCHMARK / "tools" / "time_builds.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        runs = []
        for round_no, (build, extract, cluster) in enumerate([(2.0, 30.0, 5.0), (4.0, 50.0, 7.0), (3.0, 40.0, 6.0)], start=1):
            for tool, step, wall in (("wingbox", "build", build), ("graphify", "extract", extract), ("graphify", "cluster", cluster)):
                runs.append({"round": round_no, "corpus": "demo", "source_files": 12, "tool": tool, "step": step, "wall_s": wall, "cpu_s": wall * 2})
        rows = module.summarise(runs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wingbox_build_wall_s"], 3.0)
        self.assertEqual(rows[0]["graphify_extract_wall_s"], 40.0)
        self.assertEqual(rows[0]["graphify_cluster_wall_s"], 6.0)
        self.assertEqual(rows[0]["extract_to_build_wall_ratio"], 13.3)
        self.assertIn("| demo | 12 | 3.0 | 40.0 | 6.0 | 13.3x |", module.table(rows))

    def test_build_time_summary_refuses_rounds_that_built_different_graphs(self) -> None:
        """/* REQ-BENCH-004: the build-time summary must stop when one tool built graphs of different size for the same corpus. */"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("time_builds", BENCHMARK / "tools" / "time_builds.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        run = {"corpus": "demo", "source_files": 12, "tool": "wingbox", "step": "build", "wall_s": 1.0, "cpu_s": 1.0}
        same = [dict(run, round=1, graph={"modules": 12, "import_edges": 20}), dict(run, round=2, graph={"modules": 12, "import_edges": 20})]
        self.assertEqual(module.summarise(same)[0]["wingbox_graph"], {"modules": 12, "import_edges": 20})
        different = [same[0], dict(run, round=2, graph={"modules": 12, "import_edges": 19})]
        with self.assertRaises(SystemExit):
            module.summarise(different)


if __name__ == "__main__":
    unittest.main()
