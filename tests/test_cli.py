import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


CLI_PATH = Path(__file__).resolve().parents[1] / "src" / "wingbox" / "cli.py"
sys.path.insert(0, str(CLI_PATH.parents[1]))   # `wingbox build` imports wingbox.map_page and wingbox.overview
SPEC = importlib.util.spec_from_file_location("wingbox_cli", CLI_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load the wingbox command module from {CLI_PATH}")
wingbox_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wingbox_cli
SPEC.loader.exec_module(wingbox_cli)


class WingboxCommandArgumentsTest(unittest.TestCase):
    def test_build_uses_the_current_directory_by_default(self) -> None:
        """/* REQ-CLI-001: `wingbox build` without a path must build the current directory. */"""
        # Expected paths in the system's own form: \work\project on Windows.
        self.assertEqual(wingbox_cli.build_arguments([], "/work/project"), ["--root", os.path.normpath("/work/project")])

    def test_build_resolves_a_relative_project_path_and_keeps_options(self) -> None:
        """/* REQ-CLI-002: `wingbox build PATH` must pass an absolute --root and keep the builder options. */"""
        self.assertEqual(
            wingbox_cli.build_arguments(["services/api", "--full", "--exclude-dir", "generated"], "/work"),
            ["--root", os.path.normpath("/work/services/api"), "--full", "--exclude-dir", "generated"],
        )
        self.assertEqual(
            wingbox_cli.build_arguments(["--root", "/abs/project", "--lsp"], "/work"),
            ["--root", os.path.normpath("/abs/project"), "--lsp"],
        )

    def test_query_path_options_are_resolved_against_the_current_directory(self) -> None:
        """/* REQ-CLI-003: relative --map and --root values must become absolute paths. */"""
        self.assertEqual(
            wingbox_cli.absolutize_path_options(
                ["impact", "src/app.py", "--map", ".wingbox/index.sexp", "--root", "."], "/work/project"
            ),
            ["impact", "src/app.py", "--map", os.path.normpath("/work/project/.wingbox/index.sexp"), "--root",
             os.path.normpath("/work/project")],
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


def _layered_project(root: Path) -> None:
    """A small TypeScript backend: routes, services, models, repositories; a model imports a route."""
    (root / "package.json").write_text('{"name": "shop"}\n', encoding="utf-8")
    for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)):
        for index in range(count):
            path = root / "src" / folder / f"{folder}{index}.ts"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"export const {folder}{index} = {index};\n", encoding="utf-8")
    (root / "src" / "models" / "models0.ts").write_text(
        "import { routes0 } from '../routes/routes0';\nexport const models0 = routes0;\n", encoding="utf-8"
    )


def _run(arguments: list[str]) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output):
        status = wingbox_cli.main(arguments)
    return status, output.getvalue()


class WingboxInitTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        _layered_project(self.root)

    def test_init_prints_the_styles_and_writes_the_config(self) -> None:
        """/* REQ-CLI-008: `wingbox init` prints each part with its style, score and evidence, writes .wingbox/config.yaml with the style and the layer -> folders mapping (and the .wingbox/.gitignore of the build), creates nothing else, and ends with the next step: `wingbox learn` settles the open points (the agent's task in a coding agent, questions in a terminal), then build; nobody is told to edit the file by hand. */"""
        before = {path for path in self.root.rglob("*")}
        status, text = _run(["init", str(self.root)])
        self.assertEqual(0, status, text)
        self.assertIn("layered (Martin Fowler)   1.00   container", text)
        self.assertIn("routes/ 4", text)
        self.assertIn("wrote .wingbox/config.yaml\nnext: wingbox learn settles the open points", text)
        self.assertTrue(text.rstrip().endswith("then wingbox build"), text)
        self.assertNotIn("edit it", text)
        config = (self.root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("    style: layered", config)
        self.assertIn("      api: [routes]", config)
        self.assertIn("      infra: [repositories]", config)
        self.assertIn("    target:", config)
        self.assertIn("- {layer: domain, block: value-objects, folder: src/domain/value-objects, proposed: true}", config)
        self.assertIn("- {layer: api, folder: src/routes, present: [src/routes]}", config)
        self.assertIn("missing:", text)
        created = {path for path in self.root.rglob("*")} - before
        self.assertEqual({self.root / ".wingbox", self.root / ".wingbox" / "config.yaml", self.root / ".wingbox" / ".gitignore"}, created)

    def test_init_keeps_an_existing_config_unless_forced(self) -> None:
        """/* REQ-CLI-009: when .wingbox/config.yaml exists, `wingbox init` keeps it and still prints the candidates; --force rewrites it. */"""
        config = self.root / ".wingbox" / "config.yaml"
        config.parent.mkdir()
        config.write_text("parts:\n  .:\n    style: none\n", encoding="utf-8")
        status, text = _run(["init", str(self.root)])
        self.assertEqual(0, status)
        self.assertIn("kept .wingbox/config.yaml: the file exists; wingbox init --force rewrites it", text)
        self.assertIn("layered (Martin Fowler)", text)
        self.assertEqual("parts:\n  .:\n    style: none\n", config.read_text(encoding="utf-8"))
        status, text = _run(["init", str(self.root), "--force"])
        self.assertEqual(0, status)
        self.assertIn("style: layered", config.read_text(encoding="utf-8"))

    def test_init_uses_word_vectors_only_with_synonyms(self) -> None:
        """/* REQ-CLI-016: `wingbox init` maps a folder name that no dictionary holds only with --synonyms, and the config says which folder came from a synonym. */"""
        (self.root / "src" / "webhooks").mkdir()
        (self.root / "src" / "webhooks" / "hook0.ts").write_text("export const h = 1;\n", encoding="utf-8")
        self.assertEqual(0, _run(["init", str(self.root)])[0])
        config = self.root / ".wingbox" / "config.yaml"
        self.assertNotIn("webhooks", config.read_text(encoding="utf-8"))
        self.assertEqual(0, _run(["init", str(self.root), "--force", "--synonyms"])[0])
        self.assertIn("webhooks: synonym of hooks", config.read_text(encoding="utf-8"))

    def test_init_sexp_prints_the_facts_the_text_is_made_from(self) -> None:
        """/* REQ-CLI-010: `wingbox init --sexp` prints the S-expression facts (arch-part, arch-evidence, arch-config-written) for agents. */"""
        status, text = _run(["init", str(self.root), "--sexp"])
        self.assertEqual(0, status)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith('(arch-part "." :level container'), lines[0])
        self.assertIn('(arch-evidence "." layered "routes/" :files 4 :group presentation :match exact)', lines)
        self.assertEqual('(arch-config-written ".wingbox/config.yaml")', lines[-1])


class WingboxBuildArchitectureTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        _layered_project(self.root)

    def _boundaries(self) -> str:
        return _run(["boundaries", "all", "--map", str(self.root / ".wingbox" / "index.sexp"), "--root", str(self.root)])[1]

    def test_build_writes_the_index_into_dot_wingbox_with_labels_that_name_style_and_rule(self) -> None:
        """/* REQ-CLI-011: `wingbox build` writes .wingbox/index.sexp and .wingbox/cache.json (no file at the old names); every layer label and violation names the style and the rule, in the graph, `boundaries`, REPORT.md and the map. */"""
        status, text = _run(["build", str(self.root)])
        self.assertEqual(0, status, text)
        self.assertTrue((self.root / ".wingbox" / "index.sexp").is_file())
        self.assertTrue((self.root / ".wingbox" / "cache.json").is_file())
        self.assertFalse((self.root / ".code_graph.sexp").exists())
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn('(arch-part "." :style layered :state determined :origin detected', graph)
        self.assertRegex(graph, r'\(layer \S+ api :part "\." :style layered :rule "folder routes/ -> api" :match exact\)')
        boundaries = self._boundaries()
        self.assertIn('(violation src/models/models0 src/routes/routes0 :src-layer domain :tgt-layer api', boundaries)
        self.assertIn(':style layered :rule "domain -> api"', boundaries)
        report = (self.root / ".wingbox" / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("layered: domain -> api, domain imports API layer", report)
        self.assertIn("| `.` | container | layered | determined | detected |", report)
        page = (self.root / ".wingbox" / "map.html").read_text(encoding="utf-8")
        self.assertIn("layered: folder routes/ -\\u003e api", page)

    def test_build_follows_the_config_and_an_undetermined_part_has_no_violations(self) -> None:
        """/* REQ-CLI-012: with .wingbox/config.yaml, build takes styles and folders from it (match config); a part marked undetermined gets no layers from folder rules and no boundary violations, and boundaries says it was not checked instead of ok. */"""
        config = self.root / ".wingbox" / "config.yaml"
        config.parent.mkdir()
        config.write_text("parts:\n  .:\n    style: layered\n    layers:\n      api: [models]\n", encoding="utf-8")
        self.assertEqual(0, _run(["build", str(self.root)])[0])
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn(':style layered :rule "folder models/ -> api" :match config)', graph)
        self.assertIn('(arch-part "." :style layered :state configured :origin config', graph)
        config.write_text("parts:\n  .:\n    style: undetermined\n", encoding="utf-8")
        self.assertEqual(0, _run(["build", str(self.root)])[0])
        boundaries = self._boundaries()
        self.assertIn("(boundaries-not-checked :part . :reason style-undetermined", boundaries)
        self.assertNotIn("(boundaries-ok", boundaries)
        self.assertNotRegex((self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8"), r"\(layer \S+ (api|domain) ")

    def test_recommendations_reach_the_report_the_map_analyze_impact_and_init(self) -> None:
        """/* REQ-CLI-017: after a build, REPORT.md has a Recommendations section (fix-violation with the fix the style prescribes, missing folders of the target layout), the map carries them, `analyze` and `impact` print the advice that concerns the module, and `init` ends the part with its advice. */"""
        self.assertEqual(0, _run(["build", str(self.root)])[0])
        report = (self.root / ".wingbox" / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("## Recommendations", report)
        self.assertIn("| fix-violation | `src/models/models0.ts` | move the code src/models/models0.ts needs out of src/routes/", report)
        self.assertIn("| missing-folder | `src/domain/value-objects` |", report)
        self.assertIn('"advice":[["fix-violation"', (self.root / ".wingbox" / "map.html").read_text(encoding="utf-8"))
        graph = ["--map", str(self.root / ".wingbox" / "index.sexp"), "--root", str(self.root)]
        analyze = _run(["analyze", "src/models/models0"] + graph)[1]
        self.assertIn('(advice "." :id ', analyze)
        self.assertIn(":kind fix-violation :target \"src/models/models0.ts\" :style layered :rule \"domain -> api\"", analyze)
        self.assertIn(":kind fix-violation", _run(["impact", "src/routes/routes0.ts", "--sexp"] + graph)[1])
        text = _run(["init", str(self.root)])[1]
        self.assertIn("  advice: 1 to fix", text)
        self.assertIn("  - move the code src/models/models0.ts needs out of src/routes/", text)

    def test_queries_find_the_graph_in_dot_wingbox_of_a_parent_folder(self) -> None:
        """/* REQ-CLI-013: query commands find .wingbox/index.sexp in the current folder or a parent and resolve files against the folder above .wingbox/. */"""
        self.assertEqual(0, _run(["build", str(self.root)])[0])
        cwd = os.getcwd()
        os.chdir(self.root / "src" / "routes")
        try:
            status, text = _run(["impact", "src/routes/routes0.ts", "--sexp"])
        finally:
            os.chdir(cwd)
        self.assertEqual(0, status, text)
        self.assertIn("(impact src/routes/routes0 src/models/models0 :lang ts :via imports :depth 1)", text)



_DECLARED_LAYERS = """
/** Layers of the backend, lowest first. */
const layerTable = [
  { name: "core", path: "^src/(utils|db)/" },
  { name: "routes", path: "^src/routes/" },
];
export default { forbidden: layerTable.map((layer) => ({ name: `${layer.name}-imports-no-higher-layer` })) };
"""


class WingboxDeclaredRulesTest(unittest.TestCase):
    def test_a_part_with_its_own_layer_rules_is_judged_by_them_and_its_baseline_is_named(self) -> None:
        """/* REQ-CLI-014: a part that declares its layers (dependency-cruiser layers.mjs) is judged by them: init names the file, an import from a lower to a higher layer is a violation of the declared order, and a pair listed in the project's known exceptions is a violation-known with :known. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        backend = root / "backend"
        (backend / "src" / "utils").mkdir(parents=True)
        (backend / "src" / "routes").mkdir(parents=True)
        (backend / "package.json").write_text('{"name": "backend"}\n', encoding="utf-8")
        (backend / "layers.mjs").write_text(_DECLARED_LAYERS, encoding="utf-8")
        (backend / "layers.known-upward-imports.json").write_text(
            '["src/utils/a.ts -> src/routes/r.ts (core-imports-no-higher-layer)"]\n', encoding="utf-8"
        )
        (backend / "src" / "routes" / "r.ts").write_text("export const r = 1;\n", encoding="utf-8")
        (backend / "src" / "utils" / "a.ts").write_text("import { r } from '../routes/r';\nexport const a = r;\n", encoding="utf-8")
        (backend / "src" / "utils" / "b.ts").write_text("import { r } from '../routes/r';\nexport const b = r;\n", encoding="utf-8")

        status, text = _run(["init", str(root)])
        self.assertEqual(0, status, text)
        self.assertIn("backend  declared by backend/layers.mjs (2 layers, read)", text)
        self.assertIn("known exceptions: backend/layers.known-upward-imports.json (1 entries)", text)
        self.assertIn("declared: [backend/layers.mjs]", (root / ".wingbox" / "config.yaml").read_text(encoding="utf-8"))

        self.assertEqual(0, _run(["build", str(root)])[0])
        boundaries = _run(["boundaries", "all", "--map", str(root / ".wingbox" / "index.sexp"), "--root", str(root)])[1]
        known = [line for line in boundaries.splitlines() if line.startswith("(violation-known backend/src/utils/a ")]
        fresh = [line for line in boundaries.splitlines() if line.startswith("(violation backend/src/utils/b ")]
        self.assertEqual(1, len(known), boundaries)
        self.assertIn(':style declared :rule "core -> routes" :known backend/layers.known-upward-imports.json', known[0])
        self.assertEqual(1, len(fresh), boundaries)
        self.assertNotIn(":known", fresh[0])



class WingboxNestEntryPointsTest(unittest.TestCase):
    def test_entry_points_are_not_violations_and_a_utility_on_infrastructure_is_a_candidate(self) -> None:
        """/* REQ-CLI-015: in a NestJS part, a CLI command (nest-commander) and a worker bootstrap that imports the app module break no rule, and a utility that imports a repository is a candidate with the reason "utility depends on infrastructure; move it to services or invert", not a violation. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        files = {
            "package.json": '{"name": "server"}\n',
            "nest-cli.json": "{}\n",
            "src/app.module.ts": "import { AService0 } from './services/a0.service';\nexport class AppModule {}\n",
            "src/utils/asset.util.ts": "import { ARepository0 } from '../repositories/a0.repository';\nexport const util = ARepository0;\n",
            "src/commands/list-users.command.ts": (
                "import { Command, CommandRunner } from 'nest-commander';\n"
                "import { AService0 } from '../services/a0.service';\nexport class ListUsers {}\n"
            ),
            "src/workers/api.ts": "import { AppModule } from '../app.module';\nexport const start = AppModule;\n",
        }
        for i in range(4):
            files[f"src/controllers/a{i}.controller.ts"] = f"import {{ AService{i} }} from '../services/a{i}.service';\nexport class A{i} {{}}\n"
            files[f"src/services/a{i}.service.ts"] = f"import {{ ARepository{i} }} from '../repositories/a{i}.repository';\nexport class AService{i} {{}}\n"
            files[f"src/repositories/a{i}.repository.ts"] = f"export class ARepository{i} {{}}\n"
            files[f"src/dtos/a{i}.dto.ts"] = f"export class ADto{i} {{}}\n"
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        self.assertEqual(0, _run(["build", str(root)])[0])
        boundaries = _run(["boundaries", "all", "--map", str(root / ".wingbox" / "index.sexp"), "--root", str(root)])[1]
        self.assertFalse([line for line in boundaries.splitlines() if line.startswith("(violation ")], boundaries)
        candidate = [line for line in boundaries.splitlines() if line.startswith("(violation-candidate src/utils/asset.util ")]
        self.assertEqual(1, len(candidate), boundaries)
        self.assertIn('"utility depends on infrastructure; move it to services or invert"', candidate[0])



class WingboxDecisionsTest(unittest.TestCase):
    def test_three_builds_follow_learn_and_reject(self) -> None:
        """/* REQ-CLI-018: across builds of a project whose .wingbox/decisions.sexp was started (by init, learn, align --learn or advice), the file logs the advice given, a created folder as followed, a file moved elsewhere as done differently with the layer learned for that folder (the next build labels it :learned-from), and `wingbox advice reject` hides an item from `wingbox advice`. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        _layered_project(root)
        (root / "src" / "routes" / "paths.ts").write_text("export const PATHS = ['/a'];\n", encoding="utf-8")
        for index in range(2):
            (root / "src" / "services" / f"services{index}.ts").write_text(
                f"import {{ PATHS }} from '../routes/paths';\nexport const services{index} = PATHS;\n", encoding="utf-8"
            )
        # The log is started by the project's setup (init, learn, align --learn, advice); here it starts empty.
        (root / ".wingbox").mkdir(exist_ok=True)
        (root / ".wingbox" / "decisions.sexp").write_text("", encoding="utf-8")
        self.assertEqual(0, _run(["build", str(root)])[0])
        store = root / ".wingbox" / "decisions.sexp"
        first = store.read_text(encoding="utf-8")
        move = [line for line in first.splitlines() if ':kind move :target "src/routes/paths.ts"' in line]
        self.assertEqual(1, len(move), first)
        move_id = move[0].split()[1]
        folder_id = next(line.split()[1] for line in first.splitlines() if '"src/domain/value-objects"' in line)
        rejected_id = next(line.split()[1] for line in first.splitlines() if '"src/domain/aggregates"' in line)

        (root / "src" / "domain" / "value-objects").mkdir(parents=True)
        (root / "src" / "domain" / "value-objects" / "money.ts").write_text("export const money = 1;\n", encoding="utf-8")
        (root / "src" / "core").mkdir()
        (root / "src" / "routes" / "paths.ts").rename(root / "src" / "core" / "paths.ts")
        for index in range(2):
            (root / "src" / "services" / f"services{index}.ts").write_text(
                f"import {{ PATHS }} from '../core/paths';\nexport const services{index} = PATHS;\n", encoding="utf-8"
            )
        self.assertEqual(0, _run(["build", str(root)])[0])
        second = store.read_text(encoding="utf-8")[len(first):]
        self.assertIn(f"(advice-outcome {folder_id} :followed", second)
        self.assertIn(f'(advice-outcome {move_id} :done-differently :actual "src/core/paths.ts"', second)
        self.assertIn(f'(learned-layer "." "core" service :from {move_id}', second)

        self.assertEqual(0, _run(["build", str(root)])[0])
        graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertRegex(graph, rf'\(layer \S+ service :part "\." :style layered :rule "folder core/ -> service \(learned from {move_id}\)" :match learned :learned-from {move_id}\)')

        cwd = os.getcwd()
        os.chdir(root)
        try:
            status, text = _run(["advice", "reject", rejected_id, "--reason", "no aggregates here"])
            self.assertEqual(0, status, text)
            listed = _run(["advice", "--sexp"])[1]
            learned = _run(["advice", "--learned", "--sexp"])[1]
        finally:
            os.chdir(cwd)
        self.assertNotIn(rejected_id, listed)
        self.assertIn('(advice "." :id ', listed)
        self.assertIn(f'(learned-layer "." "core" service :from {move_id})', learned)


    def test_removing_a_learned_folder_from_the_config_undoes_it(self) -> None:
        """/* REQ-CLI-022: with .wingbox/config.yaml the config states the layers: a learned folder it does not list is not applied, and the build records the removal in the decisions store. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        _layered_project(root)
        (root / "src" / "core").mkdir()
        (root / "src" / "core" / "paths.ts").write_text("export const p = 1;\n", encoding="utf-8")
        (root / ".wingbox").mkdir()
        (root / ".wingbox" / "decisions.sexp").write_text('(learned-layer "." "core" service :from abc :at 2026-01-01)\n', encoding="utf-8")
        (root / ".wingbox" / "config.yaml").write_text("parts:\n  .:\n    style: layered\n    layers:\n      api: [routes]\n", encoding="utf-8")
        self.assertEqual(0, _run(["build", str(root)])[0])
        graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertNotIn(":match learned", graph)
        self.assertIn('(learned-removed "." "core" :by config', (root / ".wingbox" / "decisions.sexp").read_text(encoding="utf-8"))


class WingboxOutputTest(unittest.TestCase):
    def test_outputs_for_people_are_text_and_init_sexp_is_summarised(self) -> None:
        """/* REQ-CLI-024: init --sexp summarises the target and the missing folders per part (details with --details); align and place print text unless --sexp; place calls an existing empty folder present; advice prints one line for the parts without advice; outside git the build says plainly once that .gitignore rules are not applied. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        _layered_project(root)
        (root / "src" / "shared").mkdir()
        summary = _run(["init", str(root), "--sexp"])[1]
        self.assertIn('(arch-target-summary "." :present ', summary)
        self.assertNotIn("(arch-target \".\"", summary)
        self.assertIn("(arch-target \".\"", _run(["init", str(root), "--sexp", "--details"])[1])
        import contextlib

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(0, _run(["build", str(root)])[0])
        self.assertEqual(1, errors.getvalue().count("not a git repository: .gitignore rules are not applied"))
        cwd = os.getcwd()
        os.chdir(root)
        try:
            align_text = _run(["align"])[1]
            place_text = _run(["place", "a helper used by several layers"])[1]
        finally:
            os.chdir(cwd)
        self.assertFalse(align_text.startswith("("), align_text)
        self.assertIn("files placed", align_text)
        self.assertTrue(place_text.startswith("src/shared/  shared layer of layered"), place_text)
        self.assertNotIn("a new folder", place_text)


class WingboxPlaceDeclaredTest(unittest.TestCase):
    def test_place_in_a_part_with_declared_rules_names_their_layer_of_the_closest_folder(self) -> None:
        """/* REQ-CLI-023: `place` in a part with declared rules (asked with --part or from inside it) names the folder whose words match the request and the project's own layer of that folder, with example files; a style whose where is prose gives it as :where, never as a path. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        backend = root / "backend"
        for folder in ("routes", "utils"):
            (backend / "src" / folder).mkdir(parents=True)
            for name in ("a", "b"):
                (backend / "src" / folder / f"{name}.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (backend / "package.json").write_text('{"name": "backend"}\n', encoding="utf-8")
        (backend / "layers.mjs").write_text(_DECLARED_LAYERS, encoding="utf-8")
        self.assertEqual(0, _run(["init", str(root)])[0])
        cwd = os.getcwd()
        try:
            os.chdir(root)
            by_part = _run(["place", "a new route for users", "--part", "backend", "--sexp"])[1]
            os.chdir(backend / "src")
            inside = _run(["place", "a new route for users", "--sexp"])[1]
        finally:
            os.chdir(cwd)
        for text in (by_part, inside):
            self.assertIn('(place "backend" :layer routes :folder "backend/src/routes" :present :style declared', text)
            self.assertIn(':examples "backend/src/routes/a.ts backend/src/routes/b.ts"', text)
            self.assertIn(':may-import "not stated"', text)


class WingboxAlignTest(unittest.TestCase):
    def _in(self, root: Path, arguments: list[str]) -> tuple[int, str]:
        cwd = os.getcwd()
        os.chdir(root)
        try:
            return _run(arguments)
        finally:
            os.chdir(cwd)

    def test_align_reports_gaps_learns_and_then_reports_only_new_violations(self) -> None:
        """/* REQ-CLI-019: `wingbox align` prints the gaps of each part (placed files, rules followed, unplaced folders, broken rules), `--to` maps the layers onto another style, `--learn` writes the learned folders into .wingbox/config.yaml with a comment and the remaining violations into .wingbox/baseline.sexp, after which build and boundaries report only new violations. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        _layered_project(root)
        (root / "src" / "connectors").mkdir()
        for index in range(2):
            (root / "src" / "connectors" / f"g{index}.ts").write_text(f"export const g{index} = 1;\n", encoding="utf-8")
            (root / "src" / "services" / f"services{index}.ts").write_text(
                f"import {{ g{index} }} from '../connectors/g{index}';\nexport const services{index} = g{index};\n", encoding="utf-8"
            )
        self.assertEqual(0, _run(["build", str(root)])[0])
        status, gaps = self._in(root, ["align", "--sexp"])
        self.assertEqual(0, status, gaps)
        self.assertIn('(align-part "." :style layered :placed 14 :files 16', gaps)
        self.assertIn('(align-gap "." :kind unplaced-folder :target "connectors" :count 2', gaps)
        self.assertIn('(align-gap "." :kind rule :target "domain -> api" :count 1 :example "src/models/models0.ts -> src/routes/routes0.ts")', gaps)
        migration = self._in(root, ["align", "--to", "onion-clean", "--sexp"])[1]
        self.assertIn('(align-part "." :style onion-clean :from layered', migration)

        status, learned = self._in(root, ["align", "--learn", "--sexp"])
        self.assertEqual(0, status, learned)
        self.assertIn('(align-style "." :style layered :score ', learned)
        self.assertIn('(align-placed "." :layer api :folders "routes" :by style)', learned)
        self.assertIn('(align-learned "." :folder "connectors" :layer infra :by imports', learned)
        config = (root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("connectors", config)
        self.assertIn("connectors: learned by wingbox align (imports: ", config)
        self.assertIn('(place "." :layer api :folder "src/routes" :present', self._in(root, ["place", "a new REST endpoint for albums", "--sexp"])[1])
        baseline = (root / ".wingbox" / "baseline.sexp").read_text(encoding="utf-8")
        self.assertIn('(baseline "." src/models/models0 src/routes/routes0 :rule "domain -> api" :from align)', baseline)
        self.assertIn('(learned-layer "." "connectors" infra :from align', (root / ".wingbox" / "decisions.sexp").read_text(encoding="utf-8"))

        (root / "src" / "models" / "models1.ts").write_text(
            "import { routes1 } from '../routes/routes1';\nexport const models1 = routes1;\n", encoding="utf-8"
        )
        self.assertEqual(0, _run(["build", str(root)])[0])
        boundaries = self._in(root, ["boundaries", "all"])[1]
        self.assertIn("(violation-known src/models/models0 src/routes/routes0 ", boundaries)
        self.assertIn("(violation src/models/models1 src/routes/routes1 ", boundaries)
        self.assertIn("(boundaries-summary :violations 1 :known 1 ", boundaries)

    def test_align_learn_on_declared_rules_takes_the_project_baseline(self) -> None:
        """/* REQ-CLI-020: for a part with declared rules, `align --learn` guesses nothing: the baseline is the current breaks of the declared order, each marked with the project's own baseline file when it lists the pair, else align. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        backend = root / "backend"
        (backend / "src" / "utils").mkdir(parents=True)
        (backend / "src" / "routes").mkdir(parents=True)
        (backend / "package.json").write_text('{"name": "backend"}\n', encoding="utf-8")
        (backend / "layers.mjs").write_text(_DECLARED_LAYERS, encoding="utf-8")
        (backend / "layers.known-upward-imports.json").write_text(
            '["src/utils/a.ts -> src/routes/r.ts (core-imports-no-higher-layer)"]\n', encoding="utf-8"
        )
        (backend / "src" / "routes" / "r.ts").write_text("export const r = 1;\n", encoding="utf-8")
        for name in ("a", "b"):
            (backend / "src" / "utils" / f"{name}.ts").write_text(f"import {{ r }} from '../routes/r';\nexport const {name} = r;\n", encoding="utf-8")
        self.assertEqual(0, _run(["build", str(root)])[0])
        status, text = self._in(root, ["align", "--learn", "--sexp"])
        self.assertEqual(0, status, text)
        self.assertIn('(align-learned-none "backend" :reason "declared rules: nothing is guessed, the baseline comes from them")', text)
        baseline = (root / ".wingbox" / "baseline.sexp").read_text(encoding="utf-8")
        self.assertIn('(baseline "backend" backend/src/utils/a backend/src/routes/r :rule "core -> routes" :from "backend/layers.known-upward-imports.json")', baseline)
        self.assertIn('(baseline "backend" backend/src/utils/b backend/src/routes/r :rule "core -> routes" :from align)', baseline)



class WingboxPlaceTest(unittest.TestCase):
    def test_place_names_the_layer_and_the_folder_present_or_new(self) -> None:
        """/* REQ-CLI-021: `wingbox place "<what>"` names the layer of the part's style for the request, the folder (present, or new from the target layout) and what that layer may import; a request with no matching words is answered with place-none. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        _layered_project(root)
        self.assertEqual(0, _run(["init", str(root)])[0])
        cwd = os.getcwd()
        os.chdir(root)
        try:
            client = _run(["place", "a client for the payment database", "--sexp"])[1]
            entity = _run(["place", "an Order entity", "--sexp"])[1]
            nothing = _run(["place", "banana giraffe", "--sexp"])[1]
        finally:
            os.chdir(cwd)
        self.assertIn('(place "." :layer infra :folder "src/repositories" :present :style layered', client)
        self.assertIn(':may-import "domain shared config"', client)
        self.assertIn(':examples "src/repositories/repositories0.ts src/repositories/repositories1.ts src/repositories/repositories2.ts"', client)
        self.assertIn('(place "." :layer domain :folder "src/models" :present', entity)
        self.assertIn('(place-none "." :reason', nothing)


if __name__ == "__main__":
    unittest.main()
