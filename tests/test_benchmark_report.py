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
USAGES_BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "usages"
LOCAL_PATH = re.compile(r'(?:^|["\s:=])/(?:Users|home|private|tmp|var)/')


def load(relative: str):
    """A benchmark script as a module, loaded by path like the other scripts of the repository."""
    return load_script(BENCHMARK / relative)


def load_script(path: Path):
    """A script as a module, loaded by path with its folder on the import path."""
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
        for folder in (BENCHMARK, COMPARISON, USAGES_BENCHMARK):
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

    def test_same_machine_build_summary_takes_medians_and_refuses_different_graphs(self) -> None:
        """/* REQ-BENCH-010: the same-machine build summary of the three tools must give the median wall time of every step, cold and after an edit, and stop when two cold runs, or two runs after the edit, of one tool wrote graphs of different size. */"""
        path = COMPARISON / "tools" / "time_builds_same_machine.py"
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sizes = {"wingbox": {"modules": 3, "import_edges": 2}, "graphify": {"nodes": 9, "links": 8}, "codegraph": {"files": 3, "nodes": 7, "edges": 6}}
        runs = []
        for round_no, wall in enumerate([2.0, 4.0, 3.0], start=1):
            for tool, steps in (("wingbox", ["build", "edit"]), ("graphify", ["extract", "cluster", "edit"]), ("codegraph", ["init", "edit"])):
                for step in steps:
                    run = {"round": round_no, "corpus": "demo", "source_files": 3, "tool": tool, "step": step, "wall_s": wall, "cpu_s": wall}
                    if step != "cluster":
                        run["graph"] = sizes[tool]
                    runs.append(run)
        row = module.summarise(runs)[0]
        self.assertEqual((row["wingbox_build_wall_s"], row["graphify_cluster_wall_s"], row["codegraph_init_wall_s"], row["codegraph_edit_wall_s"]), (3.0, 3.0, 3.0, 3.0))
        self.assertEqual((row["codegraph_graph"], row["codegraph_edit_graph"]), (sizes["codegraph"], sizes["codegraph"]))
        self.assertIn("| demo | 3 | 3.00 | 6.00 | 3.00 | 3.00 | 3.00 | 3.00 |", module.table([row]))
        edited = [dict(run, graph={"files": 3, "nodes": 8, "edges": 6}) if run["tool"] == "codegraph" and run["step"] == "edit" else run for run in runs]
        self.assertEqual(module.summarise(edited)[0]["codegraph_edit_graph"], {"files": 3, "nodes": 8, "edges": 6})
        runs[-1] = dict(runs[-1], graph={"files": 3, "nodes": 7, "edges": 5})
        with self.assertRaises(SystemExit):
            module.summarise(runs)

    def test_usages_report_is_the_output_of_its_report_script(self) -> None:
        """/* REQ-BENCH-011: benchmarks/usages/README.md must equal what its make_report.py writes from benchmarks/usages/results. */"""
        with tempfile.TemporaryDirectory() as directory:
            written = Path(directory) / "README.md"
            subprocess.run(
                [sys.executable, "-I", str(USAGES_BENCHMARK / "make_report.py"), str(USAGES_BENCHMARK / "results"), str(written)],
                check=True,
                capture_output=True,
            )
            self.assertEqual(written.read_text(encoding="utf-8"), (USAGES_BENCHMARK / "README.md").read_text(encoding="utf-8"))

    def test_a_use_site_is_scored_by_file_and_line(self) -> None:
        """/* REQ-BENCH-012: a use site counts as found when a tool names its file and line, whatever column it gives, so tools that print no column are scored on the same sites. */"""
        usages = load_script(USAGES_BENCHMARK / "usages.py")
        reference = {("a.py", 3, 5), ("a.py", 3, 9), ("b.py", 7, 1)}
        named = {("a.py", 3, None), ("c.py", 1, None)}
        scored = usages.score_sites(named, reference)
        self.assertEqual((scored["right"], scored["missing"], scored["outside"]), (1, 1, 1))

    def test_a_rust_path_accepts_the_modules_it_passes_through(self) -> None:
        """/* REQ-BENCH-007: for a Rust path crate::a::b the file of module a must be accepted for the file that wrote the path, and not required. */"""
        module = load("impact100.py")
        data = {"modules": {"app:bin:main::": "src/main.rs", "app:bin:main::a": "src/a/mod.rs", "app:bin:main::a::b": "src/a/b.rs"},
                "refs": [{"frm": "src/user.rs", "to": "src/a/b.rs"}]}
        self.assertEqual(module.ancestor_module_edges(data), {("src/user.rs", "src/a/mod.rs"), ("src/user.rs", "src/main.rs")})


class BenchmarkInstallTest(unittest.TestCase):
    def test_every_benchmark_installs_wingbox_with_all_language_parsers(self) -> None:
        """/* REQ-BENCH-019: the 0.12 releases install the parsers of C#, Java, Go, Ruby, PHP and Dart only with the extra `all`, which later versions keep, so every benchmark script installs the Wingbox under test (and the one it compares with) with the extra `all`: a release spec becomes wingbox[all]==X.Y.Z, a checkout or a file its path with [all]. */"""
        scripts = sorted((Path(__file__).resolve().parents[1] / "benchmarks").glob("*/run.sh"))
        installing = [path for path in scripts if "WINGBOX_SPEC" in path.read_text(encoding="utf-8")]
        self.assertEqual(["cpp", "graphify", "impact", "php", "tocs", "usages"], [path.parent.name for path in installing])
        for path in installing:
            text = path.read_text(encoding="utf-8")
            with self.subTest(benchmark=path.parent.name):
                installs = [line for line in text.splitlines() if "uv pip install" in line and "$WINGBOX_" in line]
                self.assertEqual([], [line for line in installs if "with_all" not in line])
                self.assertTrue(re.search(r'uv pip install[^\n]*"\$\(with_all "\$WINGBOX_SPEC"\)"', text))
        if sys.platform != "win32":
            function = re.search(r"with_all\(\) \{.*?\n\}\n", installing[0].read_text(encoding="utf-8"), re.S)
            assert function is not None
            printed = subprocess.run(
                ["sh", "-c", function.group(0) + 'with_all wingbox==1.2.3; echo; with_all /src/checkout; echo'],
                capture_output=True, text=True, check=True,
            ).stdout.split()
            self.assertEqual(["wingbox[all]==1.2.3", "/src/checkout[all]"], printed)


if __name__ == "__main__":
    unittest.main()
