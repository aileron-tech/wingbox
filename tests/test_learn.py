"""`wingbox learn`: a task for the coding agent, and `wingbox learn --apply` for its answer."""
import fnmatch
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


def _run(arguments: list[str], cwd: Path, stdin: str = "") -> tuple[int, str]:
    output = io.StringIO()
    saved_stdin, saved_cwd = sys.stdin, os.getcwd()
    sys.stdin = io.StringIO(stdin)
    os.chdir(cwd)
    try:
        with redirect_stdout(output):
            status = cli.main(arguments)
    finally:
        sys.stdin = saved_stdin
        os.chdir(saved_cwd)
    return status, output.getvalue()


class LearnTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = root = Path(directory.name).resolve()
        (root / "package.json").write_text('{"name": "shop"}\n', encoding="utf-8")
        files = {f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
                 for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)) for i in range(count)}
        files["src/connectors/pay.ts"] = "import { repositories0 } from '../repositories/repositories0';\nexport const pay = repositories0;\n"
        files["src/connectors/mail.ts"] = "export const mail = 1;\n"
        files["src/services/services0.ts"] = "import { pay } from '../connectors/pay';\nexport const services0 = pay;\n"
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        self.assertEqual(0, _run(["build", str(root)], root)[0])

    def test_the_task_names_style_layers_unplaced_folders_files_imports_and_the_format(self) -> None:
        """/* REQ-LEARN-001: `wingbox learn` prints, per part, the closest style with score and runner-up, its layers with their meaning, the folders it places, each folder it does not place with real files, imports by layer and a name guess, then the questions, the rules and the exact answer format and the apply command. */"""
        status, text = _run(["learn", "--sexp"], self.root)
        self.assertEqual(0, status, text)
        self.assertIn('(learn-part "." :style layered :score 1.00 ', text)
        self.assertIn('(learn-layer "." infra :means "', text)
        self.assertIn('(learn-placed "." api "routes")', text)
        self.assertIn('(learn-folder "." "connectors" :files "src/connectors/mail.ts src/connectors/pay.ts" :imports "infra 1" :imported-by "service 1"', text)
        self.assertIn('(learn-answer-format "(layer <part> <folder> <layer-name> :because \\"<one sentence citing the files read>\\")")', text)
        plain = _run(["learn"], self.root)[1]
        self.assertIn("connectors/", plain)
        self.assertIn("wingbox learn --apply", plain)
        self.assertIn("unknown", plain)

    def test_a_valid_answer_is_written_as_learned_and_reported(self) -> None:
        """/* REQ-LEARN-002: a valid answer is written into .wingbox/config.yaml with `learned by agent: <because>`, logged in decisions.sexp, and the result names what changed and the violation counts after the rebuild. */"""
        answer = '(layer "." connectors infra :because "pay.ts wraps a payment client and imports a repository")\n'
        status, text = _run(["learn", "--apply"], self.root, answer)
        self.assertEqual(0, status, text)
        config = (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("connectors: learned by agent: pay.ts wraps a payment client and imports a repository", config)
        self.assertIn('(learned-by-agent "." "connectors" infra', (self.root / ".wingbox" / "decisions.sexp").read_text(encoding="utf-8"))
        self.assertIn("learned connectors/ -> infra", text)
        self.assertIn("violations: 0", text)

    def test_an_invalid_answer_is_rejected_whole_with_every_error(self) -> None:
        """/* REQ-LEARN-003: an answer with an unknown layer, a folder the part does not have, a folder twice, an unknown style or a malformed line is rejected as a whole, with one error per problem, and nothing is written. */"""
        answer = "\n".join([
            '(layer "." connectors kitchen :because "x")',
            '(layer "." nowhere infra :because "x")',
            '(layer "." models domain :because "x")',
            '(layer "." models domain :because "x")',
            '(style "." spaghetti)',
            '(layer "." connectors',
        ]) + "\n"
        status, text = _run(["learn", "--apply", "--sexp"], self.root, answer)
        self.assertEqual(2, status, text)
        for expected in ('kitchen is not a layer of layered', 'nowhere is not a folder of part .', 'models twice',
                         'spaghetti is not a style', 'line 6: not one complete S-expression'):
            self.assertIn(expected, text)
        self.assertFalse((self.root / ".wingbox" / "config.yaml").exists())

    def test_an_answer_never_overwrites_an_entry_of_the_user(self) -> None:
        """/* REQ-LEARN-004: a folder the user already maps in .wingbox/config.yaml keeps its layer; the answer for it is reported as kept. */"""
        (self.root / ".wingbox" / "config.yaml").write_text(
            "parts:\n  .:\n    style: layered\n    layers:\n      api: [routes]\n      shared: [connectors]\n", encoding="utf-8")
        status, text = _run(["learn", "--apply"], self.root, '(layer "." connectors infra :because "pay.ts is a client")\n')
        self.assertEqual(0, status, text)
        self.assertIn("kept connectors/ -> shared (stated in .wingbox/config.yaml)", text)
        self.assertIn("shared: [connectors]", (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8"))

    def test_what_learn_saves_in_dot_wingbox_and_reuses(self) -> None:
        """/* REQ-LEARN-005: an accepted answer is appended to .wingbox/learned.sexp with date, mode, agent, style, score and each because; .wingbox/.gitignore keeps the generated files out (index, cache) and leaves config.yaml, learned.sexp, decisions.sexp and baseline.sexp visible to git; the next `learn` lists the folder as known instead of asking again, unless --again; a rejected answer saves nothing. */"""
        self.assertEqual(2, _run(["learn", "--apply"], self.root, '(layer "." nowhere infra :because "x")\n')[0])
        self.assertFalse((self.root / ".wingbox" / "learned.sexp").exists())
        env = dict(os.environ)
        os.environ["CLAUDECODE"] = "1"
        try:
            self.assertEqual(0, _run(["learn", "--apply"], self.root, '(layer "." connectors infra :because "pay.ts is a client")\n')[0])
        finally:
            os.environ.clear()
            os.environ.update(env)
        learned = (self.root / ".wingbox" / "learned.sexp").read_text(encoding="utf-8")
        self.assertRegex(learned, r'\(learned-answer "\." :at \d{4}-\d{2}-\d{2} :mode agent :agent "Claude Code" :style layered :score ')
        self.assertIn('(learned-entry "." "connectors" infra :because "pay.ts is a client" :files 2)', learned)
        patterns = [line for line in (self.root / ".wingbox" / ".gitignore").read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#")]
        for generated in ("index.sexp", "index.uses.sexp", "cache.json", "REPORT.md", "map.html"):
            self.assertTrue(any(fnmatch.fnmatchcase(generated, pattern) for pattern in patterns), generated)
        for decided in ("config.yaml", "learned.sexp", "decisions.sexp", "baseline.sexp"):
            self.assertFalse(any(fnmatch.fnmatchcase(decided, pattern) for pattern in patterns), decided)
        task = _run(["learn", "--sexp", "--agent"], self.root)[1]
        self.assertIn('(learn-known "." "connectors" infra :because "pay.ts is a client")', task)
        self.assertNotIn('(learn-folder "." "connectors"', task)
        self.assertIn('(learn-folder "." "connectors"', _run(["learn", "--sexp", "--agent", "--again"], self.root)[1])

    def test_a_person_in_a_terminal_answers_a_questionnaire_and_may_open_the_config(self) -> None:
        """/* REQ-LEARN-006: run by a person (--human or a terminal), learn asks the style of each part among its candidates (score and a one-line description; Enter keeps the closest), then one question per open folder with its files and imports (an invalid layer is asked again); it records the answers like an agent's, and at the end offers to open .wingbox/config.yaml in $VISUAL or $EDITOR. --yes takes the closest style and the guesses without asking and opens nothing. */"""
        marker = self.root / "opened.txt"
        saved = dict(os.environ)
        os.environ["EDITOR"] = f"touch {marker}"
        os.environ.pop("VISUAL", None)
        try:
            status, text = _run(["learn", "--human"], self.root, "\nkitchen\ninfra\ny\n")
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(0, status, text)
        self.assertIn("learn: interactive (--human)", text)
        self.assertIn("Part .: which style does it follow?", text)
        self.assertRegex(text, r"1\) layered 1\.00 - Code is split by technical role into horizontal layers; an upper layer calls the layer below it\.")
        self.assertIn("folder connectors/  files: src/connectors/mail.ts src/connectors/pay.ts", text)
        self.assertIn("one of: ", text)
        config = (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertRegex(config, r"infra: \[[^]]*connectors[^]]*\]")
        self.assertIn("connectors: answered by a person: operator: answered in the questionnaire", config)
        self.assertIn(":mode human", (self.root / ".wingbox" / "learned.sexp").read_text(encoding="utf-8"))
        self.assertTrue(marker.is_file())
        marker.unlink()
        status, text = _run(["learn", "--human", "--yes", "--again"], self.root)
        self.assertEqual(0, status, text)
        self.assertNotIn("which style", text)
        self.assertFalse(marker.exists())

    def test_align_learn_at_a_terminal_runs_the_questionnaire(self) -> None:
        """/* REQ-LEARN-011: `wingbox align --learn` run by a person (--human or a terminal) runs the questionnaire of learn; run by an agent or without a terminal it keeps writing what it derives. */"""
        status, text = _run(["align", "--learn", "--human"], self.root, "\n\nn\n")
        self.assertEqual(0, status, text)
        self.assertIn("Part .: which style does it follow?", text)
        status, text = _run(["align", "--learn", "--sexp"], self.root)
        self.assertEqual(0, status, text)
        self.assertNotIn("which style", text)

    def test_the_task_tells_the_agent_to_ask_the_operator(self) -> None:
        """/* REQ-LEARN-012: the learn task says that when the code does not decide, the agent asks the operator in its chat and passes the answer on (:because "operator: ..."), and never guesses. */"""
        task = _run(["learn", "--sexp", "--agent"], self.root)[1]
        self.assertIn("(learn-rule \"If you cannot decide from the code", task)
        self.assertIn("ask the operator in your chat and pass the answer on", task)
        self.assertIn("Never guess", _run(["learn", "--agent"], self.root)[1])

    def test_the_config_starts_with_instructions_rewritten_on_every_write(self) -> None:
        """/* REQ-LEARN-013: .wingbox/config.yaml starts with a header of instructions (each key, the rule that decides a file's layer, how to move a folder, baseline, run build and boundaries after a change, run align --learn when unsure); every write puts the current header on top once and keeps the parts below it. */"""
        self.assertEqual(0, _run(["init"], self.root)[0])
        config = (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertTrue(config.startswith("# Wingbox architecture config. Read this before you change the file"))
        for phrase in ("# - layers: layer -> folders.", "# - The layer of a file, first rule that applies: a test file is in no layer",
                       "# - To move a folder to another layer", ".wingbox/baseline.sexp",
                       "run `wingbox build` and `wingbox boundaries`", "Run `wingbox align --learn`"):
            self.assertIn(phrase, config)
        status, text = _run(["learn", "--apply"], self.root, '(layer "." connectors infra :because "pay.ts is a client")\n')
        self.assertEqual(0, status, text)
        again = (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertEqual(1, again.count("# Wingbox architecture config."))
        self.assertIn("api: [routes]", again)
        from scripts.architecture.config import with_header
        from scripts.architecture.library import load_library

        rewritten = with_header("# old words\n# more\nparts:\n  .:\n    style: layered\n", load_library(), "2026-11-17")
        self.assertNotIn("old words", rewritten)
        self.assertTrue(rewritten.endswith("parts:\n  .:\n    style: layered\n"))

    def test_nested_folders_that_break_rules_are_asked_and_an_answer_relabels_them(self) -> None:
        """/* REQ-LEARN-009: the task also lists nested folders whose files break a rule or rest on a weak label, at the deepest folder holding the files, most breaks first and capped; an answer for such a folder path relabels exactly that folder and its breaks go away. */"""
        legacy = self.root / "src" / "models" / "legacy"
        legacy.mkdir()
        (legacy / "old.ts").write_text("import { routes0 } from '../../routes/routes0';\nexport const old = routes0;\n", encoding="utf-8")
        self.assertEqual(0, _run(["build", str(self.root)], self.root)[0])
        task = _run(["learn", "--sexp", "--agent"], self.root)[1]
        self.assertIn('(learn-folder "." "models/legacy" :files "src/models/legacy/old.ts"', task)
        self.assertIn(":breaks 1", task)
        status, text = _run(["learn", "--apply"], self.root,
                            '(layer "." models/legacy api :because "old.ts re-exports a route constant for the API")\n')
        self.assertEqual(0, status, text)
        self.assertIn("rebuilt: violations: 0", text)
        self.assertNotIn("[wingbox]", text)
        self.assertIn('"models/legacy"', (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8").replace("[", '"').replace("]", '"').replace(", ", '" "'))

    def test_apply_prints_no_build_lines_of_the_parser_workers(self) -> None:
        """/* REQ-LEARN-010: `wingbox learn --apply` rebuilds quietly: no [code-graph] lines of the build or of its parser processes reach stdout or stderr unless --verbose. */"""
        import subprocess

        env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
        answer = '(layer "." connectors infra :because "pay.ts is a client")\n'
        done = subprocess.run([sys.executable, "-m", "wingbox.cli", "learn", "--apply"], cwd=self.root, input=answer,
                              capture_output=True, text=True, env=env, check=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn("[code-graph]", done.stdout + done.stderr)
        self.assertIn("learned connectors/ -> infra", done.stdout)


class LearnModeTest(unittest.TestCase):
    def test_flags_then_agent_variables_then_terminals_decide(self) -> None:
        """/* REQ-LEARN-007: --agent and --human win; then CLAUDECODE=1 or GEMINI_CLI=1 mean an agent; then stdin or stdout without a terminal means an agent and a terminal means a person; the reason is returned to be printed. */"""
        from scripts.architecture.agent_learn import learn_mode

        self.assertEqual(("human", "--human"), learn_mode(("--human",), {"CLAUDECODE": "1"}, False, False)[:2])
        self.assertEqual(("agent", "--agent"), learn_mode(("--agent",), {}, True, True)[:2])
        self.assertEqual(("agent", "CLAUDECODE=1", "Claude Code"), tuple(learn_mode((), {"CLAUDECODE": "1"}, True, True)))
        self.assertEqual(("agent", "GEMINI_CLI=1"), learn_mode((), {"GEMINI_CLI": "1"}, True, True)[:2])
        self.assertEqual(("agent", "stdin or stdout is not a terminal"), learn_mode((), {}, True, False)[:2])
        self.assertEqual(("human", "terminal"), learn_mode((), {}, True, True)[:2])

    def test_a_pseudo_terminal_is_a_person_and_a_pipe_an_agent(self) -> None:
        """/* REQ-LEARN-008: the terminal test uses the real file descriptors: a pseudo-terminal counts as a person, a pipe as an agent. */"""
        import pty

        from scripts.architecture.agent_learn import learn_mode

        main, follower = pty.openpty()
        read_end, write_end = os.pipe()
        try:
            self.assertEqual("human", learn_mode((), {}, os.isatty(follower), os.isatty(follower)).mode)
            self.assertEqual("agent", learn_mode((), {}, os.isatty(follower), os.isatty(write_end)).mode)
        finally:
            for fd in (main, follower, read_end, write_end):
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
