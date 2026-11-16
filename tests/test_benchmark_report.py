import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "impact"
COMPARISON = Path(__file__).resolve().parents[1] / "benchmarks" / "graphify"
PHP_BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "php"
LOCAL_PATH = re.compile(r'(?:^|["\s:=])/(?:Users|home|private|tmp|var)/')


def load(relative: str):
    """A benchmark script as a module, loaded by path like the other scripts of the repository."""
    path = BENCHMARK / relative
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


class BenchmarkReportTest(unittest.TestCase):
    def test_report_is_the_output_of_the_report_script_on_the_committed_results(self) -> None:
        """/* REQ-BENCH-001: benchmarks/impact/README.md must equal what make_report.py writes from benchmarks/impact/results. */"""
        with tempfile.TemporaryDirectory() as directory:
            written = Path(directory) / "README.md"
            subprocess.run(
                [sys.executable, "-I", str(BENCHMARK / "make_report.py"), str(BENCHMARK / "results"), str(written)],
                check=True,
                capture_output=True,
            )
            self.assertEqual(written.read_text(encoding="utf-8"), (BENCHMARK / "README.md").read_text(encoding="utf-8"))

    def test_committed_benchmark_files_name_no_path_of_the_machine_that_ran_them(self) -> None:
        """/* REQ-BENCH-002: benchmark results, reference lists, logs and scripts must not contain absolute paths of a local machine. */"""
        offenders = []
        for folder in (BENCHMARK, COMPARISON):
            for path in sorted(folder.rglob("*")):
                if path.is_file() and path.suffix in {".json", ".txt", ".md", ".py", ".sh", ".log"}:
                    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                        if LOCAL_PATH.search(line):
                            offenders.append(f"{path.relative_to(folder.parent)}:{number}")
        self.assertEqual(offenders, [])

    def test_comparison_report_is_the_output_of_its_report_script(self) -> None:
        """/* REQ-BENCH-008: benchmarks/graphify/README.md must equal what its tools/make_report.py writes from benchmarks/graphify/results. */"""
        with tempfile.TemporaryDirectory() as directory:
            written = Path(directory) / "README.md"
            subprocess.run(
                [sys.executable, "-I", str(COMPARISON / "tools" / "make_report.py"), str(COMPARISON / "results"), str(written)],
                check=True,
                capture_output=True,
            )
            self.assertEqual(written.read_text(encoding="utf-8"), (COMPARISON / "README.md").read_text(encoding="utf-8"))

    def test_build_time_summary_takes_the_median_of_the_rounds(self) -> None:
        """/* REQ-BENCH-003: the build-time summary must give, per corpus, the median wall time of the build and the size of the graph it wrote. */"""
        module = load("tools/time_builds.py")
        graph = {"modules": 12, "import_edges": 20}
        runs = [{"round": number, "corpus": "demo", "source_files": 12, "wall_s": wall, "cpu_s": wall * 2, "graph": graph}
                for number, wall in enumerate([2.0, 4.0, 3.0], start=1)]
        rows = module.summarise(runs)
        self.assertEqual(rows, [{"corpus": "demo", "source_files": 12, "build_wall_s": 3.0, "build_cpu_s": 6.0, "modules": 12, "import_edges": 20}])
        self.assertIn("| demo | 12 | 3.00 | 12 | 20 |", module.table(rows))

    def test_build_time_summary_refuses_rounds_that_built_different_graphs(self) -> None:
        """/* REQ-BENCH-004: the build-time summary must stop when two builds of one corpus wrote graphs of different size. */"""
        module = load("tools/time_builds.py")
        run = {"corpus": "demo", "source_files": 12, "wall_s": 1.0, "cpu_s": 1.0}
        different = [dict(run, round=1, graph={"modules": 12, "import_edges": 20}), dict(run, round=2, graph={"modules": 12, "import_edges": 19})]
        with self.assertRaises(SystemExit):
            module.summarise(different)

    def test_interval_of_a_proportion_is_the_wilson_interval(self) -> None:
        """/* REQ-BENCH-005: the interval of "k of n" must be the 95 % Wilson score interval, which stays inside 0..1 and is wide for small n. */"""
        stats = load("stats.py")
        low, high = stats.wilson(5, 5)
        self.assertAlmostEqual(low, 0.5655, places=3)
        self.assertEqual(high, 1.0)
        low, high = stats.wilson(100, 100)
        self.assertAlmostEqual(low, 0.9630, places=3)
        low, high = stats.wilson(48, 55)
        self.assertAlmostEqual(low, 0.7598, places=3)
        self.assertAlmostEqual(high, 0.9370, places=3)
        self.assertAlmostEqual(stats.sign_test(7, 0), 0.015625)

    def test_an_accepted_file_is_neither_required_nor_wrong(self) -> None:
        """/* REQ-BENCH-006: a named file that the reference accepts must not count as outside the list, and a missing accepted file must not count as missing. */"""
        module = load("impact100.py")
        gold = {"gold": ["a.py", "b.py"], "also_accepted": ["pkg/__init__.py"]}
        with_accepted = module.score({"a.py", "b.py", "pkg/__init__.py", "x.py"}, gold, "x.py")
        self.assertTrue(with_accepted["exact"])
        self.assertFalse(with_accepted["exact_strict"])
        self.assertEqual((with_accepted["right"], with_accepted["not_on_list"], with_accepted["missing"]), (2, 0, 0))
        without = module.score({"a.py", "b.py"}, gold, "x.py")
        self.assertTrue(without["exact"] and without["exact_strict"])
        outside = module.score({"a.py", "c.py"}, gold, "x.py")
        self.assertEqual((outside["right"], outside["not_on_list"], outside["missing"], outside["exact"]), (1, 1, 1, False))

    def test_php_report_and_results_name_no_local_path(self) -> None:
        """/* REQ-BENCH-009: benchmarks/php/README.md must equal what its make_report.py writes from benchmarks/php/results, and every committed result file must contain no absolute path of a local machine. */"""
        with tempfile.TemporaryDirectory() as directory:
            written = Path(directory) / "README.md"
            subprocess.run(
                [sys.executable, "-I", str(PHP_BENCHMARK / "make_report.py"), str(PHP_BENCHMARK / "results"), str(written)],
                check=True,
                capture_output=True,
            )
            self.assertEqual(written.read_text(encoding="utf-8"), (PHP_BENCHMARK / "README.md").read_text(encoding="utf-8"))
        offenders = []
        for path in sorted(PHP_BENCHMARK.rglob("*")):
            if path.name.startswith("Dockerfile"):
                # A container image names /var/lib/apt and the like; that is not a
                # path of the machine that ran the benchmark.
                continue
            if path.is_file() and path.suffix in {".json", ".txt", ".md", ".py", ".sh", ".log", ".php"}:
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if LOCAL_PATH.search(line):
                        offenders.append(f"{path.relative_to(PHP_BENCHMARK.parent)}:{number}")
        self.assertEqual(offenders, [])

    def test_a_rust_path_accepts_the_modules_it_passes_through(self) -> None:
        """/* REQ-BENCH-007: for a Rust path crate::a::b the file of module a must be accepted for the file that wrote the path, and not required. */"""
        module = load("impact100.py")
        data = {"modules": {"app:bin:main::": "src/main.rs", "app:bin:main::a": "src/a/mod.rs", "app:bin:main::a::b": "src/a/b.rs"},
                "refs": [{"frm": "src/user.rs", "to": "src/a/b.rs"}]}
        self.assertEqual(module.ancestor_module_edges(data), {("src/user.rs", "src/a/mod.rs"), ("src/user.rs", "src/main.rs")})


if __name__ == "__main__":
    unittest.main()
