import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


CLI_PATH = Path(__file__).resolve().parents[1] / "src" / "wingbox" / "cli.py"
SPEC = importlib.util.spec_from_file_location("wingbox_cli", CLI_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load the wingbox command module from {CLI_PATH}")
wingbox_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wingbox_cli
SPEC.loader.exec_module(wingbox_cli)


class WingboxCommandArgumentsTest(unittest.TestCase):
    def test_build_uses_the_current_directory_by_default(self) -> None:
        """/* REQ-CLI-001: `wingbox build` without a path must build the current directory. */"""
        self.assertEqual(wingbox_cli.build_arguments([], "/work/project"), ["--root", "/work/project"])

    def test_build_resolves_a_relative_project_path_and_keeps_options(self) -> None:
        """/* REQ-CLI-002: `wingbox build PATH` must pass an absolute --root and keep the builder options. */"""
        self.assertEqual(
            wingbox_cli.build_arguments(["services/api", "--full", "--exclude-dir", "generated"], "/work"),
            ["--root", "/work/services/api", "--full", "--exclude-dir", "generated"],
        )
        self.assertEqual(
            wingbox_cli.build_arguments(["--root", "/abs/project", "--lsp"], "/work"),
            ["--root", "/abs/project", "--lsp"],
        )

    def test_query_path_options_are_resolved_against_the_current_directory(self) -> None:
        """/* REQ-CLI-003: relative --map and --root values must become absolute paths. */"""
        self.assertEqual(
            wingbox_cli.absolutize_path_options(
                ["impact", "src/app.py", "--map", ".wingbox/index.sexp", "--root", "."], "/work/project"
            ),
            ["impact", "src/app.py", "--map", "/work/project/.wingbox/index.sexp", "--root", "/work/project"],
        )

    def test_scripts_directory_falls_back_to_the_checkout(self) -> None:
        """/* REQ-CLI-004: without bundled scripts the command must use scripts/ of the source checkout. */"""
        package_dir = str(CLI_PATH.parent)
        self.assertEqual(
            wingbox_cli.scripts_directory(package_dir),
            str(CLI_PATH.parents[2] / "scripts"),
        )


class WingboxCommandEndToEndTest(unittest.TestCase):
    def test_build_then_impact_on_a_small_python_project(self) -> None:
        """/* REQ-CLI-005: `wingbox build` then `wingbox impact` must report the dependents of a file. */"""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        (root / "shop").mkdir()
        (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
        (root / "shop" / "pricing.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
        (root / "shop" / "checkout.py").write_text(
            "from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n", encoding="utf-8"
        )

        build_output = io.StringIO()
        with redirect_stdout(build_output):
            build_status = wingbox_cli.main(["build", str(root)])
        self.assertEqual(0, build_status, build_output.getvalue())
        map_path = root / ".wingbox/index.sexp"
        map_path.parent.mkdir(exist_ok=True)
        self.assertTrue(map_path.is_file())

        output = io.StringIO()
        with redirect_stdout(output):
            status = wingbox_cli.main(
                ["impact", "shop/pricing.py", "--sexp", "--map", str(map_path), "--root", str(root)]
            )
        self.assertEqual(0, status)
        self.assertIn("(impact shop/pricing shop/checkout :lang py :via", output.getvalue())
        self.assertIn("(read-first shop/checkout.py)", output.getvalue())

    def test_version_and_help(self) -> None:
        """/* REQ-CLI-006: `wingbox --version` and `wingbox --help` must print and exit with 0. */"""
        for arguments, expected in ((["--version"], "wingbox "), (["--help"], "wingbox build [PATH]")):
            output = io.StringIO()
            with redirect_stdout(output):
                status = wingbox_cli.main(arguments)
            self.assertEqual(0, status)
            self.assertIn(expected, output.getvalue())


class ClosedPipeTest(unittest.TestCase):
    def test_a_reader_that_closes_the_pipe_ends_the_command_quietly(self) -> None:
        """/* REQ-CLI-007: when the reader of the output closes the pipe (`wingbox analyze x | head`), the command must end with status 0 and no traceback. */"""

        class _Script:
            @staticmethod
            def main(_arguments: list[str]) -> int:
                raise BrokenPipeError()

        original = wingbox_cli.load_script
        wingbox_cli.load_script = lambda _name: _Script
        try:
            status = wingbox_cli.main(["analyze", "src/app"])
        finally:
            wingbox_cli.load_script = original

        self.assertEqual(0, status)


if __name__ == "__main__":
    unittest.main()
