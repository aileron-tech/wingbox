"""Documents that say what the code does: the skill, the notation reference, the contributor guide, the limits page."""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli  # noqa: E402

SKILL = REPO / "SKILL.md"
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)


def _wingbox(arguments: list[str], cwd: Path) -> str:
    """What `wingbox <arguments>` prints in a folder (standard output and standard error)."""
    environment = dict(os.environ, WINGBOX_JOBS="1", WINGBOX_AUTO_REFRESH="0")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    done = subprocess.run([sys.executable, "-m", "wingbox", *arguments], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", env=environment, timeout=300)
    return done.stdout + done.stderr


def _shop(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "shop").mkdir()
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "pricing.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    (root / "shop" / "checkout.py").write_text(
        "from shop.pricing import total\n\n\ndef pay(items):\n    return total(items)\n", encoding="utf-8"
    )


def _shell_lines(text: str) -> list[str]:
    """The command lines of the fenced shell blocks of a document, without their comments."""
    lines = []
    for language, body in FENCE.findall(text):
        if language in ("bash", "sh", "shell", ""):
            lines += [line.split("  #", 1)[0].strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return lines


class SkillTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        _shop(cls.root)
        built = _wingbox(["build", "."], cls.root)
        assert (cls.root / ".wingbox" / "index.sexp").is_file(), built

    def test_the_examples_of_the_skill_run_the_command_not_scripts_it_does_not_hold(self) -> None:
        """/* REQ-DOC-9970: an installed skill holds SKILL.md and references/ and no script, so every example of SKILL.md is written as `wingbox <command>`: no line of a shell block runs `python3` on a script or names SKILL_ROOT or TARGET_ROOT, the script paths stand only in the section "Scripts", which says that they are in a clone of the repository, and every `wingbox <name>` of a shell block is a command the program knows (the commands of its help and the query commands it lists for an unknown one). */"""
        text = SKILL.read_text(encoding="utf-8")
        lines = _shell_lines(text)
        self.assertTrue(lines)
        self.assertEqual([], [line for line in lines if re.search(r"\bpython3?\b|SKILL_ROOT|TARGET_ROOT|scripts/", line)])
        self.assertNotIn("SKILL_ROOT", text)
        self.assertNotIn("This skill bundles", text)
        note = text[text.index("\n## Scripts\n"):].split("\n## ", 2)[1]
        self.assertIn("in a clone of the Wingbox repository only", " ".join(note.split()))
        outside = text.replace(note, "")
        self.assertEqual([], re.findall(r"scripts/\w+\.py", outside))

        usage = io.StringIO()
        with contextlib.redirect_stdout(usage):
            cli.main(["--help"])
        known = set(re.findall(r"^\s+wingbox ([a-z_]+)", usage.getvalue(), re.M))
        listed = re.search(r"; Commands: (.*?)(?:\n[^;]|\Z)", _wingbox(["no_such_command"], self.root), re.S)
        self.assertIsNotNone(listed)
        known |= set(re.findall(r"[a-z_]+", listed[1].replace("; ", "")))
        self.assertGreater(len(known), 30, sorted(known))
        used = {line.split()[1] for line in lines if line.startswith("wingbox ") and len(line.split()) > 1}
        self.assertGreaterEqual(len(used), 8, sorted(used))
        self.assertEqual(set(), {name for name in used if not name.startswith("-")} - known)

    def test_the_graph_file_names_modules_by_short_id_and_the_documents_say_so(self) -> None:
        """/* REQ-DOC-9971: .wingbox/index.sexp maps every module path to a short ID once, in its `(paths ...)` form, and its facts name modules by those IDs, so a text search of the file for a path finds no fact; SKILL.md therefore gives no `rg` line over the graph file for a module, and its lookups work: `module <id> --lines` prints the import with both paths and its line, `rdeps` names the importer, `search <fact> --lines` prints the stored fact with short IDs, and `module <short id>` names the module of one; references/sexp-notation.md states the mapping. */"""
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        facts = [line for line in graph.splitlines() if line.startswith("(imports ")]
        self.assertEqual(1, len(facts), facts)
        self.assertNotIn("shop/", facts[0])
        self.assertIsNone(re.search(r"imports .* shop/pricing", graph))
        source, target = facts[0].split()[1:3]

        text = SKILL.read_text(encoding="utf-8")
        lines = _shell_lines(text)
        self.assertEqual([], [line for line in lines if line.startswith(("rg ", "grep ")) and "index.sexp" in line])
        for example in ("wingbox module ", "wingbox rdeps ", "wingbox deps ", "wingbox entity ", "wingbox grep ", 'wingbox search "data-flow" --lines'):
            self.assertTrue(any(line.startswith(example) for line in lines), example)
        self.assertIn("short ID", text[text.index("## Quick Reference"):].split("\n## ")[0])

        self.assertIn("(imports shop/checkout shop/pricing :line 1)", _wingbox(["module", "shop/checkout", "--lines"], self.root))
        self.assertIn("(rdep shop/pricing shop/checkout ", _wingbox(["rdeps", "shop/pricing", "1"], self.root))
        self.assertIn("(dep shop/checkout shop/pricing ", _wingbox(["deps", "shop/checkout", "1"], self.root))
        self.assertIn(facts[0], _wingbox(["search", "imports", "--lines"], self.root))
        self.assertIn("(node shop/checkout ", _wingbox(["module", source], self.root))
        self.assertIn("(node shop/pricing ", _wingbox(["module", target], self.root))
        self.assertIn('(match shop/checkout.py 5 "    return total(items)")', _wingbox(["grep", "return total"], self.root))

        notation = (REPO / "references" / "sexp-notation.md").read_text(encoding="utf-8")
        raw = notation[notation.index("## Raw Graph Facts"):].split("\n## ")[0]
        self.assertIn("short IDs, not paths", raw)
        self.assertIn("(paths", raw)


class GuideTextTest(unittest.TestCase):
    def test_the_contributor_guide_names_the_systems_the_package_supports(self) -> None:
        """/* REQ-DOC-9972: AGENTS.md, section 1, names as supported the operating systems that the classifiers of pyproject.toml declare and the matrix of tests.yml runs: macOS, Linux and Windows. */"""
        classifiers = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["classifiers"]
        declared = {item.split(" :: ")[1] for item in classifiers if item.startswith("Operating System :: ")}
        self.assertEqual({"MacOS", "Microsoft", "POSIX"}, declared)
        matrix = yaml.safe_load((REPO / ".github/workflows/tests.yml").read_text(encoding="utf-8"))["jobs"]["test"]["strategy"]["matrix"]["os"]
        self.assertEqual({"ubuntu-latest", "macos-latest", "windows-latest"}, set(matrix))
        guide = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        line = " ".join(guide[guide.index("- Supported Python:"):].split("\n## ")[0].split())
        for system in ("macOS", "Linux", "Windows"):
            self.assertIn(system, line)
        self.assertNotIn("macOS and Linux.", line)

    def test_the_limits_page_names_every_language_with_use_positions(self) -> None:
        """/* REQ-DOC-9973: the row "Use positions outside ..." of docs/assurance/known-limits.md and the sentence of the README on use positions name the languages whose parser has a use-position walk (`<family>_use_positions` in scripts/lang: Python, TypeScript with JavaScript, Rust, and C and C++ through the cpp parser), and no other. */"""
        families = sorted(path.parent.name for path in (REPO / "scripts" / "lang").glob("*/parser.py")
                          if re.search(rf"^def {path.parent.name}_use_positions\(", path.read_text(encoding="utf-8"), re.M))
        self.assertEqual(["cpp", "python", "rust", "typescript"], families)
        wanted = "Python, TypeScript, JavaScript, Rust, C and C++"
        limits = (REPO / "docs" / "assurance" / "known-limits.md").read_text(encoding="utf-8")
        row = next(line for line in limits.splitlines() if line.startswith("| Use positions outside "))
        self.assertTrue(row.startswith(f"| Use positions outside {wanted} |"), row[:120])
        readme = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
        self.assertIn(f"{wanted} have use positions", readme)

    def test_the_readme_names_the_one_option_under_which_code_may_run(self) -> None:
        """/* REQ-DOC-9974: where the README says that building and querying open no network connection and that the code of a repository is never run, it names the exception: `wingbox build --lsp`, off by default, starts a language server, which can run the build of the project and reach a package registry; the bullet names the command of every language server a language adapter defines, and SKILL.md says the same where it shows `--lsp`. */"""
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        section = readme[readme.index("## Runs fully locally"):].split("\n## ")[0]
        network = " ".join(section[section.index("- **No network.**"):].split("\n- **")[0].split())
        self.assertIn("`--lsp`", network)
        never = " ".join(section[section.index("- **Your code is read, never run.**"):].split("\n- **")[0].split())
        for words in ("`wingbox build --lsp`, off by default", "can run the build of the project", "package registry"):
            self.assertIn(words, never)
        servers = {" ".join(part for part in language.lsp_command if not part.startswith("-"))
                   for language in cli.load_script("lang").LANGUAGES if language.lsp_command}
        self.assertGreaterEqual(len(servers), 8, servers)
        self.assertEqual(set(), {server for server in servers if f"`{server}`" not in never})
        skill = SKILL.read_text(encoding="utf-8")
        rebuilding = " ".join(skill[skill.index("## Rebuilding the Graph"):].split("\n## ")[0].split())
        self.assertIn("wingbox build . --lsp", rebuilding)
        self.assertIn("can run the build of the project", rebuilding)

    def test_the_roadmap_lists_released_versions_and_the_published_check(self) -> None:
        """/* REQ-DOC-9975: every version ROADMAP.md names under "Recently shipped" is a release of CHANGELOG.md, newest first, and the roadmap says that the pull request check (the GitHub Action `aileron-tech/wingbox-action`) is published, in that list and in the bullet on GitHub and GitLab, which no longer announces it as something to come. */"""
        roadmap = (REPO / "ROADMAP.md").read_text(encoding="utf-8")
        shipped = roadmap[roadmap.index("## Recently shipped"):].split("\n## ")[0]
        named = [tuple(map(int, version.split("."))) for version in re.findall(r"^- \*\*(\d+\.\d+):\*\*", shipped, re.M)]
        released = {tuple(map(int, version.split(".")[:2])) for version in
                    re.findall(r"^## (\d+\.\d+\.\d+) - ", (REPO / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)}
        self.assertTrue(named)
        self.assertLessEqual(set(named), released)
        self.assertEqual(sorted(named, reverse=True), named)
        self.assertIn("aileron-tech/wingbox-action", shipped)
        self.assertIn("published", shipped)
        bullet = next(line for line in roadmap.splitlines() if line.startswith("- **GitHub and GitLab, in the review itself"))
        self.assertIn("published", bullet.split(":**", 1)[0])
        self.assertNotIn("before they are published", bullet)


class BenchmarkDefaultTest(unittest.TestCase):
    def test_the_container_of_the_readme_runs_the_version_whose_results_are_committed(self) -> None:
        """/* REQ-BENCH-9980: the README says that `docker build -f Dockerfile.benchmark ...` and `docker run --rm wingbox-benchmark` compare every result with the committed one, so the Wingbox that benchmarks/impact/run.sh installs when WINGBOX_SPEC is not set is the release that benchmarks/impact/results/versions.json names, and Dockerfile.benchmark, whose entry point is that script, sets no other version. */"""
        import json

        script = (REPO / "benchmarks" / "impact" / "run.sh").read_text(encoding="utf-8")
        default = re.search(r"^WINGBOX_SPEC=\$\{WINGBOX_SPEC:-wingbox==([\d.]+)\}$", script, re.M)
        self.assertIsNotNone(default)
        recorded = json.loads((REPO / "benchmarks" / "impact" / "results" / "versions.json").read_text(encoding="utf-8"))["wingbox"]
        self.assertEqual(recorded, f"wingbox {default[1]}")
        image = (REPO / "Dockerfile.benchmark").read_text(encoding="utf-8")
        self.assertIn('ENTRYPOINT ["/bench/run.sh", "/work"]', image)
        self.assertEqual([], [line for line in image.splitlines() if not line.startswith("#") and "WINGBOX_SPEC" in line])
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("compares every result with the committed one", " ".join(readme.split()))
        self.assertIn("docker run --rm wingbox-benchmark\n", readme)


if __name__ == "__main__":
    unittest.main()
