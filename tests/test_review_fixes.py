"""Findings of an independent run of the architecture branch, one test per finding."""
import unittest

from scripts.architecture import decisions
from scripts.architecture.advice import Advice, advice_id
from scripts.architecture.detect import is_test_file
from scripts.architecture.labels import label_file
from scripts.architecture.library import load_library
from scripts.architecture.plan import LayerFolder, PartPlan, RepositoryPlan, apply_learned, detect_plan
from scripts.architecture.target import target_layout

LIBRARY = load_library()


def _advice(kind: str, target: str, rule: str) -> Advice:
    return Advice(advice_id(".", kind, target, rule), ".", kind, target, "why", "layered", rule, "evidence")


def _part(style: str, layers: tuple, kind: str = "npm package") -> PartPlan:
    return PartPlan(path=".", kind=kind, level="container", files=20, style=style, state="configured", origin="config",
                    score=None, judgement=None, layers=layers, routing_root=(), declared=None)


class DecisionsStateTest(unittest.TestCase):
    def test_accepted_advice_is_never_counted_as_ignored(self) -> None:
        """/* REQ-ARCH-032: an accepted item stays shown: later builds, each of a changed graph, do not count it as ignored. */"""
        item = _advice("missing-folder", "src/domain/aggregates", "target domain/aggregates")
        text = "\n".join(decisions.reconcile(decisions.Store(), [item], (), "2026-01-01", "g1"))
        text += "\n" + decisions.user_outcome(decisions.read_store(text), item.id, True, "", "2026-01-01")
        for day in (2, 3, 4, 5):
            text += "\n" + "\n".join(decisions.reconcile(decisions.read_store(text), [item], (), f"2026-01-0{day}", f"g{day}"))
        self.assertNotIn(":ignored", text)
        self.assertEqual([item], decisions.visible(decisions.read_store(text), [item]))

    def test_the_store_logs_changes_of_state_not_every_run(self) -> None:
        """/* REQ-ARCH-033: a build logs one run line, new advice and changed states; an open item adds nothing per build until it becomes hidden after 3 ignored builds of a changed graph, which is logged once. */"""
        items = [_advice("missing-folder", f"src/domain/b{i}", f"target domain/b{i}") for i in range(50)]
        text = "\n".join(decisions.reconcile(decisions.Store(), items, (), "2026-01-01", "g1"))
        sizes = []
        for day in (2, 3, 4, 5, 6):
            lines = decisions.reconcile(decisions.read_store(text), items, (), f"2026-01-0{day}", f"g{day}")
            sizes.append(len(lines))
            text += "\n" + "\n".join(lines)
        self.assertEqual([1, 1, 51, 1, 1], sizes)
        self.assertEqual([], decisions.visible(decisions.read_store(text), items))


class LabelsTest(unittest.TestCase):
    def test_test_folder_words_and_shared_inside_a_layer(self) -> None:
        """/* REQ-ARCH-034: a folder whose name starts with the word test, tests, spec or e2e (tests-integration, test-utils) is a test folder, a feature named after tests (openappa-policy-tests) is not; a shared or common folder inside a layer folder belongs to that layer (services/shared is service), a utils folder stays a utility. */"""
        for path in ("tests-integration/helpers/db.ts", "src/test-utils/render.ts", "src/e2e-cases/login.ts"):
            self.assertTrue(is_test_file(path, LIBRARY), path)
        for path in ("src/contest/latest.ts", "src/routes/openappa-policy-tests/openappa-policy-tests.routes.ts"):
            self.assertFalse(is_test_file(path, LIBRARY), path)
        part = _part("layered", (LayerFolder("services", "service", "exact", "services", 3),
                                 LayerFolder("shared", "shared", "exact", "shared", 1),
                                 LayerFolder("utils", "shared", "exact", "utils", 1),
                                 LayerFolder("routes", "api", "exact", "routes", 1)))
        plan = RepositoryPlan(parts=(part,), roots=("",))
        self.assertEqual("service", label_file("src/services/shared/user-methods.ts", plan, LIBRARY).layer)
        self.assertEqual("shared", label_file("src/shared/money.ts", plan, LIBRARY).layer)
        self.assertEqual("shared", label_file("src/routes/proxy/utils/x.ts", plan, LIBRARY).layer)


class TargetAndLearningTest(unittest.TestCase):
    def test_a_learned_folder_never_displaces_the_style_folder_of_a_layer(self) -> None:
        """/* REQ-ARCH-035: when a layer has learned or configured folders besides the style's own (controllers), the target and place name the style's own folder first. */"""
        part = _part("layered", (LayerFolder("bin", "api", "learned", "align: x", 0), LayerFolder("commands", "api", "config", "", 0),
                                 LayerFolder("controllers", "api", "config", "", 0)))
        paths = ("bin/run.ts", "src/bin/cli.ts", "src/commands/a.ts", "src/controllers/user.controller.ts")
        api = next(item for item in target_layout(part, paths, LIBRARY) if item.layer == "api" and not item.block)
        self.assertEqual("src/controllers", api.present[0])

    def test_the_config_wins_over_learned_layers(self) -> None:
        """/* REQ-ARCH-036: learned layers of the decisions store apply only when there is no .wingbox/config.yaml; with a config file, the config states the layers. */"""
        part = _part("layered", (LayerFolder("routes", "api", "config", "", 0),))
        learned = {".": [decisions.Learned(".", "core", "service", "abc")]}
        self.assertEqual(part.layers, apply_learned(RepositoryPlan((part,), ("",)), learned, from_config=True).parts[0].layers)
        self.assertEqual("core", apply_learned(RepositoryPlan((part,), ("",)), learned, from_config=False).parts[0].layers[0].folder)


class DomainBlocksTest(unittest.TestCase):
    def test_domain_blocks_only_with_a_domain_layer_and_domain_code(self) -> None:
        """/* REQ-ARCH-037: the domain building blocks are proposed only for a style with a domain layer and a part that has domain code; in Python and Rust their folders are snake_case. */"""
        frontend = detect_plan(("package.json",) + tuple(f"src/app/r{i}/page.tsx" for i in range(6))
                               + tuple(f"src/components/c{i}.tsx" for i in range(6)), lambda path: None, LIBRARY)
        self.assertEqual("route-tree", frontend.parts[0].style)
        self.assertEqual([], [i for i in target_layout(frontend.parts[0], ("src/app/r0/page.tsx",), LIBRARY) if i.block])
        no_domain = _part("layered", (LayerFolder("routes", "api", "config", "", 0),))
        self.assertEqual([], [i for i in target_layout(no_domain, ("src/routes/a.ts",), LIBRARY) if i.block])
        python = _part("layered", (LayerFolder("domain", "domain", "config", "", 0),), kind="Python project")
        folders = {i.folder for i in target_layout(python, ("src/domain/order.py",), LIBRARY) if i.block}
        self.assertIn("src/domain/value_objects", folders)



class TypeOnlyPairTest(unittest.TestCase):
    def test_a_pair_imported_for_types_only_is_listed_once(self) -> None:
        """/* REQ-ARCH-038: a pair whose import is for types only is a violation-type-only, even when a data-flow edge joins the same files; it is never also a candidate or a violation. */"""
        import io
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from scripts import code_graph_search as search

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sexp"
            path.write_text(
                "(code-graph :schema 1 :files 2 :nodes 2 :edges 2 :violations 0)\n(paths\n  (src\n    (models\n      (m0 order)\n    )\n"
                "    (repositories\n      (m1 orders)\n    )\n  )\n)\n(node m0 ts \"general module\")\n(node m1 ts \"general module\")\n"
                "(imports m0 m1 :type-only)\n(data-flow m0 m1)\n",
                encoding="utf-8",
            )
            graph = search.CodeGraph()
            graph.load(str(path), directory)
            output = io.StringIO()
            with redirect_stdout(output):
                search.cmd_boundaries(graph, "all")
        lines = output.getvalue().splitlines()
        self.assertEqual(["violation-type-only"], sorted({line.split()[0][1:] for line in lines if line.startswith("(violation")}))
        self.assertIn("(boundaries-summary :violations 0 :known 0 :type-only 1 :candidates 0 :checked 2)", lines)



class AdviceAndWordingTest(unittest.TestCase):
    def test_one_advice_item_per_pair_and_a_readable_not_read_reason(self) -> None:
        """/* REQ-ARCH-041: an import and a data-flow edge of one pair give one advice item; a layer file without a layer order is "not read: it states no layer order". */"""
        from scripts.architecture.advice import ModuleFact, PartRules, Violation, advise
        from scripts.architecture.declared import read_layer_table

        style = LIBRARY.style("layered")
        part = PartRules(".", "layered", "determined", {l.id: l.rank for l in style.layers},
                         {(d.source, d.target): (d.reason, d.severity, d.fix) for d in style.denies})
        modules = {m: ModuleFact(m, m + ".ts", ".", layer, True) for m, layer in (("src/models/a", "domain"), ("src/routes/b", "api"))}
        twice = (Violation("src/models/a", "src/routes/b", "domain", "api", ".", "layered", "domain imports API layer", False),) * 2
        advice, _ = advise((part,), modules, {}, {}, twice, (), {})
        self.assertEqual(1, len(advice))
        self.assertEqual("it states no layer order", read_layer_table("x/.dependency-cruiser.mjs", "export default {}").reason)



class BuildSpeedTest(unittest.TestCase):
    def test_the_type_only_check_resolves_no_import_again(self) -> None:
        """/* REQ-CS-060: the type-only check of an import takes the imports the build resolved once per file and resolves none again (a cached rebuild of a large monorepo spent most of its time there). */"""
        from types import SimpleNamespace

        from scripts import build_code_graph

        class Counting:
            calls = 0

            def resolve_import(self, specifier, path):
                Counting.calls += 1
                return "src/b"

        source = SimpleNamespace(import_forms={"./b": "named"}, imports=["./b", "./c"], path="src/a.ts",
                                 imported_names=[{"module": "./b", "original": "B", "local": "B"}])
        target = SimpleNamespace(entities=[{"name": "B", "kind": "interface"}])
        self.assertTrue(build_code_graph._imports_types_only(source, "src/b", target, Counting(), {"./b": "src/b", "./c": "src/c"}))
        self.assertEqual(0, Counting.calls)


if __name__ == "__main__":
    unittest.main()
