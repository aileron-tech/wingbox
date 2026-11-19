"""`wingbox add`: kept for scripts written for 0.12. The parsers of every language are installed with the
package, so the command says there is nothing to add, exits 0, opens no connection and writes nothing."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import add, cli  # noqa: E402
from wingbox.facts import fact_sexp  # noqa: E402

LANGUAGES = cli.load_script("lang").LANGUAGES
# What 0.12 accepted, and what it refused: every form now does nothing and exits 0.
FORMS: tuple[tuple[str, ...], ...] = (
    (), ("csharp",), ("java", "go", "ruby"), ("x", "--from", "FILE"), ("php", "--from=FILE"), ("kotlin",),
    ("--no-such-option",),
)


@dataclass(frozen=True)
class Dependency:
    distribution: str
    version: str

    @property
    def requirement(self) -> str:
        return f"{self.distribution}=={self.version}"


@dataclass(frozen=True)
class Language:
    name: str
    dependencies: tuple[Dependency, ...]


def run_add(*arguments: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = cli.main(["add", *arguments])
    return status, out.getvalue(), err.getvalue()


class AddCommandTest(unittest.TestCase):
    def test_every_form_of_the_command_says_there_is_nothing_to_add_and_exits_0(self) -> None:
        """/* REQ-CLI-037: `wingbox add`, `wingbox add csharp`, `wingbox add java go ruby`, `wingbox add x --from FILE`, an unknown language and an unknown option all exit 0 and print one line: the parsers of every language are installed with Wingbox since this version, there is nothing to add; with --sexp the line is one fact, `(nothing-to-add ...)`; nothing goes to stderr; `wingbox add --help` prints the usage; `wingbox --help` keeps one line that names the command as kept for compatibility. */"""
        for form in FORMS:
            with self.subTest(form=form):
                status, out, err = run_add(*form)
                self.assertEqual((0, ""), (status, err))
                self.assertEqual(1, len(out.splitlines()), out)
                self.assertIn("parsers of every language are installed with Wingbox", out)
                self.assertIn("nothing to add", out.lower())
                status, out, err = run_add(*form, "--sexp")
                self.assertEqual((0, ""), (status, err))
                self.assertEqual(1, len(out.splitlines()), out)
                self.assertTrue(out.startswith("(nothing-to-add "), out)
        status, out, _err = run_add("--help")
        self.assertEqual(0, status)
        self.assertIn("usage: wingbox add", out)
        named = [line for line in (cli.__doc__ or "").splitlines() if "wingbox add" in line]
        self.assertEqual(1, len(named), named)
        self.assertIn("compatibility", named[0])

    def test_list_says_for_each_language_whether_its_parser_is_installed(self) -> None:
        """/* REQ-CLI-038: `wingbox add --list` prints one fact per language before the line that there is nothing to add: `parser-installed` from the environment when every pinned package of its parser is there (built-in for a language parsed without one), else `parser-missing` with what is wrong (absent, or another version) and the command that installs it: the pip extra of the language, or a reinstall of Wingbox for a language without an extra. It exits 0 either way. */"""
        languages = (
            Language("python", ()),
            Language("csharp", (Dependency("tree-sitter", "0.25.2"), Dependency("tree-sitter-c-sharp", "0.23.5"))),
            Language("dart", (Dependency("tree-sitter", "0.25.2"), Dependency("tree-sitter-dart", "0.1.0"))),
            Language("go", (Dependency("tree-sitter", "0.25.2"), Dependency("tree-sitter-go", "0.25.0"))),
            Language("rust", (Dependency("tree-sitter", "0.25.2"), Dependency("tree-sitter-rust", "0.24.2"))),
        )
        installed = {"tree-sitter": "0.25.2", "tree-sitter-c-sharp": "0.23.5", "tree-sitter-go": "0.24.0"}
        facts = add.listed(languages, installed.get, ("csharp", "dart", "go"))
        self.assertEqual(
            [
                "(parser-installed python :from built-in)",
                "(parser-installed csharp :package tree-sitter-c-sharp==0.23.5 :from environment)",
                '(parser-missing dart :package tree-sitter-dart==0.1.0 :reason "tree-sitter-dart==0.1.0 (missing)" '
                ':instead "pip install \\"wingbox[dart]\\"")',
                '(parser-missing go :package tree-sitter-go==0.25.0 :reason "tree-sitter-go==0.25.0 (installed 0.24.0)" '
                ':instead "pip install \\"wingbox[go]\\"")',
                '(parser-missing rust :package tree-sitter-rust==0.24.2 :reason "tree-sitter-rust==0.24.2 (missing)" '
                ':instead "pip install --force-reinstall wingbox")',
            ],
            [fact_sexp(fact) for fact in facts],
        )
        text = add.render_text(facts)
        self.assertIn("csharp", text.splitlines()[1])
        self.assertIn("not installed", text.splitlines()[2])
        self.assertIn('pip install "wingbox[dart]"', text)
        status, out, err = run_add("--list", "--sexp")
        self.assertEqual((0, ""), (status, err))
        lines = out.splitlines()
        self.assertEqual([language.name for language in LANGUAGES], [line.split()[1].rstrip(")") for line in lines[:-1]])
        self.assertTrue(all(line.startswith(("(parser-installed ", "(parser-missing ")) for line in lines[:-1]), out)
        self.assertTrue(lines[-1].startswith("(nothing-to-add "), out)
        status, out, _err = run_add("--list")
        self.assertEqual(0, status)
        self.assertEqual(len(LANGUAGES) + 1, len([line for line in out.splitlines() if not line.startswith(" ")]), out)

    def test_the_command_writes_nothing_and_loads_no_network_module(self) -> None:
        """/* REQ-SEC-011: `wingbox add` in every form, --list included, run in a project with a temporary home folder that holds a parser folder of 0.12: creates, changes and removes no file in the home folder or in the project, and finishes without loading urllib.request, http.client, ssl, ftplib or smtplib. */"""
        script = (
            "import sys\n"
            "from wingbox import cli\n"
            f"forms = {FORMS + (('--list',), ('--list', '--sexp'), ('csharp', '--sexp'))!r}\n"
            "statuses = [cli.main(['add', *form]) for form in forms]\n"
            "network = ('urllib.request', 'urllib.error', 'http.client', 'ssl', 'ftplib', 'smtplib')\n"
            "print('STATUSES', sorted(set(statuses)), 'LOADED', sorted(name for name in network if name in sys.modules))\n"
        )

        def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
            return {
                path.relative_to(folder).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in sorted(folder.rglob("*"))
            }

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            home, project = base / "home", base / "project"
            kept = home / ".wingbox" / "parsers" / "linux-x86_64" / "tree-sitter-c-sharp-0.23.5" / "tree_sitter_c_sharp"
            kept.mkdir(parents=True)
            (kept / "__init__.py").write_text("", encoding="utf-8")
            (project / ".wingbox").mkdir(parents=True)
            (project / ".wingbox" / "graph-skipped.sexp").write_text(
                '(skipped-language csharp :files 1 :add "wingbox add csharp")\n', encoding="utf-8"
            )
            (project / "Service.cs").write_text("public class Service { }\n", encoding="utf-8")
            (project / "FILE").write_bytes(b"not a wheel")
            before = snapshot(base)
            environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE="1")
            environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
            done = subprocess.run([sys.executable, "-c", script], cwd=project, env=environment, capture_output=True, text=True)
            self.assertIn("STATUSES [0] LOADED []", done.stdout, done.stderr)
            self.assertEqual("", done.stderr)
            self.assertEqual(before, snapshot(base))


if __name__ == "__main__":
    unittest.main()
