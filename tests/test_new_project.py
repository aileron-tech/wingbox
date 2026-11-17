"""`wingbox init` in an empty folder: language, styles, $HOME defaults, monorepo siblings."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli  # noqa: E402


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _run(arguments: list[str], stdin=None, home: str = "") -> tuple[int, str]:
    output = io.StringIO()
    saved_stdin, saved_home = sys.stdin, {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    sys.stdin = stdin if stdin is not None else io.StringIO("")
    if home:
        os.environ["HOME"] = os.environ["USERPROFILE"] = home   # the home folder on POSIX and on Windows
    try:
        with redirect_stdout(output):
            status = cli.main(arguments)
    finally:
        sys.stdin = saved_stdin
        for name, value in saved_home.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return status, output.getvalue()


class NewProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.base / "shop"
        self.root.mkdir()

    def test_without_a_terminal_init_prints_the_choices_and_asks_for_flags(self) -> None:
        """/* REQ-NEW-001: in an empty folder without a terminal, init never blocks: it prints the styles that fit the language (best first, with name and source) as S-expressions and the flags it needs, writes nothing and exits with 2. */"""
        status, text = _run(["init", str(self.root), "--language", "typescript"], home=str(self.home))
        self.assertEqual(2, status, text)
        self.assertIn('(init-choice 1 layered :language typescript :from library :name "', text)
        self.assertIn("(init-choice ", text)
        self.assertIn(" route-tree ", text)
        self.assertNotIn(" mvc ", text)
        self.assertIn('(init-needs "--language typescript --style STYLE --yes")', text)
        self.assertFalse((self.root / ".wingbox").exists())

    def test_flags_write_the_style_and_its_full_target_layout_and_scaffold_creates_only_folders(self) -> None:
        """/* REQ-NEW-002: with --language and --style init writes .wingbox/config.yaml (and the .wingbox/.gitignore of the build) with the chosen style and every folder of its target layout proposed; it creates no folder unless --scaffold, which creates the empty target folders and no file. */"""
        status, text = _run(["init", str(self.root), "--language", "python", "--style", "hexagonal", "--yes"], home=str(self.home))
        self.assertIn("new project: hexagonal (--style), python, container", text)
        self.assertIn("next: wingbox learn settles the open points", text)
        self.assertNotIn("edit it", text)
        self.assertEqual(0, status, text)
        config = (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("    style: hexagonal   # chosen: --style", config)
        self.assertIn("- {layer: domain, block: entities, folder: src/domain/entities, proposed: true}", config)
        self.assertEqual({self.root / ".wingbox", self.root / ".wingbox" / "config.yaml", self.root / ".wingbox" / ".gitignore"},
                         set(self.root.rglob("*")))
        status, text = _run(["init", str(self.root), "--language", "python", "--style", "hexagonal", "--yes", "--force", "--scaffold", "--sexp"],
                            home=str(self.home))
        self.assertEqual(0, status, text)
        self.assertTrue((self.root / "src" / "domain" / "entities").is_dir())
        self.assertTrue((self.root / "src" / "domain" / "value_objects").is_dir())   # a Python package name
        self.assertTrue((self.root / "src" / "adapters" / "in").is_dir())
        self.assertEqual([], [p for p in self.root.rglob("*") if p.is_file() and ".wingbox" not in p.parts])

    def test_interactive_init_asks_the_language_and_the_style(self) -> None:
        """/* REQ-NEW-003: on a terminal, init asks the language, lists the styles with numbers and writes the chosen one after a yes. */"""
        status, text = _run(["init", str(self.root)], stdin=_Terminal("typescript\n2\ny\n"), home=str(self.home))
        self.assertEqual(0, status, text)
        self.assertIn("Language", text)
        self.assertIn("  1. layered", text)
        config = (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("style: onion-clean", config)

    def test_home_defaults_and_home_styles(self) -> None:
        """/* REQ-NEW-004: $HOME/.wingbox/config.yaml names a favourite style per language, offered first with :from home; a style file in $HOME/.wingbox/styles replaces the built-in one of the same name. */"""
        (self.home / ".wingbox" / "styles").mkdir(parents=True)
        (self.home / ".wingbox" / "config.yaml").write_text("defaults:\n  typescript: hexagonal\n", encoding="utf-8")
        builtin = (REPO / "scripts" / "architecture" / "styles" / "layered.toml").read_text(encoding="utf-8")
        (self.home / ".wingbox" / "styles" / "layered.toml").write_text(
            builtin.replace('name = "Layered architecture (presentation, service, data, model)"', 'name = "Our layered house style"'),
            encoding="utf-8",
        )
        status, text = _run(["init", str(self.root), "--language", "typescript"], home=str(self.home))
        self.assertEqual(2, status, text)
        self.assertIn("(init-choice 1 hexagonal :language typescript :from home ", text)
        self.assertIn('layered :language typescript :from library :name "Our layered house style"', text)

    def test_a_new_part_in_a_monorepo_takes_the_style_of_its_siblings_before_home(self) -> None:
        """/* REQ-NEW-005: a new empty folder inside a repository whose .wingbox/config.yaml has parts of the same language is offered their style first ("same as packages/a"), before the $HOME favourite: the precedence is declared rules > project config > siblings > home > the library. */"""
        repo = self.base / "mono"
        for name in ("a", "b"):
            (repo / "packages" / name).mkdir(parents=True)
            (repo / "packages" / name / "package.json").write_text("{}\n", encoding="utf-8")
        (repo / ".wingbox").mkdir()
        (repo / ".wingbox" / "config.yaml").write_text(
            "parts:\n  packages/a:\n    style: onion-clean\n  packages/b:\n    style: onion-clean\n", encoding="utf-8"
        )
        (self.home / ".wingbox").mkdir()
        (self.home / ".wingbox" / "config.yaml").write_text("defaults:\n  typescript: hexagonal\n", encoding="utf-8")
        new = repo / "packages" / "c"
        new.mkdir()
        status, text = _run(["init", str(new), "--language", "typescript"], home=str(self.home))
        self.assertEqual(2, status, text)
        self.assertIn('(init-choice 1 onion-clean :language typescript :from siblings :note "same as packages/a, packages/b"', text)
        self.assertIn("(init-choice 2 hexagonal :language typescript :from home", text)

    def test_a_new_package_in_a_monorepo_becomes_a_part_of_the_root_config(self) -> None:
        """/* REQ-NEW-006: init in an empty package of a repository with a root .wingbox/config.yaml adds the package as a part of that file (a component under packages/), infers the language from its package.json, writes no nested .wingbox, and compare from the package reads the root config. */"""
        repo = self.base / "mono"
        for name in ("a", "b"):
            (repo / "packages" / name / "src" / "routes").mkdir(parents=True)
            (repo / "packages" / name / "package.json").write_text("{}\n", encoding="utf-8")
            (repo / "packages" / name / "src" / "routes" / "r.ts").write_text("export const r = 1;\n", encoding="utf-8")
        (repo / ".wingbox").mkdir()
        (repo / ".wingbox" / "config.yaml").write_text(
            "parts:\n  packages/a:\n    style: layered\n  packages/b:\n    style: layered\n", encoding="utf-8"
        )
        new = repo / "packages" / "c"
        new.mkdir()
        (new / "package.json").write_text("{}\n", encoding="utf-8")
        status, text = _run(["init", str(new), "--yes"], home=str(self.home))
        self.assertEqual(0, status, text)
        config = (repo / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("  packages/c:\n    level: component\n    style: layered   # chosen: siblings", config)
        self.assertFalse((new / ".wingbox").exists())
        cwd = os.getcwd()
        os.chdir(new)
        try:
            status, text = _run(["compare", "--sexp"])
        finally:
            os.chdir(cwd)
        self.assertIn('(compare-part "packages/a" :language typescript :style layered', text)


if __name__ == "__main__":
    unittest.main()
