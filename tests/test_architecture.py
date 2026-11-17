"""Architecture styles: matching folder names, parts, detection, labels, declared rules, config."""
import unittest

from scripts.architecture import words
from scripts.architecture.config import ConfigErr, ConfigOk, parse_config, plan_from_config, render_config
from scripts.architecture.declared import DeclaredLayers, NotRead, read_layer_table
from scripts.architecture.detect import split_parts
from scripts.architecture.labels import label_file, part_denies
from scripts.architecture.library import load_library
from scripts.architecture.plan import detect_plan
from scripts.architecture.init import init_facts, render_text

LIBRARY = load_library()


def _files(*groups: tuple[str, int]) -> tuple[str, ...]:
    """Paths `folder/file<i>.ts` for each (folder, count)."""
    return tuple(f"{folder}/file{i}.ts" for folder, count in groups for i in range(count))


def _reader(texts: dict[str, str]):
    return lambda path: texts.get(path)


def _layer_role(word: str, style_id: str, synonyms: bool = True):
    style = LIBRARY.style(style_id)
    from scripts.architecture.detect import layer_dictionary

    settings = words.MatchSettings() if synonyms else words.DEFAULT_SETTINGS
    match = words.match_name(word, layer_dictionary(style), settings)
    return (match.role, match.step) if match else None


class WordMatchingTest(unittest.TestCase):
    def test_names_match_whole_normalised_segments_only(self) -> None:
        """/* REQ-ARCH-001: a folder name matches a style word only as a whole normalised segment: use-cases, UseCases and use_cases are one name; s3, class3, utils3d and api-gateways-old match nothing. */"""
        self.assertEqual(("application", "exact"), _layer_role("use-cases", "onion-clean"))
        self.assertEqual(("application", "exact"), _layer_role("UseCases", "onion-clean"))
        self.assertEqual(("domain", "exact"), _layer_role("Shop.Domain", "onion-clean"))
        for name in ("s3", "class3", "api-gateways-old", "rabbitmq"):
            self.assertIsNone(_layer_role(name, "layered"), name)
        self.assertNotEqual("infra", (_layer_role("utils3d", "layered") or ("", ""))[0])

    def test_spelling_variants_match_by_levenshtein(self) -> None:
        """/* REQ-ARCH-002: a long folder name one edit away from a style word matches it as a spelling variant; short names do not. */"""
        self.assertEqual(("api", "spelling"), _layer_role("controlers", "layered"))
        self.assertEqual(("infra", "spelling"), _layer_role("repositorys", "layered"))
        self.assertNotEqual("spelling", (_layer_role("stories", "layered") or ("", ""))[1])

    def test_architecture_synonyms_map_to_their_layer(self) -> None:
        """/* REQ-ARCH-003: persistence -> infra, usecases and interactor -> application, presenter -> interface adapters, viewmodel -> ui. */"""
        self.assertEqual("infra", _layer_role("persistence", "layered")[0])
        self.assertEqual("application", _layer_role("usecases", "onion-clean")[0])
        self.assertEqual("application", _layer_role("interactor", "onion-clean")[0])
        self.assertEqual("interface", _layer_role("presenter", "onion-clean")[0])
        self.assertEqual("ui", _layer_role("viewmodel", "layered")[0])
        # Names in no dictionary, matched by the built-in word vectors.
        self.assertEqual(("domain", "synonym"), _layer_role("schema", "nestjs"))
        self.assertEqual(("ui", "synonym"), _layer_role("shared-components", "route-tree"))

    def test_unrelated_words_stay_unmatched(self) -> None:
        """/* REQ-ARCH-004: words with no architecture meaning match no layer of any style. */"""
        for word in ("banana", "giraffe", "sheets", "formula", "mountain"):
            for style in LIBRARY.styles:
                if style.layers:
                    self.assertIsNone(_layer_role(word, style.id), (word, style.id))

    def test_synonyms_are_off_unless_asked_for(self) -> None:
        """/* REQ-ARCH-022: by default a folder name matches only exactly or as a spelling variant; the word-vector step runs only with --synonyms (it did not beat the dictionaries on the held-out half of benchmarks/architecture). */"""
        self.assertFalse(words.DEFAULT_SETTINGS.synonyms)
        self.assertIsNone(_layer_role("schema", "nestjs", synonyms=False))
        self.assertEqual(("domain", "synonym"), _layer_role("schema", "nestjs", synonyms=True))

    def test_matching_is_deterministic(self) -> None:
        """/* REQ-ARCH-005: matching the same names twice gives the same roles, steps and similarities. */"""
        names = ("datastore", "persistence", "usecases", "banana", "controlers", "handlers")
        first = [_layer_role(name, "layered") for name in names]
        words._match_cached.cache_clear()
        words.name_vector.cache_clear()
        self.assertEqual(first, [_layer_role(name, "layered") for name in names])


class PartsAndDetectionTest(unittest.TestCase):
    def test_parts_start_at_manifests_and_small_parts_are_not_judged(self) -> None:
        """/* REQ-ARCH-006: a part starts at a folder with a manifest and ends at a nested part; the root is a part; a pom.xml below another is the same application. */"""
        paths = (
            ("package.json", "apps/web/package.json", "apps/web/src/a.ts", "lib/x.ts", "pom.xml",
             "mod/pom.xml", "mod/src/A.java", "crates/c/Cargo.toml", "crates/c/src/lib.rs")
        )
        parts = {part.path: part.files for part in split_parts(paths, LIBRARY)}
        self.assertEqual({".": ("lib/x.ts", "mod/src/A.java"), "apps/web": ("src/a.ts",), "crates/c": ("src/lib.rs",)}, parts)

    def test_layered_backend_is_detected_and_its_folders_mapped(self) -> None:
        """/* REQ-ARCH-007: a part with routes, services, models and repositories folders is layered; the folders map to api, service, domain and infra. */"""
        paths = ("package.json",) + _files(("src/routes", 8), ("src/services", 8), ("src/models", 6), ("src/repositories", 4))
        plan = detect_plan(paths, _reader({}), LIBRARY)
        part = plan.parts[0]
        self.assertEqual(("layered", "determined"), (part.style, part.state))
        self.assertEqual(
            {"routes": "api", "services": "service", "models": "domain", "repositories": "infra"},
            {folder.folder: folder.layer for folder in part.layers},
        )
        label = label_file("src/routes/file0.ts", plan, LIBRARY)
        self.assertEqual(("api", "layered", "folder routes/ -> api", "exact"), (label.layer, label.style, label.rule, label.match))

    def test_a_route_tree_frontend_never_fires_backend_folder_rules(self) -> None:
        """/* REQ-ARCH-008: below the routing root of a route-tree frontend, folders such as gateways are URL segments: the files are route or feature code, never infrastructure. */"""
        paths = ("package.json",) + tuple(f"src/app/r{i}/page.tsx" for i in range(6)) + _files(
            ("src/components", 6), ("src/app/mcp/gateways", 3), ("src/lib", 3)
        )
        plan = detect_plan(paths, _reader({}), LIBRARY)
        self.assertEqual("route-tree", plan.parts[0].style)
        self.assertEqual("feature", label_file("src/app/mcp/gateways/file0.ts", plan, LIBRARY).layer)
        self.assertEqual("route", label_file("src/app/r1/page.tsx", plan, LIBRARY).layer)
        self.assertEqual("ui", label_file("src/components/file0.ts", plan, LIBRARY).layer)

    def test_route_tree_reads_app_wide_private_folders_and_state_modules(self) -> None:
        """/* REQ-ARCH-021: in a route-tree frontend, a private folder at the top of the routing root (app/_parts) is shared by the routes (ui), a deeper one belongs to its route (feature); a query, mutation, store, context or use-* module outside the root is state; a utility that imports a component or state is a candidate, not a violation. */"""
        paths = ("package.json",) + tuple(f"src/app/r{i}/page.tsx" for i in range(6)) + _files(
            ("src/components", 6), ("src/lib", 6), ("src/app/_parts", 2), ("src/app/r1/_parts", 2)
        ) + ("src/lib/agent.query.ts", "src/lib/chat/use-chat.ts", "src/lib/auth/session.store.ts")
        plan = detect_plan(paths, _reader({}), LIBRARY)
        self.assertEqual("ui", label_file("src/app/_parts/file0.ts", plan, LIBRARY).layer)
        self.assertEqual("feature", label_file("src/app/r1/_parts/file0.ts", plan, LIBRARY).layer)
        for path in ("src/lib/agent.query.ts", "src/lib/chat/use-chat.ts", "src/lib/auth/session.store.ts"):
            self.assertEqual("state", label_file(path, plan, LIBRARY).layer, path)
        self.assertEqual("shared", label_file("src/lib/file0.ts", plan, LIBRARY).layer)
        severities = {(d.source, d.target): d.severity for d in part_denies(plan.parts[0], LIBRARY)}
        self.assertEqual("candidate", severities[("shared", "ui")])
        self.assertEqual("violation", severities[("ui", "feature")])

    def test_a_plugin_package_is_microkernel_and_its_folders_are_not_layers(self) -> None:
        """/* REQ-ARCH-009: a package with src/plugin.ts and commands, controllers, menu, views folders is a microkernel plug-in: no folder becomes a backend layer and no rule applies. */"""
        paths = ("package.json", "src/plugin.ts") + _files(
            ("src/commands", 5), ("src/controllers", 5), ("src/menu", 3), ("src/views", 3), ("src/services", 3)
        )
        plan = detect_plan(paths, _reader({}), LIBRARY)
        part = plan.parts[0]
        self.assertEqual("microkernel", part.style)
        self.assertEqual("other", label_file("src/menu/file0.ts", plan, LIBRARY).layer)
        self.assertEqual((), part_denies(part, LIBRARY))

    def test_weak_evidence_leaves_the_part_without_layers(self) -> None:
        """/* REQ-ARCH-010: a part whose folders support no style (a library) has style none: no layers, no rules. */"""
        paths = ("pyproject.toml",) + tuple(f"lib/{name}/m{i}.py" for name in ("security", "openapi", "encoders") for i in range(5))
        plan = detect_plan(paths, _reader({}), LIBRARY)
        part = plan.parts[0]
        self.assertIn(part.style, ("none", "undetermined"))
        self.assertEqual("other", label_file("lib/security/m0.py", plan, LIBRARY).layer)
        self.assertEqual((), part_denies(part, LIBRARY))


LAYERS_MJS = """
/** The layers of the backend, lowest first. */
const layerTable = [
  { name: "schema", path: "^src/(types/|db/schema\\\\.ts$)", project: APP },
  {
    // pinned files
    name: "core",
    path: "^src/(utils|db)/",
    files: ["src/routes/route-paths.ts"],
  },
  { name: "routes", path: "^src/routes/" },
];
export default { forbidden: layers.map((layer) => ({ name: `${layer.name}-imports-no-higher-layer` })) };
"""


class DeclaredRulesTest(unittest.TestCase):
    def test_a_dependency_cruiser_layer_table_is_read_in_order_with_pinned_files(self) -> None:
        """/* REQ-ARCH-011: a layer list of plain data is read lowest first; a pinned file belongs to the layer that pins it. */"""
        table = read_layer_table("backend/layers.mjs", LAYERS_MJS)
        self.assertIsInstance(table, DeclaredLayers)
        self.assertEqual(("schema", "core", "routes"), tuple(layer.name for layer in table.layers))
        self.assertEqual("core", table.layer_of("src/routes/route-paths.ts"))
        self.assertEqual("routes", table.layer_of("src/routes/chat.ts"))
        self.assertEqual("schema", table.layer_of("src/db/schema.ts"))

    def test_a_layer_table_with_computed_entries_is_not_read(self) -> None:
        """/* REQ-ARCH-012: a layer entry that is not plain data (a spread) makes the file "not read". */"""
        text = LAYERS_MJS.replace('{ name: "routes", path: "^src/routes/" }', "{ ...routesLayer }")
        self.assertIsInstance(read_layer_table("backend/layers.mjs", text), NotRead)

    def test_a_part_with_its_own_rules_is_judged_by_them(self) -> None:
        """/* REQ-ARCH-013: a part that declares its layers is labelled by them and forbids imports from a lower to a higher layer. */"""
        paths = ("backend/package.json", "backend/layers.mjs") + tuple(
            f"backend/src/{folder}/f{i}.ts" for folder in ("utils", "routes", "services", "models") for i in range(4)
        )
        plan = detect_plan(paths, _reader({"backend/layers.mjs": LAYERS_MJS}), LIBRARY)
        part = next(p for p in plan.parts if p.path == "backend")
        self.assertEqual(("declared", "declared"), (part.style, part.state))
        label = label_file("backend/src/utils/f0.ts", plan, LIBRARY)
        self.assertEqual(("core", "declared", "backend/layers.mjs: layer core"), (label.layer, label.style, label.rule))
        denies = {(deny.source, deny.target) for deny in part_denies(part, LIBRARY)}
        self.assertIn(("core", "routes"), denies)
        self.assertNotIn(("routes", "core"), denies)


class TestFileTest(unittest.TestCase):
    def test_test_helpers_named_after_tests_are_tests(self) -> None:
        """/* REQ-ARCH-018: a file named x.test.ts, x.spec.ts, x_test.go, test_x.py, x.test-helpers.ts or x.test-data.ts, or in a tests, __tests__, __mocks__, fixtures or e2e folder, is a test file. */"""
        from scripts.architecture.detect import is_test_file

        for path in ("src/a.test.ts", "src/a.spec.tsx", "pkg/a_test.go", "pkg/test_a.py", "src/routes/x/x.test-helpers.ts",
                     "src/r/run.test-data.ts", "tests/a.py", "src/__tests__/a.ts", "src/__mocks__/db.ts", "e2e/login.ts"):
            self.assertTrue(is_test_file(path, LIBRARY), path)
        for path in ("src/contest/latest.ts", "src/testing-library-setup.ts", "src/attestation.ts"):
            self.assertFalse(is_test_file(path, LIBRARY), path)


class CompositionRootTest(unittest.TestCase):
    def test_a_composition_root_is_labelled_but_marked(self) -> None:
        """/* REQ-ARCH-017: main.ts, app.module.ts and Program.cs are composition roots: they keep their layer and are marked, so that the boundary rules let them import every layer. */"""
        from scripts.architecture.labels import is_composition_root

        self.assertTrue(is_composition_root("src/main.ts", LIBRARY))
        self.assertTrue(is_composition_root("src/app.module.ts", LIBRARY))
        self.assertTrue(is_composition_root("Program.cs", LIBRARY))
        self.assertTrue(is_composition_root("src/index.ts", LIBRARY))
        self.assertFalse(is_composition_root("src/routes/chat/index.ts", LIBRARY))
        self.assertFalse(is_composition_root("src/domain/mainframe.ts", LIBRARY))


class CqrsAndEntryPointsTest(unittest.TestCase):
    def _nest(self, extra: tuple[str, ...]) -> tuple[str, ...]:
        return ("package.json", "nest-cli.json") + tuple(
            f"src/{folder}/{name}{i}.{kind}.ts"
            for folder, name, kind in (("controllers", "a", "controller"), ("services", "a", "service"),
                                       ("repositories", "a", "repository"), ("modules", "a", "module"), ("dtos", "a", "dto"))
            for i in range(4)
        ) + extra

    def test_command_files_are_cqrs_commands_only_when_the_part_has_the_cqrs_overlay(self) -> None:
        """/* REQ-ARCH-019: without its own CQRS evidence (commands with queries and handlers) a part does not read *.command.ts or commands/ as CQRS commands of the domain; with it, it does. */"""
        plain = detect_plan(self._nest(("src/commands/list-users.command.ts", "src/commands/version.command.ts")), _reader({}), LIBRARY)
        self.assertEqual("nestjs", plain.parts[0].style)
        self.assertEqual((), plain.parts[0].overlays)
        self.assertNotEqual("domain", label_file("src/commands/list-users.command.ts", plain, LIBRARY).layer)
        cqrs_files = tuple(f"src/application/{side}/{name}{i}.{kind}.ts" for side, name, kind in
                           (("commands", "c", "command"), ("queries", "q", "query"), ("handlers", "h", "handler")) for i in range(5))
        cqrs = detect_plan(self._nest(cqrs_files), _reader({}), LIBRARY)
        self.assertIn("cqrs", cqrs.parts[0].overlays)
        self.assertEqual("domain", label_file("src/application/commands/c0.command.ts", cqrs, LIBRARY).layer)

    def test_a_file_that_starts_the_application_is_a_composition_root(self) -> None:
        """/* REQ-ARCH-020: a file that imports NestFactory from @nestjs/core, or Command or CommandRunner from nest-commander, starts the application: it is a composition root whatever its name. */"""
        from scripts.architecture.labels import label_in_part

        plan = detect_plan(self._nest(("src/workers/api.ts", "src/commands/list-users.command.ts")), _reader({}), LIBRARY)
        part = plan.parts[0]
        worker = label_in_part("src/workers/api.ts", part, LIBRARY, frozenset({"@nestjs/core:NestFactory"}))
        command = label_in_part("src/commands/list-users.command.ts", part, LIBRARY, frozenset({"nest-commander:CommandRunner"}))
        plain = label_in_part("src/services/a0.service.ts", part, LIBRARY, frozenset({"@nestjs/common:Injectable"}))
        self.assertTrue(worker.root and command.root)
        self.assertIn("composition root", worker.rule)
        self.assertFalse(plain.root)


class TargetLayoutTest(unittest.TestCase):
    def test_the_target_layout_lists_present_and_proposed_folders_with_the_domain_blocks(self) -> None:
        """/* REQ-ARCH-023: the target layout of a style lists every layer folder and every domain building block (Evans 2003, Vernon 2013); folders the part has are present with their real path, the others proposed where the style puts them. */"""
        from scripts.architecture.target import target_layout

        paths = ("package.json",) + _files(
            ("src/routes", 6), ("src/services", 6), ("src/models", 4), ("src/domain/entities", 3)
        )
        plan = detect_plan(paths, _reader({}), LIBRARY)
        part = plan.parts[0]
        self.assertEqual("layered", part.style)
        items = {(item.layer, item.block): item for item in target_layout(part, paths, LIBRARY)}
        self.assertEqual(("src/routes",), items[("api", "")].present)
        self.assertEqual((), items[("infra", "")].present)
        self.assertEqual("src/repositories", items[("infra", "")].folder)
        self.assertEqual(("src/domain/entities",), items[("domain", "entities")].present)
        self.assertEqual("src/domain/value-objects", items[("domain", "value-objects")].folder)
        self.assertEqual((), items[("domain", "value-objects")].present)
        self.assertIn("Evans", items[("domain", "aggregates")].source)
        blocks = {item.block for item in target_layout(part, paths, LIBRARY) if item.block}
        self.assertEqual({"entities", "value-objects", "aggregates", "domain-events", "domain-services",
                          "repository-interfaces", "specifications"}, blocks)

    def test_hexagonal_puts_repository_interfaces_in_the_out_ports(self) -> None:
        """/* REQ-ARCH-024: in the hexagonal target the repository interfaces are out ports of the application (application/ports/out), not a domain folder. */"""
        from scripts.architecture.target import target_layout

        paths = ("pom.xml",) + _files(("src/application/port/in", 4), ("src/adapter/in/web", 4), ("src/domain", 6), ("src/adapter/out/persistence", 4))
        plan = detect_plan(paths, _reader({}), LIBRARY)
        part = plan.parts[0]
        self.assertEqual("hexagonal", part.style)
        items = {(item.layer, item.block): item for item in target_layout(part, paths, LIBRARY)}
        self.assertEqual("src/application/ports/out", items[("application", "repository-interfaces")].folder)


class AdviceTest(unittest.TestCase):
    def _part(self, state: str = "determined") -> "object":
        from scripts.architecture.advice import PartRules

        style = LIBRARY.style("layered")
        return PartRules(
            path=".", style="layered", state=state, ranks={layer.id: layer.rank for layer in style.layers},
            denies={(d.source, d.target): (d.reason, d.severity, d.fix) for d in style.denies},
        )

    def test_each_kind_of_advice_names_style_rule_and_evidence(self) -> None:
        """/* REQ-ARCH-025: advice is deterministic and of three kinds: move (a file its dependents import against the rules, with the counts), fix-violation (with the fix the style prescribes) and missing-folder (from the target layout); each names style and rule and has a stable id. */"""
        from scripts.architecture.advice import ModuleFact, TargetFact, Violation, advise

        modules = {
            "src/routes/paths": ModuleFact("src/routes/paths", "src/routes/paths.ts", ".", "api", True),
            "src/services/a": ModuleFact("src/services/a", "src/services/a.ts", ".", "service", True),
            "src/services/b": ModuleFact("src/services/b", "src/services/b.ts", ".", "service", True),
            "src/models/order": ModuleFact("src/models/order", "src/models/order.ts", ".", "domain", True),
            "src/db/orders": ModuleFact("src/db/orders", "src/db/orders.ts", ".", "infra", True),
        }
        dependents = {"src/routes/paths": ("src/services/a", "src/services/b"), "src/db/orders": ("src/models/order",)}
        dependencies = {"src/services/a": ("src/routes/paths",), "src/services/b": ("src/routes/paths",), "src/models/order": ("src/db/orders",)}
        violations = (
            Violation("src/models/order", "src/db/orders", "domain", "infra", ".", "layered", "domain imports infrastructure directly", False),
        )
        targets = (TargetFact(".", "domain", "value-objects", "src/domain/value-objects", True, "Eric Evans, Domain-Driven Design, 2003"),)
        first = advise((self._part(),), modules, dependents, dependencies, violations, targets, {".": {"service": "src/services"}})
        second = advise((self._part(),), modules, dependents, dependencies, violations, targets, {".": {"service": "src/services"}})
        self.assertEqual(first, second)
        advice, silent = first
        kinds = {item.kind: item for item in advice}
        self.assertEqual({"move", "fix-violation", "missing-folder"}, set(kinds))
        self.assertEqual("src/routes/paths.ts", kinds["move"].target)
        self.assertIn("to service (src/services/)", kinds["move"].reason)
        self.assertIn("2 of 2 dependents break a rule", kinds["move"].evidence)
        self.assertIn("introduce an interface (a port) in the domain", kinds["fix-violation"].reason)
        self.assertEqual(("layered", "domain -> infra"), (kinds["fix-violation"].style, kinds["fix-violation"].rule))
        self.assertEqual("src/domain/value-objects", kinds["missing-folder"].target)
        self.assertEqual(12, len(kinds["move"].id))
        self.assertEqual((), silent)

    def test_a_part_without_a_determined_style_gets_no_advice(self) -> None:
        """/* REQ-ARCH-026: a part whose style is not determined gets no advice, and says so. */"""
        from scripts.architecture.advice import advise

        advice, silent = advise((self._part("undetermined"),), {}, {}, {}, (), (), {})
        self.assertEqual((), advice)
        self.assertEqual("style layered (undetermined): no advice", silent[0][1])


class DecisionsStoreTest(unittest.TestCase):
    def _advice(self, kind: str, target: str, rule: str, destination: str = ""):
        from scripts.architecture.advice import Advice, advice_id

        return Advice(advice_id(".", kind, target, rule), ".", kind, target, "why", "layered", rule, "evidence",
                      destination=destination)

    def test_outcomes_are_read_from_the_next_run(self) -> None:
        """/* REQ-ARCH-027: the store logs advice once with a stable id; on the next run a created folder is followed, a file moved to the advised folder is followed, a file moved elsewhere is done differently and teaches that folder's layer, advice still open for 3 builds is hidden (logged once). */"""
        from scripts.architecture import decisions

        folder = self._advice("missing-folder", "src/domain/value-objects", "target domain/value-objects")
        move_a = self._advice("move", "src/routes/a.ts", "move api -> service", "src/services")
        move_b = self._advice("move", "src/routes/b.ts", "move api -> service", "src/services")
        still = self._advice("missing-folder", "src/domain/aggregates", "target domain/aggregates")
        first = decisions.reconcile(decisions.Store(), [folder, move_a, move_b, still], ("src/routes/a.ts",), "2026-01-01", "g1")
        self.assertEqual(4, sum(line.startswith("(advice-given ") for line in first))
        self.assertEqual("(build-run :graph g1 :at 2026-01-01)", first[0])
        self.assertEqual(first, decisions.reconcile(decisions.Store(), [folder, move_a, move_b, still], ("src/routes/a.ts",), "2026-01-01", "g1"))
        store = decisions.read_store("\n".join(first))
        paths = ("src/domain/value-objects/money.ts", "src/services/a.ts", "src/core/b.ts")
        second = decisions.reconcile(store, [still], paths, "2026-01-02", "g2")
        self.assertIn(f"(advice-outcome {folder.id} :followed :at 2026-01-02)", second)
        self.assertIn(f"(advice-outcome {move_a.id} :followed :at 2026-01-02)", second)
        self.assertIn(f'(advice-outcome {move_b.id} :done-differently :actual "src/core/b.ts" :at 2026-01-02)', second)
        self.assertIn(f'(learned-layer "." "core" service :from {move_b.id} :at 2026-01-02)', second)
        self.assertFalse([line for line in second if ":ignored" in line])
        text = "\n".join(first + second)
        for run in (3, 4):
            text += "\n" + "\n".join(decisions.reconcile(decisions.read_store(text), [still], paths, f"2026-01-0{run}", "g"))
        self.assertEqual([], decisions.visible(decisions.read_store(text), [still]))
        self.assertEqual("core", decisions.learned_layers(decisions.read_store(text))["."][0].folder)

    def test_a_rejected_item_is_not_shown_and_lowers_its_rule(self) -> None:
        """/* REQ-ARCH-028: `advice reject` hides the item and ranks advice of the same kind and rule in the part lower; an unknown id is refused. */"""
        from scripts.architecture import decisions

        one = self._advice("missing-folder", "src/domain/aggregates", "target domain/aggregates")
        other = self._advice("missing-folder", "src/domain/specifications", "target domain/specifications")
        move = self._advice("move", "src/routes/a.ts", "move api -> service", "src/services")
        store_text = "\n".join(decisions.reconcile(decisions.Store(), [one, other, move], (), "2026-01-01", "g"))
        line = decisions.user_outcome(decisions.read_store(store_text), one.id, False, "not in this project", "2026-01-02")
        self.assertEqual(f'(advice-outcome {one.id} :rejected :by user :reason "not in this project" :at 2026-01-02)', line)
        self.assertIsNone(decisions.user_outcome(decisions.read_store(store_text), "000000000000", True, "", "2026-01-02"))
        shown = decisions.visible(decisions.read_store(store_text + "\n" + line), [other, move, one])
        self.assertEqual([move.id, other.id], [item.id for item in shown])


class ConfigTest(unittest.TestCase):
    def test_the_written_config_reads_back_to_the_same_layers(self) -> None:
        """/* REQ-ARCH-014: .wingbox/config.yaml written by init reads back to the same parts, styles and folder mapping. */"""
        paths = ("package.json",) + _files(("src/routes", 8), ("src/services", 8), ("src/models", 6), ("src/repositories", 4))
        plan = detect_plan(paths, _reader({}), LIBRARY)
        config = parse_config(render_config(plan, LIBRARY, "2026-01-01"), LIBRARY)
        self.assertIsInstance(config, ConfigOk)
        again = plan_from_config(config, paths, _reader({}), LIBRARY)
        self.assertEqual(
            [(f.folder, f.layer) for f in plan.parts[0].layers], [(f.folder, f.layer) for f in again.parts[0].layers]
        )
        self.assertEqual("layered", again.parts[0].style)

    def test_an_unknown_style_or_layer_is_an_error(self) -> None:
        """/* REQ-ARCH-015: the config names only known styles and layers of the chosen style; anything else is a typed error naming the part. */"""
        bad_style = parse_config("parts:\n  .:\n    style: spaghetti\n", LIBRARY)
        bad_layer = parse_config("parts:\n  .:\n    style: layered\n    layers:\n      kitchen: [pots]\n", LIBRARY)
        self.assertIsInstance(bad_style, ConfigErr)
        self.assertIn("spaghetti", bad_style.error)
        self.assertIsInstance(bad_layer, ConfigErr)
        self.assertIn("kitchen", bad_layer.error)


class InitOutputTest(unittest.TestCase):
    def test_the_text_is_a_printer_over_the_s_expressions(self) -> None:
        """/* REQ-ARCH-016: init facts name style, score, source and evidence; the text prints the best style, its folders with counts and the other candidates. */"""
        paths = ("package.json",) + _files(("src/routes", 8), ("src/services", 8), ("src/models", 6), ("src/repositories", 4))
        plan = detect_plan(paths, _reader({}), LIBRARY)
        facts = init_facts(plan, LIBRARY, '(arch-config-written ".wingbox/config.yaml")')
        self.assertTrue(facts[0].startswith('(arch-part "." :level container'), facts[0])
        self.assertIn(":style layered :state determined", facts[0])
        self.assertIn('Martin Fowler, \\"Patterns of Enterprise Application Architecture', facts[0])
        self.assertIn('(arch-evidence "." layered "routes/" :files 8 :group presentation :match exact)', facts)
        text = render_text(facts, 12)
        self.assertIn("layered (Martin Fowler)   1.00   container", text)
        self.assertIn("services/ 8", text)
        self.assertIn("wrote .wingbox/config.yaml\n", text)
        self.assertTrue(text.rstrip().endswith("next: wingbox learn settles the open points (run by a coding agent it is the "
                                               "agent's task, asking you when the code does not tell; in a terminal it asks "
                                               "you), then wingbox build"), text)
        self.assertNotIn("edit it", text)
        self.assertEqual(text, render_text(list(facts), 12))


if __name__ == "__main__":
    unittest.main()
