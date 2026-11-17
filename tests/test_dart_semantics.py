from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from scripts import code_graph_search  # noqa: E402
from scripts.build_code_graph import (  # noqa: E402
    Ok,
    build_graph,
    discover_files,
    parse_file,
    render_sexp,
)
from scripts.lang.purpose import infer_purpose, scan_root  # noqa: E402
from wingbox import overview  # noqa: E402


def _write(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _flutter_project(root: Path) -> None:
    """A small invented Flutter app with feature-first folders and generated code."""
    _write(root, "pubspec.yaml", "name: example_app\ndescription: Example application.\n")
    files = {
        "lib/main.dart": "import 'features/orders/presentation/orders_page.dart';\n\nvoid main() {}\n",
        "lib/features/orders/presentation/orders_page.dart": (
            "import '../domain/order.dart';\n"
            "import '../data/order_repository.dart';\n\n"
            "class OrdersPage {\n"
            "  OrdersPage(this.repository);\n"
            "  final OrderRepository repository;\n"
            "  Order? first() => null;\n"
            "}\n"
        ),
        "lib/features/orders/presentation/orders_screen.dart": "class OrdersScreen {}\n",
        "lib/features/orders/presentation/orders_view.dart": "class OrdersView {}\n",
        "lib/features/orders/presentation/orders_widget.dart": "class OrdersWidget {}\n",
        "lib/features/orders/presentation/widgets/order_tile.dart": "class OrderTile {}\n",
        "lib/features/orders/presentation/orders_event.dart": "class OrdersEvent {}\n",
        "lib/features/orders/domain/order.dart": (
            "class Order {\n  const Order(this.id);\n  final String id;\n}\n"
        ),
        "lib/features/orders/domain/order_entity.dart": "class OrderEntity {}\n",
        "lib/features/orders/domain/order_validator.dart": (
            "import '../data/order_repository.dart';\n\n"
            "class OrderValidator {\n"
            "  OrderValidator(this.repository);\n"
            "  final OrderRepository repository;\n"
            "}\n"
        ),
        "lib/features/orders/data/order_repository.dart": (
            "import '../domain/order.dart';\n\n"
            "class OrderRepository {\n  Order? find(String id) => null;\n}\n"
        ),
        "lib/features/orders/data/order_api_service.dart": "class OrderApiService {}\n",
        "lib/features/orders/bloc/orders_bloc.dart": "class OrdersBloc {}\n",
        "lib/features/orders/cubit/orders_cubit.dart": "class OrdersCubit {}\n",
        "lib/features/orders/state/orders_state.dart": "class OrdersState {}\n",
        "lib/features/orders/orders_route.dart": "class OrdersRoute {}\n",
        "lib/core/config/env_config.dart": "class EnvConfig {}\n",
        "lib/core/utils/formats.dart": "String formatMoney(int value) => '$value';\n",
        "lib/models/order_model.dart": "class OrderModel {}\n",
        "lib/models/order.freezed.dart": "part of 'order_model.dart';\n\nclass OrderModelFreezed {}\n",
        "lib/models/order.g.dart": (
            "part of 'order_model.dart';\n\nMap<String, dynamic> toJson(OrderModel value) => {};\n"
        ),
        "test/features/orders/order_test.dart": "void main() {}\n",
        "integration_test/app_test.dart": "void main() {}\n",
        "integration_test/helpers.dart": "void waitForUi() {}\n",
    }
    for relative, content in files.items():
        _write(root, relative, content)


def _purpose(root: Path, relative_path: str) -> str:
    content = (root / relative_path).read_text(encoding="utf-8")
    with scan_root(str(root)):
        return infer_purpose(
            str(root / relative_path), content, "dart", []
        )


def _build_without_pool(root: Path) -> None:
    """Run the builder's parse and graph-writer steps without its process pool.

    A host that denies the POSIX semaphores a pool needs (some sandboxes do)
    cannot run `build_code_graph.py --root`; this keeps the same parser, graph
    assembly and S-expression writer so the semantic assertions still run.
    """
    results = [
        parse_file((path, language, repo, str(root)))
        for path, language, repo in discover_files(str(root))
    ]
    graph_result = build_graph(results, str(root))
    if not isinstance(graph_result, Ok):
        raise AssertionError(f"Dart graph build failed: {graph_result.error}")
    (root / ".wingbox").mkdir(parents=True, exist_ok=True)
    (root / ".wingbox" / "index.sexp").write_text(
        render_sexp(
            graph_result.value.nodes,
            graph_result.value.edges,
            str(root),
            "syntax",
            len(results),
        ),
        encoding="utf-8",
    )


def _write_graph(root: Path) -> Path:
    """A hand-written graph, because a build pool is not available everywhere."""
    (root / ".wingbox").mkdir(parents=True, exist_ok=True)
    sexp_path = root / ".wingbox" / "index.sexp"
    sexp_path.write_text(
        textwrap.dedent(
            """
            (code-graph :schema 1 :files 8 :nodes 8 :edges 10 :violations 0)
            (paths
              (lib
                (features
                  (orders
                    (presentation
                      (m0 orders_page)
                    )
                    (domain
                      (m1 order)
                    )
                    (data
                      (m2 order_repository)
                    )
                    (bloc
                      (m7 orders_bloc)
                    )
                  )
                )
                (core
                  (utils
                    (m3 formats)
                  )
                )
                (models
                  (m6 order.g)
                )
                (m5 main)
              )
              (test
                (features
                  (orders
                    (m4 orders_test)
                  )
                )
              )
            )
            (node m0 dart "page/route")
            (node m1 dart "domain logic")
            (node m2 dart "repository")
            (node m3 dart "shared utilities")
            (node m4 dart "test")
            (node m5 dart "application entry point")
            (node m6 dart "data model")
            (node m7 dart "state store")
            (imports m0 m1)
            (imports m0 m2)
            (imports m0 m3)
            (imports m0 m6)
            (imports m1 m2)
            (imports m1 m3)
            (imports m1 m6)
            (imports m2 m1)
            (imports m2 m6)
            (imports m7 m6)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return sexp_path


class DartPurposeTest(unittest.TestCase):
    def test_test_files_win_over_every_other_dart_rule(self) -> None:
        """/* REQ-DARTLAYER-001: test folders, integration_test and *_test.dart make a Dart file a test whatever its feature folder says. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _flutter_project(root)
            _write(
                root,
                "test/features/orders/domain/order_test.dart",
                "void main() {}\n",
            )
            _write(
                root,
                "lib/features/orders/presentation/orders_widget_test.dart",
                "void main() {}\n",
            )

            cases = {
                "test/features/orders/domain/order_test.dart": "test",
                "lib/features/orders/presentation/orders_widget_test.dart": "test",
                "integration_test/app_test.dart": "test",
                "integration_test/helpers.dart": "test",
            }

            self.assertEqual(
                cases, {path: _purpose(root, path) for path in cases}
            )

    def test_ui_names_and_folders_take_the_existing_ui_purposes(self) -> None:
        """/* REQ-DARTLAYER-002: Dart pages and page folders are page/route, and screens, views, widgets and widget folders are UI components. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _flutter_project(root)
            _write(root, "lib/pages/orders_page.dart", "class OrdersPage {}\n")
            _write(root, "lib/screens/home_screen.dart", "class HomeScreen {}\n")
            _write(root, "lib/widgets/money_text.dart", "class MoneyText {}\n")

            cases = {
                "lib/features/orders/presentation/orders_page.dart": "page/route",
                "lib/features/orders/presentation/orders_screen.dart": "UI component",
                "lib/features/orders/presentation/orders_view.dart": "UI component",
                "lib/features/orders/presentation/orders_widget.dart": "UI component",
                "lib/features/orders/presentation/widgets/order_tile.dart": "UI component",
                "lib/pages/orders_page.dart": "page/route",
                "lib/screens/home_screen.dart": "UI component",
                "lib/widgets/money_text.dart": "UI component",
            }

            self.assertEqual(
                cases, {path: _purpose(root, path) for path in cases}
            )

    def test_bloc_cubit_state_and_event_names_use_the_state_purpose(self) -> None:
        """/* REQ-DARTLAYER-003: bloc, cubit, state and event names and folders take the existing state purpose. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _flutter_project(root)
            _write(root, "lib/features/orders/bloc/orders_event.dart", "class OrdersEvent {}\n")
            _write(root, "lib/features/orders/state/orders_bloc.dart", "class OrdersBloc {}\n")

            cases = {
                "lib/features/orders/bloc/orders_bloc.dart": "state store",
                "lib/features/orders/cubit/orders_cubit.dart": "state store",
                "lib/features/orders/state/orders_state.dart": "state store",
                "lib/features/orders/state/orders_bloc.dart": "state store",
                "lib/features/orders/bloc/orders_event.dart": "state store",
                "lib/features/orders/presentation/orders_event.dart": "state store",
            }

            self.assertEqual(
                cases, {path: _purpose(root, path) for path in cases}
            )

    def test_repositories_data_and_services_take_the_existing_service_purposes(self) -> None:
        """/* REQ-DARTLAYER-004: repository files and folders are repositories and service files and folders are services. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _flutter_project(root)
            _write(root, "lib/features/orders/repositories/order_store.dart", "class OrderStore {}\n")
            _write(root, "lib/features/orders/services/pricing.dart", "class Pricing {}\n")
            _write(root, "lib/features/orders/data/order_repo.dart", "class OrderRepo {}\n")

            cases = {
                "lib/features/orders/data/order_repository.dart": "repository",
                "lib/features/orders/data/order_repo.dart": "repository",
                "lib/features/orders/repositories/order_store.dart": "repository",
                "lib/features/orders/data/order_api_service.dart": "service",
                "lib/features/orders/services/pricing.dart": "service",
            }

            self.assertEqual(
                cases, {path: _purpose(root, path) for path in cases}
            )

    def test_models_entities_and_generated_parts_take_domain_purposes(self) -> None:
        """/* REQ-DARTLAYER-005: model, entity and generated files, and model, entity and domain folders, take domain purposes. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _flutter_project(root)
            _write(root, "lib/entities/order_entity.dart", "class OrderEntity {}\n")
            _write(
                root,
                "lib/features/orders/domain/order.g.dart",
                "part of 'order.dart';\n\nMap<String, dynamic> toJson(Order o) => {};\n",
            )
            _write(
                root,
                "lib/features/orders/domain/order.freezed.dart",
                "part of 'order.dart';\n\nclass OrderFreezed {}\n",
            )

            cases = {
                "lib/models/order_model.dart": "data model",
                "lib/models/order.g.dart": "data model",
                "lib/models/order.freezed.dart": "data model",
                "lib/features/orders/domain/order.dart": "domain logic",
                "lib/features/orders/domain/order_entity.dart": "domain entity",
                "lib/features/orders/domain/order.g.dart": "data model",
                "lib/features/orders/domain/order.freezed.dart": "data model",
                "lib/entities/order_entity.dart": "domain entity",
            }

            self.assertEqual(
                cases, {path: _purpose(root, path) for path in cases}
            )

    def test_entry_points_configuration_core_and_routes_use_existing_vocabulary(self) -> None:
        """/* REQ-DARTLAYER-006: main is the existing application entry point, config files are configuration, core is shared utilities and routes are page/route. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _flutter_project(root)
            _write(root, "lib/features/orders/config/orders_config.dart", "class OrdersConfig {}\n")
            _write(root, "lib/core/router/app_router.dart", "class AppRouter {}\n")
            _write(root, "lib/core/config/env_config.dart", "class EnvConfig {}\n")

            cases = {
                "lib/main.dart": "application entry point",
                "lib/core/config/env_config.dart": "configuration",
                "lib/features/orders/config/orders_config.dart": "configuration",
                "lib/core/utils/formats.dart": "shared utilities",
                "lib/features/orders/orders_route.dart": "page/route",
                "lib/core/router/app_router.dart": "page/route",
            }

            self.assertEqual(
                cases, {path: _purpose(root, path) for path in cases}
            )

    def test_other_languages_keep_their_purposes(self) -> None:
        """/* REQ-DARTLAYER-007: Dart rules do not change the purpose of a TypeScript or Python file. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with scan_root(str(root)):
                self.assertEqual(
                    "UI component",
                    infer_purpose(
                        str(root / "src/components/button.tsx"),
                        "export const Button = () => null;\n",
                        "typescript",
                        [],
                    ),
                )
                self.assertEqual(
                    "test",
                    infer_purpose(
                        str(root / "tests/test_orders.py"),
                        "def test_orders():\n    pass\n",
                        "python",
                        [],
                    ),
                )


class DartLayerTest(unittest.TestCase):
    def test_flutter_folder_rules_place_dart_modules(self) -> None:
        """/* REQ-DARTLAYER-008: Flutter feature, core, config and test folders set the layer from the path (the label rules of a layered part). */"""
        cases = {
            "lib/features/orders/presentation/orders_page": "ui",
            "lib/features/orders/presentation/widgets/order_tile": "ui",
            "lib/features/orders/domain/order": "domain",
            "lib/features/orders/data/order_repository": "infra",
            "lib/features/orders/bloc/orders_bloc": "service",
            "lib/features/orders/cubit/orders_cubit": "service",
            "lib/features/orders/state/orders_state": "service",
            "lib/core/utils/formats": "shared",
            "lib/core/config/env_config": "config",
            "test/features/orders/order_test": "test",
            "integration_test/helpers": "test",
        }

        graph = code_graph_search.CodeGraph()
        for path in cases:
            graph.nodes[path] = code_graph_search.Node(path, "dart", "")
        self.assertEqual(
            cases,
            {
                path: code_graph_search.module_layer(graph, path)
                for path in cases
            },
        )

    def test_dart_purposes_are_in_the_layer_map(self) -> None:
        """/* REQ-DARTLAYER-009: every purpose the Dart rules emit maps to a layer, including the application entry point and state management. */"""
        expected = {
            "test": "test",
            "page/route": "ui",
            "UI component": "ui",
            "application entry point": "config",
            "configuration": "config",
            "shared utilities": "shared",
            "repository": "infra",
            "service": "service",
            "data model": "domain",
            "domain entity": "domain",
            "domain logic": "domain",
        }

        self.assertEqual(
            expected,
            {
                purpose: code_graph_search._classify_layer_by_purpose(purpose)
                for purpose in expected
            },
        )

    def test_a_dart_state_store_is_application_state_not_ui(self) -> None:
        """/* REQ-DARTLAYER-010: a Dart state store is placed in the service layer; the same purpose stays other for other languages. */"""
        graph = code_graph_search.CodeGraph()
        graph.nodes["lib/features/orders/bloc/orders_bloc"] = code_graph_search.Node(
            "lib/features/orders/bloc/orders_bloc", "dart", "state store"
        )
        graph.nodes["lib/features/orders/orders_store"] = code_graph_search.Node(
            "lib/features/orders/orders_store", "dart", "state store"
        )
        graph.nodes["src/store/messages"] = code_graph_search.Node(
            "src/store/messages", "ts", "state store"
        )

        self.assertEqual(
            ["service", "service", "other"],
            [
                code_graph_search.module_layer(graph, module)
                for module in (
                    "lib/features/orders/bloc/orders_bloc",
                    "lib/features/orders/orders_store",
                    "src/store/messages",
                )
            ],
        )

    def test_dart_module_ids_resolve_to_their_files(self) -> None:
        """/* REQ-DARTLAYER-011: a Dart module ID resolves to its .dart file and an impact path loses its .dart extension. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write(root, "lib/features/orders/presentation/orders_page.dart", "class OrdersPage {}\n")

            self.assertEqual(
                str(root / "lib/features/orders/presentation/orders_page.dart"),
                code_graph_search._module_id_to_file(
                    "lib/features/orders/presentation/orders_page", str(root)
                ),
            )
            self.assertEqual(
                "lib/features/orders/presentation/orders_page",
                code_graph_search._target_to_module_pattern(
                    str(root / "lib/features/orders/presentation/orders_page.dart"),
                    str(root),
                ),
            )


class DartOverviewTest(unittest.TestCase):
    def _overview(self) -> overview.Overview:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name).resolve()
        sexp_path = _write_graph(root)
        graph = code_graph_search.CodeGraph()
        self.assertIsInstance(
            graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk
        )
        return overview.build_overview(graph, code_graph_search, "example-app")

    def test_report_layers_and_boundaries_follow_the_flutter_folders(self) -> None:
        """/* REQ-DARTLAYER-012: the report counts Dart layers by folder, and a presentation import of a domain file stays inside the allowed layer order. */"""
        view = self._overview()
        by_id = {module.id: module for module in view.modules}

        self.assertEqual(
            [
                ("ui", 1),
                ("service", 1),
                ("domain", 2),
                ("infra", 1),
                ("shared", 1),
                ("config", 1),
                ("test", 1),
            ],
            list(view.layers),
        )
        self.assertEqual(
            ("ui", "path"),
            (by_id["lib/features/orders/presentation/orders_page"].layer,
             by_id["lib/features/orders/presentation/orders_page"].layer_basis),
        )
        self.assertEqual(
            ("domain", "path"),
            (by_id["lib/features/orders/domain/order"].layer,
             by_id["lib/features/orders/domain/order"].layer_basis),
        )
        self.assertEqual(
            ("infra", "path"),
            (by_id["lib/features/orders/data/order_repository"].layer,
             by_id["lib/features/orders/data/order_repository"].layer_basis),
        )
        self.assertEqual(
            ("config", "role"),
            (by_id["lib/main"].layer, by_id["lib/main"].layer_basis),
        )
        self.assertEqual(
            [
                (
                    "lib/features/orders/domain/order",
                    "lib/features/orders/data/order_repository",
                ),
                (
                    "lib/features/orders/presentation/orders_page",
                    "lib/features/orders/data/order_repository",
                ),
            ],
            [
                (violation.source, violation.target)
                for violation in view.violations
            ],
        )
        self.assertNotIn(
            ("lib/features/orders/presentation/orders_page", "lib/features/orders/domain/order"),
            [(violation.source, violation.target) for violation in view.violations],
        )
        self.assertEqual(
            [
                ("domain", "infra", "domain imports infrastructure directly"),
                ("ui", "infra", "UI imports infrastructure directly"),
            ],
            [
                (violation.source_layer, violation.target_layer, violation.reason)
                for violation in view.violations
            ],
        )

    def test_generated_dart_parts_stay_out_of_the_risk_lists(self) -> None:
        """/* REQ-DARTLAYER-013: generated *.g.dart and *.freezed.dart modules never rank as entry points, most depended-on modules or the widest blast radius. */"""
        view = self._overview()
        generated = overview.ModuleInfo(
            id="lib/models/order.g",
            file="lib/models/order.g.dart",
            lang="dart",
            layer="domain",
            layer_basis="role",
            fan_in=40,
            fan_out=9,
            within_two_hops=40,
        )
        freezed = dataclasses.replace(
            generated,
            id="lib/models/order.freezed",
            file="lib/models/order.freezed.dart",
            fan_in=0,
            fan_out=12,
        )
        control = dataclasses.replace(
            generated,
            id="lib/models/order_model",
            file="lib/models/order_model.dart",
            fan_in=2,
            fan_out=1,
        )
        noisy = dataclasses.replace(
            view, modules=view.modules + (generated, freezed, control)
        )

        self.assertNotIn(
            generated.id,
            [module.id for module in overview.most_depended_on(noisy, 15)],
        )
        self.assertNotIn(
            freezed.id,
            [module.id for module in overview.entry_points(noisy, 10)],
        )
        self.assertNotEqual(
            generated.id,
            overview.widest_blast_radius(noisy).id,
        )
        self.assertIn(
            control.id,
            [module.id for module in overview.most_depended_on(noisy, 15)],
        )


class DartBuildTest(unittest.TestCase):
    def test_a_parsed_flutter_project_keeps_its_purposes_and_layers(self) -> None:
        """/* REQ-DARTLAYER-014: the parser and graph writer keep Dart purposes and Flutter layers for a whole project. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "example_app"
            root.mkdir()
            _flutter_project(root)
            _build_without_pool(root)
            sexp_path = root / ".wingbox" / "index.sexp"
            graph = code_graph_search.CodeGraph()
            self.assertIsInstance(
                graph.load(str(sexp_path), str(root)),
                code_graph_search.MapLoadOk,
            )
            view = overview.build_overview(graph, code_graph_search, "example_app")

        self.assertEqual(
            {
                "ui": 7,
                "service": 3,
                "domain": 6,
                "infra": 2,
                "shared": 1,
                "config": 2,
                "test": 3,
            },
            dict(view.layers),
        )
        self.assertEqual(
            {
                (
                    "lib/features/orders/domain/order_validator",
                    "lib/features/orders/data/order_repository",
                ),
                (
                    "lib/features/orders/presentation/orders_page",
                    "lib/features/orders/data/order_repository",
                ),
            },
            {(violation.source, violation.target) for violation in view.violations},
        )
        self.assertNotIn(
            ("lib/features/orders/presentation/orders_page", "lib/features/orders/domain/order"),
            {(violation.source, violation.target) for violation in view.violations},
        )
        expected = {
            "lib/features/orders/presentation/orders_page": ("page/route", "ui"),
            "lib/features/orders/presentation/orders_widget": ("UI component", "ui"),
            "lib/features/orders/presentation/orders_event": ("state store", "ui"),
            "lib/features/orders/domain/order": ("domain logic", "domain"),
            "lib/features/orders/domain/order_entity": ("domain entity", "domain"),
            "lib/features/orders/data/order_repository": ("repository", "infra"),
            "lib/features/orders/data/order_api_service": ("service", "infra"),
            "lib/features/orders/bloc/orders_bloc": ("state store", "service"),
            "lib/features/orders/orders_route": ("page/route", "ui"),
            "lib/core/config/env_config": ("configuration", "config"),
            "lib/core/utils/formats": ("shared utilities", "shared"),
            "lib/models/order_model": ("data model", "domain"),
            "lib/models/order.g": ("data model", "domain"),
            "lib/models/order.freezed": ("data model", "domain"),
            "lib/main": ("application entry point", "config"),
            "test/features/orders/order_test": ("test", "test"),
            "integration_test/helpers": ("test", "test"),
        }
        self.assertEqual(
            expected,
            {
                module: (
                    graph.module_purpose(module),
                    code_graph_search.module_layer(graph, module),
                )
                for module in expected
            },
        )

    def test_the_builder_runs_on_a_flutter_project(self) -> None:
        """/* REQ-DARTLAYER-015: the builder entry point builds a Flutter project and publishes Dart purposes, layers and boundaries. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "example_app"
            root.mkdir()
            _flutter_project(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(REPO / "scripts" / "build_code_graph.py"),
                    "--root",
                    str(root),
                ],
                cwd=REPO,
                capture_output=True,
                check=False,
                text=True,
            )
            if completed.returncode != 0:
                # Some sandboxes deny the POSIX semaphores a build pool needs.
                # Everything after the pool is the same code, so build serially.
                self.assertIn("Permission denied", completed.stderr, completed.stderr)
                _build_without_pool(root)
            sexp_path = root / ".wingbox" / "index.sexp"
            self.assertTrue(sexp_path.is_file(), completed.stderr)
            graph = code_graph_search.CodeGraph()
            self.assertIsInstance(
                graph.load(str(sexp_path), str(root)),
                code_graph_search.MapLoadOk,
            )
            view = overview.build_overview(graph, code_graph_search, "example_app")

        self.assertEqual(
            "page/route",
            graph.module_purpose("lib/features/orders/presentation/orders_page"),
        )
        self.assertEqual(
            "ui",
            code_graph_search.module_layer(
                graph, "lib/features/orders/presentation/orders_page"
            ),
        )
        self.assertEqual(
            "shared",
            code_graph_search.module_layer(graph, "lib/core/utils/formats"),
        )
        self.assertEqual(
            "config",
            code_graph_search.module_layer(graph, "lib/main"),
        )
        violations = {(violation.source, violation.target) for violation in view.violations}
        self.assertIn(
            (
                "lib/features/orders/presentation/orders_page",
                "lib/features/orders/data/order_repository",
            ),
            violations,
        )
        self.assertNotIn(
            (
                "lib/features/orders/presentation/orders_page",
                "lib/features/orders/domain/order",
            ),
            violations,
        )


if __name__ == "__main__":
    unittest.main()