"""Review of 0.19.1, the architecture config: what `.wingbox/config.yaml` and the files next to it may
hold, what Wingbox writes there, and what it reads on the way. One test per finding."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli  # noqa: E402

from scripts.architecture import agent_learn, decisions  # noqa: E402
from scripts.architecture.advice import PartRules, Violation, fix_violations  # noqa: E402
from scripts.architecture.config import ConfigErr, ConfigOk, parse_config, plan_from_config, read_config, render_config  # noqa: E402
from scripts.architecture.declared import DeclaredLayers, NotRead, layers_from_order, read_layer_table  # noqa: E402
from scripts.architecture.detect import DeclaredFile  # noqa: E402
from scripts.architecture.facts import graph_architecture  # noqa: E402
from scripts.architecture.library import load_library  # noqa: E402
from scripts.architecture.plan import (  # noqa: E402
    DeclaredRules, LayerFolder, PartPlan, RepositoryPlan, apply_learned, declared_rules,
)
from scripts.architecture.repository import text_reader  # noqa: E402

LIBRARY = load_library()
LAYERED = """parts:
  .:
    style: layered
    layers:
      api: [routes]
      service: [services]
      infra: [repositories]
      domain: [models]
"""


def _temporary(test: unittest.TestCase) -> Path:
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    return Path(directory.name).resolve()


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _layered(root: Path, violation: bool = False) -> Path:
    """A small layered TypeScript project: routes, services, models, repositories."""
    files = {"package.json": '{"name": "shop"}\n'}
    for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)):
        for index in range(count):
            files[f"src/{folder}/{folder}{index}.ts"] = f"export const {folder}{index} = {index};\n"
    if violation:
        files["src/models/models0.ts"] = "import { routes0 } from '../routes/routes0';\nexport const models0 = routes0;\n"
    _write(root, files)
    return root


def _folders(root: Path, folders: tuple[str, ...], below: str = "src") -> Path:
    """A project whose folders hold five files each; each folder imports the one before it."""
    files = {"package.json": '{"name": "m"}\n'}
    for position, folder in enumerate(folders):
        for index in range(5):
            files[f"{below}/{folder}/m{index}.ts"] = f"export const m{index} = {index};\n"
        if position:
            files[f"{below}/{folder}/m0.ts"] = f"import {{ m1 }} from '../{folders[position - 1]}/m1';\nexport const m0 = m1;\n"
    _write(root, files)
    return root


def _cli(arguments: list[str], cwd: Path, home: Path, stdin: str = "") -> tuple[int, str, str]:
    """(exit status, stdout, stderr) of `wingbox <arguments>` run in cwd with its own home folder."""
    out, err = io.StringIO(), io.StringIO()
    saved = (sys.stdin, os.getcwd(), {name: os.environ.get(name) for name in ("HOME", "USERPROFILE", "WINGBOX_JOBS")})
    sys.stdin = io.StringIO(stdin)
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
    os.environ["WINGBOX_JOBS"] = "1"
    os.chdir(cwd)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            status = cli.main(arguments)
    finally:
        sys.stdin = saved[0]
        os.chdir(saved[1])
        for name, value in saved[2].items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return status, out.getvalue(), err.getvalue()


def _error(text: str) -> str:
    result = parse_config(text, LIBRARY)
    return result.error if isinstance(result, ConfigErr) else ""


def _part(path: str, style: str, layers: tuple = (), declared: DeclaredRules | None = None, state: str = "configured") -> PartPlan:
    return PartPlan(path=path, kind="folder", level="component", files=10, style=style, state=state, origin="config",
                    score=None, judgement=None, layers=layers, routing_root=(), declared=declared)


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _temporary(self)
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.base / "shop"
        self.root.mkdir()

    def run_cli(self, *arguments: str, cwd: Path | None = None, stdin: str = "") -> tuple[int, str, str]:
        return _cli(list(arguments), cwd or self.root, self.home, stdin)

    def config(self, root: Path | None = None) -> str:
        return ((root or self.root) / ".wingbox" / "config.yaml").read_text(encoding="utf-8")

    def assert_reads(self, text: str) -> ConfigOk:
        result = parse_config(text, LIBRARY)
        self.assertIsInstance(result, ConfigOk, getattr(result, "error", ""))
        return result


class UnreadableConfigTest(ProjectCase):
    def test_a_config_that_is_there_and_cannot_be_read_is_an_error(self) -> None:
        """/* REQ-ARCH-9400: a `.wingbox/config.yaml` that exists and cannot be read - bytes that are not UTF-8, more than the size limit, a folder in its place - is a config error that names the file and the reason; only a missing file means "no config". */"""
        from scripts.architecture.repository import MAX_TEXT_BYTES, file_reader

        self.assertIsNone(read_config(file_reader(str(self.root)), LIBRARY))
        (self.root / ".wingbox").mkdir()
        config = self.root / ".wingbox" / "config.yaml"
        config.write_bytes(b"parts:\n  .:\n    style: layered   # caf\xe9\n")
        result = read_config(file_reader(str(self.root)), LIBRARY)
        self.assertIsInstance(result, ConfigErr)
        self.assertIn(".wingbox/config.yaml cannot be read: ", result.error)
        self.assertIn("utf-8", result.error)
        config.write_bytes(b"parts: {}\n#" + b"x" * MAX_TEXT_BYTES)
        result = read_config(file_reader(str(self.root)), LIBRARY)
        self.assertIsInstance(result, ConfigErr)
        self.assertIn(f"larger than {MAX_TEXT_BYTES} bytes", result.error)
        config.unlink()
        config.mkdir()
        result = read_config(file_reader(str(self.root)), LIBRARY)
        self.assertIsInstance(result, ConfigErr)
        self.assertIn("not a regular file", result.error)

    def test_the_build_stops_on_a_config_it_cannot_read(self) -> None:
        """/* REQ-ARCH-9401: with one byte that is not UTF-8 in `.wingbox/config.yaml`, `wingbox build` prints an error with the file and the reason and exits 2; it does not fall back to detection and writes no graph. */"""
        _layered(self.root, violation=True)
        (self.root / ".wingbox").mkdir()
        (self.root / ".wingbox" / "config.yaml").write_bytes(
            b"parts:\n  .:\n    style: layered   # caf\xe9\n    layers:\n      api: [routes]\n      domain: [services, models]\n")
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(2, status, out + err)
        self.assertIn("ERROR: .wingbox/config.yaml cannot be read: ", err)
        self.assertNotIn("Architecture: detected", out)
        self.assertFalse((self.root / ".wingbox" / "index.sexp").exists())

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs a file mode that denies reading")
    def test_a_config_without_read_permission_is_an_error(self) -> None:
        """/* REQ-ARCH-9402: a `.wingbox/config.yaml` that the user may not open is a config error with the reason, as for bytes that are not UTF-8. */"""
        from scripts.architecture.repository import file_reader

        _write(self.root, {".wingbox/config.yaml": LAYERED})
        config = self.root / ".wingbox" / "config.yaml"
        config.chmod(0)
        self.addCleanup(config.chmod, 0o644)
        result = read_config(file_reader(str(self.root)), LIBRARY)
        self.assertIsInstance(result, ConfigErr)
        self.assertIn(".wingbox/config.yaml cannot be read: ", result.error)


class PartKeyTest(ProjectCase):
    def test_a_key_that_names_a_folder_of_the_repository_is_normalised(self) -> None:
        """/* REQ-ARCH-9403: a part key that names a folder inside the repository is read as that folder: `./src` and `src/` are `src`, `src/./x` is `src/x`, `./` is the root; the part keeps its files instead of being dropped. */"""
        for key, folder in (("./src", "src"), ("src/", "src"), ("src/./x", "src/x"), ("./", "."), (".", ".")):
            result = self.assert_reads(f"parts:\n  {key}:\n    style: layered\n")
            self.assertEqual([folder], [part.path for part in result.parts], key)
        config = self.assert_reads("parts:\n  ./src:\n    style: declared\n    order:\n      - core: [core]\n      - ui: [ui]\n")
        paths = tuple(f"src/{folder}/f{index}.ts" for folder in ("core", "ui") for index in range(5))
        plan = plan_from_config(config, paths, lambda path: None, LIBRARY)
        self.assertEqual(["src"], [part.path for part in plan.parts])
        self.assertEqual(10, plan.parts[0].files)
        self.assertEqual("src", plan.part_of("src/core/f0.ts").path)

    def test_a_key_that_names_no_folder_of_the_repository_is_refused(self) -> None:
        """/* REQ-ARCH-9404: a part key that cannot be read as a folder inside the repository is an error with its line: `..` between `/` or `\\`, an absolute path, a drive, a number, an empty key. */"""
        for key in ("../x", "src/../../x", "/etc", "'a\\..\\b'", "'C:/x'", "1", "''"):
            error = _error(f"parts:\n  src:\n    style: none\n  {key}:\n    style: layered\n")
            self.assertIn(".wingbox/config.yaml line 4: ", error, key)
            self.assertIn("the part must be a folder relative to the repository root", error, key)

    def test_a_listed_part_without_a_source_file_stays_in_the_plan(self) -> None:
        """/* REQ-ARCH-9405: a part that `.wingbox/config.yaml` lists and that holds no source file is not dropped: the plan keeps it with no file, names it in `empty`, and the graph gets its `arch-part` fact, so that a gate can say it was not checked. */"""
        config = self.assert_reads("parts:\n  src:\n    style: layered\n  docs/api:\n    style: hexagonal\n")
        paths = tuple(f"src/services/s{index}.ts" for index in range(6)) + ("docs/api/readme.md",)
        plan = plan_from_config(config, paths, lambda path: None, LIBRARY)
        self.assertEqual(["docs/api", "src"], [part.path for part in plan.parts])
        self.assertEqual(("docs/api",), plan.empty)
        empty = plan.parts[0]
        self.assertEqual((0, "hexagonal", "configured"), (empty.files, empty.style, empty.state))
        lines = graph_architecture(plan, LIBRARY, {}).part_lines
        self.assertTrue(any(line.startswith('(arch-part "docs/api" :style hexagonal ') and ":files 0 " in line for line in lines), lines)
        rendered = render_config(plan, LIBRARY, "2026-01-01")
        self.assertEqual(["docs/api", "src"], [part.path for part in self.assert_reads(rendered).parts])

    def test_the_build_names_a_part_without_a_source_file(self) -> None:
        """/* REQ-ARCH-9406: `wingbox build` with a part key `./src` and a listed part that holds no source file: the graph has an `arch-part` for `src` and for the empty part, and the build warns once, naming the empty part. */"""
        _folders(self.root, ("core", "ui"))
        _write(self.root, {".wingbox/config.yaml": "parts:\n  ./src:\n    style: declared\n    order:\n      - core: [core]\n"
                                                  "      - ui: [ui]\n  docs/api:\n    style: layered\n"})
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(0, status, out + err)
        self.assertIn("WARNING: .wingbox/config.yaml: part docs/api holds no source file", err)
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn('(arch-part "src" :style declared :state declared ', graph)
        self.assertIn('(arch-part "docs/api" :style layered ', graph)
        self.assertNotIn("Architecture: detected (0 parts)", out)


class ConfigCheckTest(ProjectCase):
    def test_an_unknown_key_is_an_error_with_its_line(self) -> None:
        """/* REQ-ARCH-9407: a key the file does not have is an error with its line, never ignored: a top-level key other than `parts` and `generators` (`partz`), and a key of a part other than level, style, layers, routing-root, capabilities, overlays, declared, order and target (`layerz`, `exclude`). */"""
        error = _error("partz:\n  .:\n    style: layered\n")
        self.assertIn(".wingbox/config.yaml line 1: unknown key 'partz'", error)
        error = _error("parts:\n  .:\n    style: layered\ndefaults:\n  python: layered\n")
        self.assertIn(".wingbox/config.yaml line 4: unknown key 'defaults'", error)
        error = _error("parts:\n  .:\n    style: layered\n    layerz: {api: [x]}\n")
        self.assertIn(".wingbox/config.yaml line 4: part .: unknown key 'layerz'", error)
        error = _error("parts:\n  .:\n    style: layered\n    layers: {api: [x]}\n    exclude: [src]\n")
        self.assertIn(".wingbox/config.yaml line 5: part .: unknown key 'exclude'", error)
        self.assert_reads("parts:\n  .:\n    level: container\n    style: route-tree\n    routing-root: [app]\n    overlays: [cqrs]\n"
                          "    capabilities:\n      sql: {allowed_in: [shared]}\n    target:\n      - {layer: ui, folder: src/ui, proposed: true}\n"
                          "generators:\n  - run: tools/make.py\n    outputs: [src/made.ts]\n")

    def test_a_folder_or_a_key_written_twice_is_an_error(self) -> None:
        """/* REQ-ARCH-9408: a part folder listed twice (`src` and `src/`) is an error; so is a key that one mapping of the file holds twice, at any depth, with the lines of both - YAML alone would keep the last one in silence. */"""
        error = _error("parts:\n  src:\n    style: layered\n  src/:\n    style: none\n")
        self.assertIn(".wingbox/config.yaml line 4: ", error)
        self.assertIn("the folder src is listed twice, as src and as src/", error)
        error = _error("parts:\n  src:\n    style: layered\n  src:\n    style: none\n")
        self.assertIn(".wingbox/config.yaml line 4: the key 'src' is written twice (first on line 2)", error)
        error = _error("parts:\n  src:\n    style: layered\n    layers:\n      api: [a]\n      api: [b]\n")
        self.assertIn(".wingbox/config.yaml line 6: the key 'api' is written twice (first on line 5)", error)
        self.assert_reads("parts:\n  a: &same\n    style: layered\n  b: *same\n")

    def test_a_folder_has_one_layer(self) -> None:
        """/* REQ-ARCH-9409: a folder listed under two layers of `layers` is an error, and so are, in `order`, a folder and a folder that holds it (`x` and `x/y`): a folder is the whole tree below it and has one layer. A path under another layer of `layers` (`lib/hooks` next to `lib`) stays allowed, as the header of the file says. */"""
        error = _error("parts:\n  .:\n    style: layered\n    layers: {api: [x], domain: [x]}\n")
        self.assertIn("line 2: part .: `layers` lists the folder x under api and under domain", error)
        self.assert_reads("parts:\n  .:\n    style: layered\n    layers: {api: [lib], domain: [lib/hooks]}\n")
        error = _error("parts:\n  .:\n    style: declared\n    order:\n      - a: [x]\n      - b: [x/y]\n")
        self.assertIn("line 2: part .: `order` names the folder x/y and the folder x that holds it", error)
        error = _error("parts:\n  .:\n    style: declared\n    order:\n      - a: [x/y/z, w]\n      - b: ['x/']\n")
        self.assertIn("`order` names the folder x/y/z and the folder x that holds it", error)
        self.assert_reads("parts:\n  .:\n    style: declared\n    order:\n      - a: [x, .]\n      - b: [xy, y/x]\n")

    def test_a_value_of_the_wrong_kind_is_an_error_not_a_crash(self) -> None:
        """/* REQ-ARCH-9410: a `style` that is a list or a mapping is the "unknown style" error, and YAML nested deeper than the reader follows is a "not valid YAML" error: neither raises. */"""
        for value in ("[declared]", "{a: 1}", "3"):
            self.assertIn("line 2: part .: unknown style", _error(f"parts:\n  .:\n    style: {value}\n"), value)
        self.assertIn("is not valid YAML", _error("parts: " + "[" * 5000))

    def test_a_layer_name_and_a_level_are_one_word(self) -> None:
        """/* REQ-ARCH-9411: a layer name of `order` holds letters, digits, `_`, `.` and `-` only, and `level` is one such word: both are written into the graph file as bare words, so a name with a line break or a bracket is a config error and never becomes a line of the graph. */"""
        injected = 'game 0)\n(arch-part "injected" :style layered :state determined)\n(arch-rank "src" game'
        text = "parts:\n  src:\n    style: declared\n    order:\n      - core: [core]\n      - " + \
               '"' + injected.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '": [game]\n'
        error = _error(text)
        self.assertIn("line 2: part src: `order` layer name ", error)
        self.assertNotIn("\n", error)
        for name in ("'a b'", "'a)'", "'@a'", "'a/b'"):
            self.assertIn("`order` layer name ", _error(f"parts:\n  .:\n    style: declared\n    order:\n      - {name}: [x]\n"), name)
        self.assert_reads("parts:\n  .:\n    style: declared\n    order:\n      - v1.core_x-y: [x]\n      - '2024': [y]\n")
        error = _error('parts:\n  .:\n    style: layered\n    level: "x :kind y)\\n(arch-part z"\n')
        self.assertIn("line 2: part .: `level` is one word", error)
        self.assertIn("`level` is one word", _error("parts:\n  .:\n    style: layered\n    level: [a, b]\n"))
        self.assertEqual("", self.assert_reads("parts:\n  .:\n    style: layered\n    level:\n").parts[0].level)

    def test_a_quoted_text_of_a_fact_stays_on_one_line(self) -> None:
        """/* REQ-ARCH-9444: a text quoted into a fact never ends the line: every character that a reader of text takes for a line break (a line feed, a carriage return, a form feed, the Unicode line separators) is written as a space. A part folder named with such a character adds no line to the facts of the graph. */"""
        from scripts.architecture.sexp import q

        for separator in "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029":
            self.assertEqual('"a b"', q(f"a{separator}b"), repr(separator))
        self.assertEqual('"tab\there \\\\ \\"quote\\""', q('tab\there \\ "quote"'))
        key = "docs\r(arch-part injected :style layered :state determined)\rz"
        plan = RepositoryPlan((_part(key, "layered"),), (key,))
        lines = graph_architecture(plan, LIBRARY, {}).part_lines
        self.assertTrue(lines)
        self.assertEqual([], [line for line in lines if len(line.splitlines()) != 1 or not line.startswith(("(arch-part \"docs ", "(arch-deny \"docs ", "(arch-capability \"docs ", "(arch-pattern \"docs ", "(arch-rank \"docs "))])

    def test_a_layer_name_of_a_declared_table_is_one_word(self) -> None:
        """/* REQ-ARCH-9412: a layer table of the project (layers.mjs) whose layer name holds a line break, a space or a bracket is "not read" with the reason: the name would be written into the graph file as a bare word. */"""
        text = ('// lowest first\nexport const layers = [\n  { name: "core 0)\\n(arch-part injected :style layered :state determined)'
                '\\n(arch-rank . core", path: "^src/core/" },\n  { name: "ui", path: "^src/ui/" },\n];\n')
        result = read_layer_table("layers.mjs", text)
        self.assertIsInstance(result, NotRead)
        self.assertIn("is not one word", result.reason)
        self.assertNotIn("\n", result.reason)
        named = read_layer_table("layers.mjs", '// lowest first\nexport const layers = [{ name: "@app/core-v2", path: "^src/core/" }];\n')
        self.assertIsInstance(named, DeclaredLayers)


class ConfigWriteTest(ProjectCase):
    NAMES = ("2024", "on", "1_0", "Null", "yes", "No", "TRUE", "off", "~", "0x1f", "1.5", ".5", "017", "12:30", "2024-01-02",
             ".inf", ".NaN", "=", "<<", "@scope", "-x", "+1", "a b", 'x"y', "back\\slash", "tab\there", "bell\x07", "nul\x00x",
             "line\u2028break", "next\x85line", "caf\u00e9", "\U0001f600", "a:b", "a#b", "[id]", "(group)", "{x}", "a,b", "'q'",
             "trailing ", ".github", "v1.0", "_internal", "normal-name")

    def test_every_name_wingbox_writes_reads_back_as_itself(self) -> None:
        """/* REQ-ARCH-9413: a folder name that YAML reads as something else when it is bare (2024, on, 1_0, Null, ~, 0x1f, @scope) or that holds a character YAML refuses (a control character, a line separator) is written quoted and escaped: the config that Wingbox renders passes its own check and gives back every name unchanged, for a part key, a `layers` folder and an `order` entry. */"""
        order = tuple((f"layer{index}", (name,)) for index, name in enumerate(self.NAMES))
        rules = DeclaredRules(files=(), layers=layers_from_order(".wingbox/config.yaml", "src", order), not_read=(),
                              baseline=frozenset(), baseline_file="")
        layers = tuple(LayerFolder(name, "service", "config", ".wingbox/config.yaml", 0) for name in self.NAMES)
        parts = (_part("src", "declared", declared=rules, state="declared"), _part("2024", "layered", layers=layers),
                 _part("on", "layered"), _part("pkg/1_0", "layered"), _part("Null", "layered"))
        result = self.assert_reads(render_config(RepositoryPlan(parts, ("src", "pkg/1_0", "Null", "2024", "on")), LIBRARY, "2026-01-01"))
        self.assertEqual(["src", "2024", "on", "pkg/1_0", "Null"], [part.path for part in result.parts])
        self.assertEqual(order, result.parts[0].order)
        self.assertEqual((("service", self.NAMES),), result.parts[1].layers)

    def test_a_comment_wingbox_writes_never_breaks_the_file(self) -> None:
        """/* REQ-ARCH-9414: the reason of a learned folder is written as a YAML comment with every character YAML refuses there, and every line break, replaced by a space: a reason with a control character or a line separator gives a config that passes the check and holds the parts it held. */"""
        reason = "agent: read order.ts\x07 and z.ts\u2028parts: {}\x85  other:\x00 1"
        part = _part(".", "layered", layers=(LayerFolder("zone", "service", "learned", reason, 0),))
        text = render_config(RepositoryPlan((part,), ("",)), LIBRARY, "2026-01-01")
        result = self.assert_reads(text)
        self.assertEqual((("service", ("zone",)),), result.parts[0].layers)
        self.assertIn("zone: learned by agent: read order.ts  and z.ts parts: {}", text)

    def test_an_answer_with_a_control_character_is_rejected(self) -> None:
        """/* REQ-LEARN-9415: a line of an answer to `wingbox learn` that holds a control character is an error of that line, so the answer is rejected as a whole: its text would go into the config, learned.sexp and the terminal. A tab is not one. */"""
        parts = {".": ("layered", frozenset({"zone"}))}
        answer = agent_learn.parse_answer('(layer . zone service :because "read order.ts\x07 and z.ts")\n', parts, LIBRARY)
        self.assertIsInstance(answer, agent_learn.AnswerErr)
        self.assertEqual(("line 1: the control character '\\x07' is not allowed: write plain text",), answer.errors)
        self.assertIsInstance(agent_learn.parse_answer('(layer . zone service :because "read\torder.ts")\n', parts, LIBRARY),
                              agent_learn.AnswerOk)

    def test_an_answer_file_that_cannot_be_read_is_an_error(self) -> None:
        """/* REQ-LEARN-9445: `wingbox learn --apply FILE` with a file that does not exist, or that is not UTF-8, names the file and the reason on stderr and exits 2; nothing is saved. */"""
        _layered(self.root)
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])
        status, out, err = self.run_cli("learn", "--apply", "nosuch.sexp", "--sexp")
        self.assertEqual(2, status, out + err)
        self.assertIn("ERROR: cannot read the answer nosuch.sexp: ", err)
        (self.root / "answer.sexp").write_bytes(b'(style . caf\xe9)\n')
        status, out, err = self.run_cli("learn", "--apply", "answer.sexp", "--sexp")
        self.assertEqual(2, status, out + err)
        self.assertIn("ERROR: cannot read the answer answer.sexp: ", err)
        self.assertFalse((self.root / ".wingbox" / "learned.sexp").exists())

    def test_a_style_change_through_learn_gives_a_config_the_build_reads(self) -> None:
        """/* REQ-LEARN-9416: `wingbox learn --apply` with `(style . hexagonal)` on a part whose config lists the layers of another style writes the folders the new style places, not the old `layers:` block: the config passes the check, the build after it succeeds and the exit status is 0. */"""
        _layered(self.root)
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])
        _write(self.root, {".wingbox/config.yaml": LAYERED})
        status, out, err = self.run_cli("learn", "--apply", "--sexp", stdin="(style . hexagonal)\n")
        self.assertEqual(0, status, out + err)
        self.assertIn('(learn-style "." hexagonal)', out)
        self.assertIn("(learn-result :build ok ", out)
        result = self.assert_reads(self.config())
        self.assertEqual("hexagonal", result.parts[0].style)
        hexagonal = {layer.id for layer in LIBRARY.style("hexagonal").layers}
        written = {layer for layer, _folders_of in result.parts[0].layers}
        self.assertTrue(written and written <= hexagonal, result.parts[0].layers)
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])

    def test_learn_apply_fails_when_the_build_after_it_fails(self) -> None:
        """/* REQ-LEARN-9417: when the build that follows `wingbox learn --apply` fails, the command says so on stderr, prints `(learn-result :build failed ...)` and exits with a status other than 0. */"""
        _layered(self.root)
        _write(self.root, {"src/zone/z.ts": "export const z = 1;\n", "src/zone/order.ts": "export const o = 1;\n"})
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])
        with mock.patch.object(cli, "_build", return_value=2):
            status, out, err = self.run_cli("learn", "--apply", "--sexp", stdin='(layer . zone service :because "z.ts is a service")\n')
        self.assertEqual(1, status, out + err)
        self.assertIn("(learn-result :build failed ", out)
        self.assertIn("ERROR: the build after learn failed (status 2)", err)

    def test_learn_writes_nothing_when_the_config_would_not_pass_the_check(self) -> None:
        """/* REQ-LEARN-9418: before `wingbox learn --apply` replaces `.wingbox/config.yaml`, the text is checked the way the build checks the file; a text the check refuses (here: a capability rule that names a layer the new style does not have) is not written: the command names the reason, exits 1 and leaves config.yaml and learned.sexp as they were. */"""
        _layered(self.root)
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])
        before = LAYERED + "    capabilities:\n      sql: {allowed_in: [infra]}\n"
        _write(self.root, {".wingbox/config.yaml": before})
        status, out, err = self.run_cli("learn", "--apply", "--sexp", stdin="(style . hexagonal)\n")
        self.assertEqual(1, status, out + err)
        self.assertIn("ERROR: .wingbox/config.yaml not written: ", err)
        self.assertIn("style hexagonal has no layer 'infra'", err)
        self.assertEqual(before, self.config())
        self.assertFalse((self.root / ".wingbox" / "learned.sexp").exists())

    def test_layers_from_folders_with_names_yaml_reads_as_values(self) -> None:
        """/* REQ-ARCH-9419: `wingbox init --from-folders` on folders named 2024, on, 1_0 and Null writes a config that passes the check with those four layers, and the build after it succeeds; a folder name that cannot be a layer name gives a layer name of its letters (`(shop)` is `shop`), unique within the part. */"""
        _folders(self.root, ("2024", "on", "1_0", "Null"))
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])
        status, out, err = self.run_cli("init", "--from-folders", "src", "--sexp")
        self.assertEqual(0, status, out + err)
        result = self.assert_reads(self.config())
        declared = next(part for part in result.parts if part.path == "src")
        self.assertEqual([("2024", ("2024",)), ("on", ("on",)), ("1_0", ("1_0",)), ("Null", ("Null",))], list(declared.order))
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(0, status, out + err)
        from scripts.architecture.config import layer_names

        self.assertEqual({"shop": "shop", "(shop)": "shop-2", "@scope": "scope", "[id]": "id", "..": "..", ".": "root", "a b": "a-b"},
                         layer_names(("shop", "(shop)", "@scope", "[id]", "..", ".", "a b")))
        self.assertEqual({"root": "root", ".": "root-files"}, layer_names((".", "root")))

    def test_a_new_package_is_added_to_a_config_without_parts(self) -> None:
        """/* REQ-NEW-9420: `wingbox init` in an empty package of a repository whose root config is `parts: {}` (as init writes it for a repository too small to judge) adds the package as the first part: the file passes the check and lists the package. A second run keeps the file and says that the part is listed. */"""
        _write(self.root, {"a.py": "x = 1\n"})
        (self.root / ".git").mkdir()
        self.assertEqual(0, self.run_cli("init", str(self.root), "--sexp")[0])
        self.assertIn("\nparts: {}\n", self.config())
        package = self.root / "pkg" / "new"
        package.mkdir(parents=True)
        status, out, err = self.run_cli("init", "--language", "python", "--style", "layered", "--yes", "--sexp", cwd=package)
        self.assertEqual(0, status, out + err)
        self.assertIn('(init-new-project "pkg/new" :language python :style layered ', out)
        self.assertEqual(["pkg/new"], [part.path for part in self.assert_reads(self.config()).parts])
        written = self.config()
        status, out, err = self.run_cli("init", "--language", "python", "--style", "layered", "--yes", "--sexp", cwd=package)
        self.assertEqual(0, status, out + err)
        self.assertIn('(arch-config-kept ".wingbox/config.yaml" :reason "part pkg/new is listed in the file")', out)
        self.assertEqual(written, self.config())
        from scripts.architecture.config import add_parts

        added = add_parts("# head\nparts: { }   # nothing yet\n# tail\n", "# new head\nparts:\n  b:\n    style: none\n")
        self.assertEqual(["b"], [part.path for part in self.assert_reads(added).parts])


    def test_a_command_that_writes_the_parts_keeps_the_generators(self) -> None:
        """/* REQ-LEARN-9441: a command that renders the parts of `.wingbox/config.yaml` anew (`wingbox learn --apply`) replaces the parts and keeps the other key of the file, `generators:`, with its comments; `init` in a new package of the repository adds its part at the end of `parts:`, before `generators:`. */"""
        _layered(self.root)
        _write(self.root, {"src/zone/z.ts": "export const z = 1;\n", "src/zone/order.ts": "export const o = 1;\n",
                           "tools/make.py": "x = 1\n"})
        generators = "# what writes z.ts\ngenerators:\n  - run: tools/make.py   # the generator\n    outputs: [src/zone/z.ts]\n"
        _write(self.root, {".wingbox/config.yaml": LAYERED + "\n" + generators})
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])
        status, out, err = self.run_cli("learn", "--apply", "--sexp", stdin='(layer . zone service :because "z.ts is a service")\n')
        self.assertEqual(0, status, out + err)
        self.assertIn("zone: learned by agent: z.ts is a service", self.config())
        self.assertTrue(self.config().endswith(generators), self.config())
        self.assert_reads(self.config())
        (self.root / ".git").mkdir()
        package = self.root / "packages" / "new"
        package.mkdir(parents=True)
        status, out, err = self.run_cli("init", "--language", "python", "--style", "layered", "--yes", "--sexp", cwd=package)
        self.assertEqual(0, status, out + err)
        self.assertEqual([".", "packages/new"], [part.path for part in self.assert_reads(self.config()).parts])
        self.assertTrue(self.config().endswith(generators), self.config())

    def test_the_other_keys_of_a_config_are_kept_as_text(self) -> None:
        """/* REQ-ARCH-9442: the keys of a config file other than `parts` are taken as the file writes them - the comment lines right above the key included, the instruction header and the closing comment of the parts left out - wherever the key stands; a key inside braces is written again in block style; a file that is not a YAML mapping gives nothing. */"""
        from scripts.architecture.config import kept_keys

        after = ("# Wingbox architecture config. Read this\n# more\nparts:\n  a:\n    style: layered\n"
                 "# not judged (fewer than 5 source files): x\n\n# mine\ngenerators:\n  - run: t.py   # g\n    outputs: [a]\n")
        self.assertEqual("# mine\ngenerators:\n  - run: t.py   # g\n    outputs: [a]\n", kept_keys(after))
        before = "# Wingbox architecture config.\ngenerators:\n  - run: t.py\n    outputs: [a]\n\n# about parts\nparts: {}\n"
        self.assertEqual("generators:\n  - run: t.py\n    outputs: [a]\n", kept_keys(before))
        self.assertEqual("generators:\n- run: t.py\n  outputs:\n  - a\n",
                         kept_keys("{parts: {a: {style: none}}, generators: [{run: t.py, outputs: [a]}]}\n"))
        for text in ("", "parts: [\n", "- a\n", "parts:\n  a:\n    style: none\n"):
            self.assertEqual("", kept_keys(text), text)


class ProjectRootTest(ProjectCase):
    def test_init_never_adopts_the_config_of_the_home_folder(self) -> None:
        """/* REQ-NEW-9421: `wingbox init` in an empty folder below the home folder never takes `$HOME/.wingbox/config.yaml` (the favourite styles of a person) for the config of the project: it writes the config, the setup notes and the .gitignore into the new folder and leaves the home folder as it was. */"""
        _write(self.home, {".wingbox/config.yaml": "defaults:\n  python: hexagonal\n"})
        project = self.home / "work" / "newproj"
        project.mkdir(parents=True)
        status, out, err = self.run_cli("init", "--language", "python", "--style", "hexagonal", "--yes", cwd=project)
        self.assertEqual(0, status, out + err)
        self.assertIn("wrote .wingbox/config.yaml", out)
        self.assertEqual(["."], [part.path for part in self.assert_reads(self.config(project)).parts])
        self.assertEqual("defaults:\n  python: hexagonal\n", self.config(self.home))
        self.assertEqual({self.home / ".wingbox", self.home / ".wingbox" / "config.yaml", self.home / "work"},
                         {path for path in self.home.rglob("*") if project not in path.parents and path != project})

    def test_the_search_for_the_project_config_ends_at_the_repository_root(self) -> None:
        """/* REQ-NEW-9422: looking upward for the config a new package belongs to, `wingbox init` stops at the root of the repository (the folder that holds .git) and passes over a `.wingbox/config.yaml` without `parts:`: a config above the repository, or one that states no parts, is not the config of this project. */"""
        outer = self.base / "outer"
        _write(outer, {".wingbox/config.yaml": "parts:\n  other:\n    style: layered\n"})
        package = outer / "repo" / "pkg"
        package.mkdir(parents=True)
        (outer / "repo" / ".git").mkdir()
        status, out, err = self.run_cli("init", "--language", "python", "--style", "layered", "--yes", "--sexp", cwd=package)
        self.assertEqual(0, status, out + err)
        self.assertIn('(init-new-project "." :language python :style layered ', out)
        self.assertEqual("parts:\n  other:\n    style: layered\n", self.config(outer))
        self.assertTrue((package / ".wingbox" / "config.yaml").is_file())
        loose = self.base / "loose"
        _write(loose, {".wingbox/config.yaml": "defaults:\n  python: mvc\n"})
        (loose / "pkg").mkdir()
        status, out, err = self.run_cli("init", "--language", "python", "--style", "layered", "--yes", "--sexp", cwd=loose / "pkg")
        self.assertEqual(0, status, out + err)
        self.assertEqual("defaults:\n  python: mvc\n", self.config(loose))
        self.assertTrue((loose / "pkg" / ".wingbox" / "config.yaml").is_file())

    def test_place_answers_for_the_project_below_a_folder_with_another_config(self) -> None:
        """/* REQ-ARCH-9423: `wingbox place` in a project whose parent folder holds a `.wingbox/config.yaml` without `parts:` answers for the project, by its own graph and style, instead of taking the parent folder for the repository; a parent config that lists the project as a part still decides, as before. */"""
        _write(self.base / "area", {".wingbox/config.yaml": "defaults:\n  python: hexagonal\n"})
        project = _layered(self.base / "area" / "shop")
        self.assertEqual(0, self.run_cli("build", str(project), cwd=project)[0])
        status, out, err = self.run_cli("place", "a new HTTP endpoint for orders", cwd=project)
        self.assertEqual(0, status, out + err)
        self.assertIn('(place "." :layer api :folder "src/routes" :present :style layered ', out)
        _write(self.base / "area", {".wingbox/config.yaml": "parts:\n  shop:\n    style: layered\n"})
        (project / ".git").mkdir()
        status, out, err = self.run_cli("place", "a new HTTP endpoint for orders", cwd=project)
        self.assertEqual(0, status, out + err)
        self.assertIn('(place "shop" :layer api :folder "shop/src/routes" :present :style layered ', out)

    def test_a_home_config_that_is_not_yaml_is_a_warning(self) -> None:
        """/* REQ-NEW-9424: a `$HOME/.wingbox/config.yaml` that is not valid YAML is passed over with a warning that names the file; `wingbox init` still offers the styles of the library. */"""
        _write(self.home, {".wingbox/config.yaml": "defaults: [unclosed\n"})
        status, out, err = self.run_cli("init", str(self.root), "--language", "typescript")
        self.assertEqual(2, status, out + err)
        self.assertIn("(init-choice 1 layered :language typescript :from library ", out)
        self.assertIn("WARNING: ", err)
        self.assertIn("config.yaml not read: ", err)


class DamagedDataTest(ProjectCase):
    def test_a_line_of_the_decisions_store_that_cannot_be_read_is_skipped(self) -> None:
        """/* REQ-ARCH-9425: a line of `.wingbox/decisions.sexp` whose `:runs` is not a number is left out and named with its line number in `skipped`; reading the store never raises and the other lines count. */"""
        store = decisions.read_store("\n".join([
            '(advice-given abc :part "." :kind move :target "a.ts" :rule "move api -> domain")',
            "(advice-outcome X :ignored :runs abc)",
            "(advice-outcome Y :ignored :runs -1)",
            "(advice-outcome abc :ignored :runs 4)",
        ]))
        self.assertEqual(["line 2: :runs 'abc' is not a number", "line 3: :runs '-1' is not a number"], store.skipped)
        self.assertEqual({"abc": "ignored"}, store.outcomes)
        self.assertEqual({"abc": 4}, store.ignored_runs)

    def test_a_damaged_decisions_store_never_stops_a_build(self) -> None:
        """/* REQ-ARCH-9426: with a line in `.wingbox/decisions.sexp` that cannot be read, `wingbox build` warns with the file and the line, writes the graph and exits 0, and `wingbox advice` answers. */"""
        _layered(self.root, violation=True)
        _write(self.root, {".wingbox/decisions.sexp": "(advice-outcome X :ignored :runs abc)\n"})
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(0, status, out + err)
        self.assertIn("WARNING: .wingbox/decisions.sexp line 1: :runs 'abc' is not a number: the line is skipped", err)
        self.assertTrue((self.root / ".wingbox" / "index.sexp").is_file())
        status, out, err = self.run_cli("advice")
        self.assertEqual(0, status, out + err)
        self.assertIn(":kind fix-violation ", out)
        self.assertIn("WARNING: .wingbox/decisions.sexp line 1: ", err)

    def test_a_decisions_store_that_is_not_utf8_never_stops_a_build(self) -> None:
        """/* REQ-ARCH-9443: a byte that is not UTF-8 in `.wingbox/decisions.sexp` (a reason typed in another encoding) stops neither `wingbox build`, which writes the graph, the report and the map and exits 0, nor `wingbox advice`. */"""
        _layered(self.root, violation=True)
        (self.root / ".wingbox").mkdir()
        (self.root / ".wingbox" / "decisions.sexp").write_bytes(b'(advice-outcome X :rejected :by user :reason "caf\xe9" :at 2026-01-01)\n')
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(0, status, out + err)
        self.assertTrue((self.root / ".wingbox" / "REPORT.md").is_file())
        status, out, err = self.run_cli("advice")
        self.assertEqual(0, status, out + err)
        self.assertIn(":kind fix-violation ", out)

    def test_a_line_of_learned_sexp_that_cannot_be_read_is_skipped(self) -> None:
        """/* REQ-LEARN-9427: an entry of `.wingbox/learned.sexp` whose `:files` is not a number is left out (its folder is asked again) and named with its line; `wingbox learn --agent` warns and prints the task. */"""
        text = ('(learned-entry "." "zone" service :because "x" :files abc)\n'
                '(learned-entry "." "kept" infra :because "y" :files 3)\n')
        self.assertEqual({(".", "kept"): ("infra", "y", 3)}, agent_learn.read_learned(text))
        self.assertEqual(["line 1: :files 'abc' is not a number"], agent_learn.unread_learned(text))
        _layered(self.root)
        _write(self.root, {"src/zone/z.ts": "export const z = 1;\n", "src/zone/order.ts": "export const o = 1;\n",
                           ".wingbox/learned.sexp": text})
        self.assertEqual(0, self.run_cli("build", str(self.root))[0])
        status, out, err = self.run_cli("learn", "--agent", "--sexp")
        self.assertEqual(0, status, out + err)
        self.assertIn("WARNING: .wingbox/learned.sexp line 1: :files 'abc' is not a number: the line is skipped", err)
        self.assertIn('(learn-folder "." "zone" ', out)

    def test_a_brace_in_a_path_is_text_in_the_fix_of_a_rule(self) -> None:
        """/* REQ-ARCH-9428: the fix of a rule has four names that are filled in - {source}, {target}, {source_folder}, {target_folder}; any other brace is text. The fix of a declared rule holds the path of its file, so a folder named `pkg{x}` gives advice instead of raising. */"""
        fix = "move the imported code down ({file}) or see pkg{x}/layers.mjs: {source} -> {target_folder}/"
        part = PartRules("pkg{x}", "declared", "declared", {"core": 0, "ui": 1}, {("core", "ui"): ("core imports ui", "violation", fix)})
        violation = Violation("a", "b", "core", "ui", "pkg{x}", "declared", "core imports ui", False)
        advice = fix_violations(part, (violation,), {"a": "pkg{x}/src/core/c0.ts", "b": "pkg{x}/src/ui/u0.ts"})
        self.assertEqual("move the imported code down ({file}) or see pkg{x}/layers.mjs: pkg{x}/src/core/c0.ts -> pkg{x}/src/ui/",
                         advice[0].reason)

    def test_a_layer_table_nested_too_deep_is_not_read(self) -> None:
        """/* REQ-ARCH-9429: a layer table with brackets nested thousands deep is "not read" with the reason; reading it never raises. */"""
        result = read_layer_table("layers.mjs", "// lowest first\nexport const layers = " + "[" * 5000 + ";\n")
        self.assertIsInstance(result, NotRead)
        self.assertIn("nested deeper than", result.reason)
        mixed = read_layer_table("layers.mjs", "// lowest first\nexport const layers = " + "[{a: " * 3000 + ";\n")
        self.assertIsInstance(mixed, NotRead)


class OutsideReadTest(ProjectCase):
    def test_a_declared_rule_file_is_a_path_inside_the_repository(self) -> None:
        """/* REQ-ARCH-9430: an entry of `declared` that is absolute or holds `..` is a config error: a rule file is a file of the repository. */"""
        for path in ("../outside/secret.mjs", "a/../../x.mjs", "/etc/layers.mjs", "..\\\\x.mjs", "C:/x.mjs"):
            error = _error(f"parts:\n  .:\n    style: declared\n    declared: ['{path}']\n")
            self.assertIn("line 2: part .: declared ", error, path)
            self.assertIn("must be a file inside the repository", error, path)
        self.assert_reads("parts:\n  .:\n    style: declared\n    declared: [platform/backend/layers.mjs, layers..mjs]\n")

    @unittest.skipIf(os.name == "nt", "needs symbolic links")
    def test_only_a_regular_file_inside_the_repository_is_read(self) -> None:
        """/* REQ-ARCH-9431: the reader of rule files opens a regular file whose real path is inside the repository; a symbolic link to a file outside, a path with `..` and a folder give no text and a warning with the reason. A link to a file inside the repository is read. */"""
        _write(self.base, {"outside/secret.mjs": "// lowest first\nexport const layers = [{ name: \"secret\", path: \"^x/\" }];\n"})
        _write(self.root, {"rules/real.mjs": "inside\n"})
        os.symlink(self.base / "outside" / "secret.mjs", self.root / "layers.mjs")
        os.symlink(self.root / "rules" / "real.mjs", self.root / "alias.mjs")
        os.symlink(self.root / "nowhere.mjs", self.root / "dangling.mjs")
        read = text_reader(str(self.root))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(read("layers.mjs"))
            self.assertIsNone(read("../outside/secret.mjs"))
            self.assertIsNone(read("rules"))
            self.assertIsNone(read("dangling.mjs"))
            self.assertIsNone(read("missing.mjs"))
            self.assertEqual("inside\n", read("alias.mjs"))
            self.assertEqual("inside\n", read("rules/real.mjs"))
        self.assertIn("WARNING: cannot read layers.mjs: it is outside the repository", err.getvalue())
        self.assertIn("WARNING: cannot read ../outside/secret.mjs: it is outside the repository", err.getvalue())
        self.assertIn("WARNING: cannot read rules: it is not a regular file", err.getvalue())
        self.assertIn("WARNING: cannot read dangling.mjs: ", err.getvalue())
        self.assertNotIn("missing.mjs", err.getvalue())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_a_pipe_in_the_place_of_a_rule_file_does_not_block(self) -> None:
        """/* REQ-ARCH-9432: a named pipe (or a link to one) in the place of a rule file is not opened: the reader answers at once with a warning instead of waiting for a writer. */"""
        os.mkfifo(self.base / "pipe")
        os.symlink(self.base / "pipe", self.root / "layers.mjs")
        os.mkfifo(self.root / "inside.mjs")
        read = text_reader(str(self.root))
        answers: list = []

        def read_both() -> None:
            with redirect_stderr(io.StringIO()):
                answers.extend([read("layers.mjs"), read("inside.mjs")])

        thread = threading.Thread(target=read_both, daemon=True)
        thread.start()
        thread.join(5)
        blocked = thread.is_alive()
        while thread.is_alive():   # let a reader that waits on a pipe go, so that the test ends
            for pipe in (self.base / "pipe", self.root / "inside.mjs"):
                try:
                    os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
                except OSError:
                    pass
            thread.join(0.2)
        self.assertFalse(blocked, "the reader waited on a named pipe")
        self.assertEqual([None, None], answers)

    def test_a_rule_file_that_was_not_read_is_named_as_such(self) -> None:
        """/* REQ-ARCH-9433: a rule file of a declared part that is missing or cannot be read is listed as not read for that reason, not as a file that "states no layer order". */"""
        rules = declared_rules((DeclaredFile("layers.mjs", "rules"),), lambda path: None)
        self.assertIsNone(rules.layers)
        self.assertEqual((NotRead("layers.mjs", "the file is missing or cannot be read"),), rules.not_read)

    @unittest.skipIf(os.name == "nt", "needs symbolic links")
    def test_the_build_reads_no_rule_file_outside_the_repository(self) -> None:
        """/* REQ-ARCH-9434: `wingbox build` puts nothing of a file outside the repository into the graph: a `layers.mjs` that is a symbolic link to such a file leaves its part with rules that were not read, and `declared: ["../x"]` stops the build with a config error. */"""
        secret = "// lowest first\nexport const layers = [\n  { name: \"secretcore\", path: \"^src/core/\" },\n  { name: \"secretui\", path: \"^src/ui/\" },\n];\n"
        _write(self.base, {"outside/secret.mjs": secret})
        _folders(self.root, ("core", "ui"))
        os.symlink(self.base / "outside" / "secret.mjs", self.root / "layers.mjs")
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(0, status, out + err)
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertNotIn("secretcore", graph)
        self.assertIn('(arch-declared-unread "." "layers.mjs" ', graph)
        self.assertIn("WARNING: cannot read layers.mjs: it is outside the repository", err)
        (self.root / "layers.mjs").unlink()
        _write(self.root, {".wingbox/config.yaml": 'parts:\n  .:\n    style: declared\n    declared: ["../outside/secret.mjs"]\n'})
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(2, status, out + err)
        self.assertIn("must be a file inside the repository", err)


class PatternTest(unittest.TestCase):
    def _table(self, pattern: str) -> DeclaredLayers | NotRead:
        text = '// lowest first\nexport const layers = [\n  { name: "core", path: "%s" },\n  { name: "ui", path: "^src/ui/" },\n];\n'
        return read_layer_table("layers.mjs", text % pattern.replace("\\", "\\\\"))

    def test_a_pattern_that_can_take_unbounded_time_is_not_read(self) -> None:
        """/* REQ-ARCH-9435: a layer table whose path holds a group that repeats and holds another repeat, an optional item or overlapping alternatives - `(a+)+`, `(a*)*`, `(.+/)*`, `(a|aa)+`, `(a?a?)+` - is "not read", with the layer and the group named: matching it against a long file name can take time that doubles with every character. */"""
        for pattern, group in ((r"^src/core/(a+)+$", "(a+)+"), (r"^src/(a*)*x", "(a*)*"), (r"^(.+/)*core/", "(.+/)*"),
                               (r"^src/(a|aa)+$", "(a|aa)+"), (r"^src/(?:x+x+){2,}y", "(?:x+x+){2,}"), (r"^((a+)b)+", "((a+)b)+"),
                               (r"^src/(a?a?)+$", "(a?a?)+"), (r"^src/(a{0,1}b?)*$", "(a{0,1}b?)*")):
            result = self._table(pattern)
            self.assertIsInstance(result, NotRead, pattern)
            self.assertIn(f"layer core: the group {group} of its path repeats", result.reason, pattern)

    def test_the_usual_path_patterns_are_read(self) -> None:
        """/* REQ-ARCH-9436: path patterns without such a group are read as before: alternatives, optional groups, classes, a lookahead, a repeat that is not inside a repeating group, and the two repeating forms that match in one way only - a run of one kind of character with a character that is not of that kind (`([^/]+/)*`) and alternatives of plain text of which none starts another (`(core|platform)+`). */"""
        for pattern in (r"^src/(core|platform)/", r"^src/[^/]+/domain/", r"\.(spec|test)\.(js|ts)$", r"^(?!src/test)src/",
                        r"^(.*/)?node_modules/", r"^src/(?:app|pages)(/.*)?$", r"^src/([^/]+/)*domain/", r"^packages/(?:[\w-]+/)*src/",
                        r"^lib(/[^/]+)*$", r"^src/(core|platform)+/", r"^src/core/(a+){1}$", r"^src/[(a+)+]/", r"^a{2,3}/",
                        r"^src/(ab)+/", r"^src/(\.[a-z]+)?$", r"^src/(x/)?(y/)?z"):
            table = self._table(pattern)
            self.assertIsInstance(table, DeclaredLayers, f"{pattern}: {getattr(table, 'reason', '')}")
        table = self._table(r"^src/([^/]+/)*core/")
        self.assertEqual("core", table.layer_of("src/a/b/core/x.ts"))
        self.assertEqual("ui", table.layer_of("src/ui/x.ts"))


class LearnedLayerTest(ProjectCase):
    def test_a_learned_layer_that_the_style_does_not_have_is_not_applied(self) -> None:
        """/* REQ-ARCH-9437: a `learned-layer` line of the decisions store whose layer is no layer of the style of its part is not applied to the plan and is returned as unknown; a line with a layer of the style applies as before. */"""
        from scripts.architecture.plan import unknown_learned

        part = _part(".", "layered", layers=(LayerFolder("routes", "api", "config", "", 0),))
        plan = RepositoryPlan((part,), ("",))
        bad, good = decisions.Learned(".", "services", "nolayer", "x"), decisions.Learned(".", "core", "service", "y")
        learned = {".": [bad, good]}
        self.assertEqual((bad,), unknown_learned(plan, learned, LIBRARY))
        applied = apply_learned(plan, learned, library=LIBRARY).parts[0].layers
        self.assertEqual([("core", "service"), ("routes", "api")], [(folder.folder, folder.layer) for folder in applied])

    def test_the_build_warns_about_a_learned_layer_it_ignores(self) -> None:
        """/* REQ-ARCH-9438: `wingbox build` with a `learned-layer` line that names a layer the style of the part does not have warns with the folder and the layer, ignores the line, and writes no such layer into the graph. */"""
        _layered(self.root)
        _write(self.root, {".wingbox/decisions.sexp": '(learned-layer "." "services" nolayer :from x :at 2026-01-01)\n'})
        status, out, err = self.run_cli("build", str(self.root))
        self.assertEqual(0, status, out + err)
        self.assertIn("WARNING: .wingbox/decisions.sexp: learned-layer '.' 'services': 'nolayer' is no layer of the style", err)
        self.assertNotIn("nolayer", (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8"))


class CompareTest(ProjectCase):
    def test_compare_says_when_there_is_nothing_to_compare(self) -> None:
        """/* REQ-CMP-9439: `wingbox compare` never answers with nothing: when no part has a style and a language (a repository of one part without a manifest), it prints one `compare-none` fact with the number of parts of the repository and the reason. --human says the same in words, --json holds the fact and --sexp prints it too. */"""
        _layered(self.root)
        (self.root / "package.json").unlink()
        for form in ((), ("--sexp",)):
            status, out, err = self.run_cli("compare", *form)
            self.assertEqual(0, status, out + err)
            self.assertEqual(1, len(out.splitlines()), out)
            self.assertTrue(out.startswith('(compare-none :parts 1 :reason "no part has a style and a language'), out)
        self.assertTrue(self.run_cli("compare", "--human")[1].startswith("nothing to compare: no part has a style"))
        self.assertIn('"fact":"compare-none"', self.run_cli("compare", "--json")[1])

    def test_compare_of_a_part_it_shows_prints_no_compare_none(self) -> None:
        """/* REQ-CMP-9440: when a part is shown, `wingbox compare` prints no `compare-none`: the answer for a repository of one part with a style and a manifest is its `compare-part` line and nothing else, as before. */"""
        _layered(self.root)
        status, out, err = self.run_cli("compare", "--sexp")
        self.assertEqual(0, status, out + err)
        self.assertEqual('(compare-part "." :language typescript :style layered :layers "api domain infra service")\n', out)


if __name__ == "__main__":
    unittest.main()
