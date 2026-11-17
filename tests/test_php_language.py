from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import build_code_graph
from scripts.build_code_graph import (
    DEFAULT_DISCOVERY_EXCLUSIONS,
    Ok,
    build_graph,
    discover_files,
)
from scripts.lang.interface import DiscoveryExclusions, FileResult
from scripts.lang.php import PHP_LANGUAGE
from scripts.lang.php.parser import parse_php_treesitter, php_metadata


class PhpLanguageTests(unittest.TestCase):
    def _write(self, root: Path, relative_path: str, content: str) -> Path:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = self._write(root, relative_path, content)
        return parse_php_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def _graph(
        self,
        root: Path,
        files: dict[str, str],
        exclusions: DiscoveryExclusions = DEFAULT_DISCOVERY_EXCLUSIONS,
    ):
        for relative_path, content in files.items():
            self._write(root, relative_path, content)
        results = []
        for item in discover_files(str(root), exclusions):
            result = build_code_graph.parse_file((*item, str(root)))
            self.assertIsNotNone(result)
            results.append(result)
        graph_result = build_graph(results, str(root), exclusions)
        if not isinstance(graph_result, Ok):
            self.fail(f"PHP graph build failed: {graph_result.error}")
        return graph_result.value

    def _imports(self, graph) -> dict[tuple[str, str], set[str]]:
        return {
            (edge["source"], edge["target"]): set(edge.get("relations", []))
            for edge in graph.edges
            if edge["type"] == "IMPORTS"
        }

    def test_parser_extracts_php_declarations_methods_attributes_and_entities(self) -> None:
        """/* REQ-PHP-001: PHP parsing preserves classes, interfaces, traits, enums, attributes, fully qualified names and one entity per method. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self._parse(
                root,
                "app/Http/Controllers/WidgetController.php",
                r"""
<?php
namespace App\Http\Controllers;

#[Controller]
class WidgetController
{
    #[Route('/widgets')]
    public function __construct() {}
    public function index(): void {}
}

interface WidgetPort
{
    public function execute(): void;
}

trait FormatsWidgets
{
    public function format(): string
    {
        return '';
    }
}

enum WidgetStatus: string
{
    case Active = 'active';
}
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        controller = r"App\Http\Controllers\WidgetController"
        self.assertEqual("php", result.language)
        self.assertEqual("class", entities[controller]["kind"])
        self.assertEqual(["__construct", "index"], entities[controller].get("methods", []))
        self.assertEqual(["Controller"], entities[controller].get("decorators", []))
        self.assertEqual(
            ["Route"],
            entities[f"{controller}::__construct"].get("decorators", []),
        )
        self.assertEqual(
            "interface",
            entities[r"App\Http\Controllers\WidgetPort"]["kind"],
        )
        self.assertEqual(
            "trait",
            entities[r"App\Http\Controllers\FormatsWidgets"]["kind"],
        )
        self.assertEqual(
            "enum",
            entities[r"App\Http\Controllers\WidgetStatus"]["kind"],
        )
        self.assertIn(f"{controller}::index", entities)
        self.assertEqual("function", entities[f"{controller}::index"]["kind"])
        self.assertEqual(9, entities[f"{controller}::index"]["line"])
        self.assertEqual(
            {
                controller,
                r"App\Http\Controllers\WidgetPort",
                r"App\Http\Controllers\FormatsWidgets",
                r"App\Http\Controllers\WidgetStatus",
            },
            set(result.exported_names),
        )

    def test_parser_reads_aliased_and_grouped_use_statements(self) -> None:
        """/* REQ-PHP-002: `use` statements, aliases and grouped `use A\\{B, C as D}` produce php-class imports and imported_names. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self._parse(
                root,
                "app/Support/Widget.php",
                r"""
<?php
namespace App\Support;

use App\Models\User;
use App\Services\{Billing, Reports as ReportService};
use Illuminate\Support\Facades\DB as Database;

class Widget
{
}
""".lstrip(),
            )

        self.assertEqual(
            [
                r"php-class:App\Models\User",
                r"php-class:App\Services\Billing",
                r"php-class:App\Services\Reports",
                r"php-class:Illuminate\Support\Facades\DB",
            ],
            result.imports,
        )
        self.assertEqual(
            [
                {
                    "local": "User",
                    "original": "User",
                    "module": r"php-class:App\Models\User",
                },
                {
                    "local": "Billing",
                    "original": "Billing",
                    "module": r"php-class:App\Services\Billing",
                },
                {
                    "local": "ReportService",
                    "original": "Reports",
                    "module": r"php-class:App\Services\Reports",
                },
                {
                    "local": "Database",
                    "original": "DB",
                    "module": r"php-class:Illuminate\Support\Facades\DB",
                },
            ],
            result.imported_names,
        )

    def test_parser_records_inheritance_interface_trait_use_and_abstractness(self) -> None:
        """/* REQ-PHP-003: extends, implements and `use TraitA, TraitB` fill bases, extends, implements and exports_abstract. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self._parse(
                root,
                "app/Http/Controllers/WidgetController.php",
                r"""
<?php
namespace App\Http\Controllers;

abstract class BaseController
{
}

interface Runnable
{
    public function run(): void;
}

trait Notifiable
{
    public function notify(): void {}
}

class WidgetController extends BaseController implements Runnable, \JsonSerializable
{
    use Notifiable;
}
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        controller = r"App\Http\Controllers\WidgetController"
        self.assertEqual(
            ["BaseController", "Runnable", r"\JsonSerializable", "Notifiable"],
            entities[controller].get("bases", []),
        )
        self.assertEqual(["BaseController"], result.extends)
        self.assertEqual(["Runnable", r"\JsonSerializable"], result.implements)
        self.assertEqual(
            {
                r"App\Http\Controllers\Runnable",
                r"App\Http\Controllers\Notifiable",
                r"App\Http\Controllers\BaseController",
            },
            set(result.exports_abstract),
        )
        self.assertNotIn(controller, result.exports_abstract)

    def test_parser_records_eloquent_events_jobs_and_views_as_relations(self) -> None:
        """/* REQ-PHP-004: Eloquent associations, event(new X), dispatch(new X), X::dispatch and view() carry their relation labels. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self._parse(
                root,
                "app/Models/Order.php",
                r"""
<?php
namespace App\Models;

class Order extends Model
{
    public function user(): BelongsTo
    {
        return $this->belongsTo(User::class);
    }

    public function items(): HasMany
    {
        return $this->hasMany(Item::class);
    }

    public function place(): void
    {
        event(new OrderPlaced($this));
        dispatch(new SyncOrder($this));
        SyncOrder::dispatch($this);
        view('orders.show');
        $this->authorize('view');
    }
}
""".lstrip(),
            )

        relations = {(relation["target"], relation["relation"]) for relation in result.relations}
        self.assertEqual(
            {
                (r"php-symbol:User", "association"),
                (r"php-symbol:Item", "association"),
                (r"php-symbol:OrderPlaced", "event"),
                (r"php-symbol:SyncOrder", "job"),
                (r"php-view:orders.show", "view"),
            },
            relations,
        )
        self.assertIn(r"php-symbol:User", result.imports)
        self.assertIn("php-view:orders.show", result.imports)

    def test_psr4_use_resolves_to_the_model_file_with_an_import_edge(self) -> None:
        """/* REQ-PHP-005: a controller's `use App\\Models\\User;` resolves through the PSR-4 map to app/Models/User with an IMPORTS edge. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "composer.json": r'{"autoload": {"psr-4": {"App\\": "app/"}}}',
                    "app/Models/User.php": r"""
<?php
namespace App\Models;

class User
{
}
""".lstrip(),
                    "app/Http/Controllers/UserController.php": r"""
<?php
namespace App\Http\Controllers;

use App\Models\User;

class UserController
{
    public function show(User $user): void {}
}
""".lstrip(),
                },
            )

        self.assertEqual(
            {
                (
                    "app/Http/Controllers/UserController",
                    "app/Models/User",
                )
            },
            set(self._imports(graph)),
        )

    def test_same_namespace_reference_resolves_without_a_use_statement(self) -> None:
        """/* REQ-PHP-006: a class referenced from the same namespace resolves without a `use` statement. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "app/Models/User.php": r"""
<?php
namespace App\Models;

class User
{
}
""".lstrip(),
                    "app/Models/Post.php": r"""
<?php
namespace App\Models;

class Post
{
    public function author(): User
    {
        return new User();
    }
}
""".lstrip(),
                },
            )

        self.assertIn(
            ("app/Models/Post", "app/Models/User"),
            set(self._imports(graph)),
        )

    def test_root_composer_module_prefix_resolves_across_modules(self) -> None:
        """/* REQ-PHP-007: a Modules\\Finance\\ -> modules/Finance/src/ prefix in the root composer.json resolves a cross-module class. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "composer.json": (
                        r'{"autoload": {"psr-4": '
                        r'{"App\\": "app/", "Modules\\Finance\\": "modules/Finance/src/"}}}'
                    ),
                    "modules/Finance/src/Livewire/Card.php": r"""
<?php
namespace Modules\Finance\Livewire;

class Card
{
}
""".lstrip(),
                    "app/Http/Controllers/FinanceController.php": r"""
<?php
namespace App\Http\Controllers;

use Modules\Finance\Livewire\Card;

class FinanceController
{
    public function show(Card $card): void {}
}
""".lstrip(),
                },
            )

        self.assertIn(
            (
                "app/Http/Controllers/FinanceController",
                "modules/Finance/src/Livewire/Card",
            ),
            set(self._imports(graph)),
        )

    def test_duplicate_fully_qualified_names_without_a_psr4_match_make_no_edge(self) -> None:
        """/* REQ-PHP-008: two files declaring one FQCN that no PSR-4 prefix maps produce no guessed edge (fail closed). */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "a/User.php": r"""
<?php
namespace App\Models;

class User
{
}
""".lstrip(),
                    "b/User.php": r"""
<?php
namespace App\Models;

class User
{
}
""".lstrip(),
                    "app/Consumer.php": r"""
<?php
namespace App;

use App\Models\User;

class Consumer
{
    public function handle(User $user): void {}
}
""".lstrip(),
                },
            )

        consumer_edges = [edge for edge in graph.edges if edge["source"] == "app/Consumer"]
        self.assertEqual([], consumer_edges)

    def test_an_ide_helper_stub_does_not_steal_a_psr4_class_resolution(self) -> None:
        """/* REQ-PHP-009: an `_ide_helper_models.php` stub declaring a model's FQCN does not take the `use App\\Models\\User` edge away from app/Models/User. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "composer.json": r'{"autoload": {"psr-4": {"App\\": "app/"}}}',
                    "app/Models/User.php": r"""
<?php
namespace App\Models;

class User
{
}
""".lstrip(),
                    "_ide_helper_models.php": r"""
<?php
namespace App\Models;

class User
{
}
""".lstrip(),
                    "app/Http/Controllers/UserController.php": r"""
<?php
namespace App\Http\Controllers;

use App\Models\User;

class UserController
{
    public function show(User $user): void {}
}
""".lstrip(),
                },
            )

        imports = self._imports(graph)
        self.assertIn(
            ("app/Http/Controllers/UserController", "app/Models/User"),
            imports,
        )
        self.assertNotIn(
            ("app/Http/Controllers/UserController", "_ide_helper_models"),
            imports,
        )

    def test_a_namespaced_view_resolves_through_load_views_from(self) -> None:
        """/* REQ-PHP-010: `view('admin::nav')` resolves to the module Blade template registered by loadViewsFrom. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "modules/Finance/src/Providers/FinanceServiceProvider.php": r"""
<?php
namespace Modules\Finance\Providers;

use Illuminate\Support\ServiceProvider;

class FinanceServiceProvider extends ServiceProvider
{
    public function boot(): void
    {
        $this->loadViewsFrom(base_path('modules/Finance/resources/views'), 'admin');
    }
}
""".lstrip(),
                    "modules/Finance/resources/views/nav.blade.php": "<nav></nav>\n",
                    "app/Http/Controllers/AdminController.php": r"""
<?php
namespace App\Http\Controllers;

class AdminController
{
    public function show(): mixed
    {
        return view('admin::nav');
    }
}
""".lstrip(),
                },
            )

        self.assertEqual(
            {"view"},
            self._imports(graph)[
                (
                    "app/Http/Controllers/AdminController",
                    "modules/Finance/resources/views/nav",
                )
            ],
        )

    def test_a_dotted_view_resolves_and_blade_module_ids_strip_the_extension(self) -> None:
        """/* REQ-PHP-011: `view('users.index')` resolves to resources/views/users/index and a Blade module ID drops only `.blade.php`. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "resources/views/users/index.blade.php": "<h1>Users</h1>\n",
                    "app/Http/Controllers/UserController.php": r"""
<?php
namespace App\Http\Controllers;

class UserController
{
    public function index(): mixed
    {
        return view('users.index');
    }
}
""".lstrip(),
                },
            )

        self.assertEqual(
            {"view"},
            self._imports(graph)[
                (
                    "app/Http/Controllers/UserController",
                    "resources/views/users/index",
                )
            ],
        )
        self.assertEqual(
            "resources/views/users/index",
            PHP_LANGUAGE.normalize_module_id("resources/views/users/index.blade.php"),
        )
        self.assertEqual(
            "app/Models/User",
            PHP_LANGUAGE.normalize_module_id("app/Models/User.php"),
        )

    def test_blade_extends_and_include_edges_ignore_comments(self) -> None:
        """/* REQ-PHP-012: `@extends` and `@include` create Blade-to-Blade edges while a reference inside `{{-- ... --}}` creates none. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "resources/views/layouts/app.blade.php": "<html></html>\n",
                    "resources/views/partials/nav.blade.php": "<nav></nav>\n",
                    "resources/views/partials/hidden.blade.php": "<p>hidden</p>\n",
                    "resources/views/pages/home.blade.php": (
                        "@extends('layouts.app')\n"
                        "@include('partials.nav')\n"
                        "{{-- @include('partials.hidden') --}}\n"
                    ),
                },
            )

        imports = self._imports(graph)
        page = "resources/views/pages/home"
        self.assertEqual({"extends"}, imports[(page, "resources/views/layouts/app")])
        self.assertEqual({"include"}, imports[(page, "resources/views/partials/nav")])
        self.assertNotIn((page, "resources/views/partials/hidden"), imports)

    def test_an_anonymous_component_resolves_to_its_blade_file(self) -> None:
        """/* REQ-PHP-013: `<x-alert>` resolves to resources/views/components/alert. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "resources/views/components/alert.blade.php": "<div>{{ $slot }}</div>\n",
                    "resources/views/pages/dashboard.blade.php": '<x-alert type="info" />\n',
                },
            )

        self.assertEqual(
            {"component"},
            self._imports(graph)[
                (
                    "resources/views/pages/dashboard",
                    "resources/views/components/alert",
                )
            ],
        )

    def test_livewire_tags_resolve_app_and_module_class_conventions(self) -> None:
        """/* REQ-PHP-014: `<livewire:counter>` resolves to App\\Livewire\\Counter and a dotted alias to the module's nested component. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "composer.json": (
                        r'{"autoload": {"psr-4": '
                        r'{"App\\": "app/", '
                        r'"Modules\\Wellness\\": "modules/Wellness/src/"}}}'
                    ),
                    "app/Livewire/Counter.php": r"""
<?php
namespace App\Livewire;

use Livewire\Component;

class Counter extends Component
{
}
""".lstrip(),
                    "modules/Wellness/src/Livewire/Journal/Insights.php": r"""
<?php
namespace Modules\Wellness\Livewire\Journal;

use Livewire\Component;

class Analytics extends Component
{
}
""".lstrip(),
                    "resources/views/pages/dashboard.blade.php": (
                        "<livewire:counter />\n<livewire:journal.insights />\n"
                    ),
                },
            )

        imports = self._imports(graph)
        page = "resources/views/pages/dashboard"
        self.assertEqual({"livewire"}, imports[(page, "app/Livewire/Counter")])
        self.assertEqual(
            {"livewire"},
            imports[(page, "modules/Wellness/src/Livewire/Journal/Insights")],
        )

    def test_a_livewire_component_registration_resolves_its_alias(self) -> None:
        """/* REQ-PHP-015: `Livewire::component('alias', Class::class)` makes `<livewire:alias>` resolve to that class. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "app/Providers/AppServiceProvider.php": r"""
<?php
namespace App\Providers;

use App\Livewire\GlobalSearch;
use Livewire\Livewire;

class AppServiceProvider
{
    public function boot(): void
    {
        Livewire::component('search-box', GlobalSearch::class);
    }
}
""".lstrip(),
                    "app/Livewire/GlobalSearch.php": r"""
<?php
namespace App\Livewire;

use Livewire\Component;

class GlobalSearch extends Component
{
}
""".lstrip(),
                    "resources/views/pages/search.blade.php": "<livewire:search-box />\n",
                },
            )

        self.assertEqual(
            {"livewire"},
            self._imports(graph)[
                (
                    "resources/views/pages/search",
                    "app/Livewire/GlobalSearch",
                )
            ],
        )

    def test_a_livewire_component_links_its_conventional_view_and_a_page_both(self) -> None:
        """/* REQ-PHP-016: a Livewire component class links to its conventional view and a page embedding it depends on both the class and the template. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "app/Livewire/GlobalSearch.php": r"""
<?php
namespace App\Livewire;

use Livewire\Component;

class GlobalSearch extends Component
{
}
""".lstrip(),
                    "resources/views/livewire/global-search.blade.php": "<div></div>\n",
                    "resources/views/pages/search.blade.php": "<livewire:global-search />\n",
                },
            )

        imports = self._imports(graph)
        component = "app/Livewire/GlobalSearch"
        view = "resources/views/livewire/global-search"
        page = "resources/views/pages/search"
        self.assertIn((component, view), imports)
        self.assertEqual({"livewire"}, imports[(page, component)])
        self.assertIn((page, view), imports)

    def test_purposes_and_layers_follow_laravel_folder_conventions(self) -> None:
        """/* REQ-PHP-017: app/Models is a data model in the domain layer, controllers are the API, Livewire and views are UI, and a module test is a test. */"""
        from scripts import code_graph_search

        files = {
            "app/Models/User.php": r"""
<?php
namespace App\Models;

class User
{
}
""".lstrip(),
            "app/Http/Controllers/UserController.php": r"""
<?php
namespace App\Http\Controllers;

class UserController
{
}
""".lstrip(),
            "app/Livewire/Counter.php": r"""
<?php
namespace App\Livewire;

class Counter
{
}
""".lstrip(),
            "resources/views/pages/home.blade.php": "<h1>Home</h1>\n",
            "modules/Finance/Tests/Feature/Livewire/CardTest.php": r"""
<?php
namespace Modules\Finance\Tests\Feature\Livewire;

class CardTest
{
}
""".lstrip(),
        }
        expected = {
            "app/Models/User.php": ("data model", "domain"),
            "app/Http/Controllers/UserController.php": ("API controller", "api"),
            "app/Livewire/Counter.php": ("UI component", "ui"),
            "resources/views/pages/home.blade.php": ("UI component", "ui"),
            "modules/Finance/Tests/Feature/Livewire/CardTest.php": ("test", "test"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            found = {}
            for relative_path, content in files.items():
                path = self._write(root, relative_path, content)
                result = build_code_graph.parse_file(
                    (str(path), "php", "fixture", str(root))
                )
                self.assertIsNotNone(result)
                module_id = PHP_LANGUAGE.normalize_module_id(relative_path)
                graph = code_graph_search.CodeGraph()
                graph.nodes[module_id] = code_graph_search.Node(module_id, "php", result.purpose)
                found[relative_path] = (
                    result.purpose,
                    code_graph_search.module_layer(graph, module_id),
                )

        self.assertEqual(expected, found)

    def test_vendor_exclusion_keeps_composer_packages_out_and_blade_only_builds(self) -> None:
        """/* REQ-PHP-018: a --exclude-dir vendor style exclusion drops the Composer vendor tree, and a Blade-only project builds. */"""
        exclusions = DiscoveryExclusions(
            directory_names=frozenset({"vendor"}),
            paths=frozenset(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "vendor/acme/pkg/src/Thing.php": r"""
<?php
namespace Acme\Pkg;

class Thing
{
}
""".lstrip(),
                    "app/Http/Controllers/Consumer.php": r"""
<?php
namespace App\Http\Controllers;

use Acme\Pkg\Thing;

class Consumer
{
    public function handle(Thing $thing): void {}
}
""".lstrip(),
                    "resources/views/layouts/app.blade.php": "<html></html>\n",
                    "resources/views/pages/home.blade.php": "@extends('layouts.app')\n",
                },
                exclusions,
            )
            discovered = {
                Path(item[0]).resolve().relative_to(root.resolve()).as_posix()
                for item in discover_files(str(root), exclusions)
            }

        self.assertNotIn("vendor/acme/pkg/src/Thing.php", discovered)
        node_ids = {node["id"] for node in graph.nodes}
        self.assertNotIn("vendor/acme/pkg/src/Thing", node_ids)
        self.assertEqual(
            [],
            [
                edge
                for edge in graph.edges
                if edge["source"] == "app/Http/Controllers/Consumer"
            ],
        )
        self.assertEqual(
            {"extends"},
            self._imports(graph)[
                ("resources/views/pages/home", "resources/views/layouts/app")
            ],
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            blade_only = self._graph(
                root,
                {
                    "resources/views/layouts/app.blade.php": "<html></html>\n",
                    "resources/views/pages/home.blade.php": "@extends('layouts.app')\n",
                },
            )

        self.assertEqual(
            {"resources/views/layouts/app", "resources/views/pages/home"},
            {node["id"] for node in blade_only.nodes},
        )

    def test_anonymous_classes_closures_and_attributes_invent_no_declarations(self) -> None:
        """/* REQ-PHP-019: anonymous classes, closures and PHP 8 attributes neither crash the parser nor add declarations or method entities. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            content = r"""
<?php
namespace App\Services;

#[Attribute]
class Handler
{
    #[Route('/handlers')]
    public function handle(): void
    {
        $anonymous = new class {
            public function run(): void {}
        };
        $closure = function (): void {};
        $arrow = fn (int $value): int => $value;
        $extended = new class extends Base {
        };
    }
}
""".lstrip()
            result = self._parse(root, "app/Services/Handler.php", content)

        metadata = php_metadata(content)
        self.assertEqual(1, len(metadata.declarations))
        self.assertEqual(
            [r"App\Services\Handler", r"App\Services\Handler::handle"],
            [entity["name"] for entity in result.entities],
        )
        self.assertEqual([r"App\Services\Handler"], result.exported_names)

    def test_a_published_vendor_view_is_source_while_root_vendor_is_excluded(self) -> None:
        """/* REQ-PHP-020: `resources/views/vendor` holds published Laravel views and is discovered, while a Composer vendor directory at the repository root is not. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self._write(
                root,
                "resources/views/vendor/jetstream/components/button.blade.php",
                "<button>{{ $slot }}</button>\n",
            )
            self._write(
                root,
                "resources/views/pages/home.blade.php",
                "<h1>Home</h1>\n",
            )
            self._write(
                root,
                "vendor/acme/pkg/src/Thing.php",
                r"""
<?php
namespace Acme\Pkg;

class Thing
{
}
""".lstrip(),
            )

            discovered = {
                Path(item[0]).resolve().relative_to(root.resolve()).as_posix()
                for item in discover_files(str(root), DEFAULT_DISCOVERY_EXCLUSIONS)
            }

        self.assertEqual(
            {
                "resources/views/pages/home.blade.php",
                "resources/views/vendor/jetstream/components/button.blade.php",
            },
            discovered,
        )

    def test_an_explicit_use_import_wins_over_a_same_namespace_class(self) -> None:
        """/* REQ-PHP-021: an explicit `use` import is authoritative, so an external `Request` import gives no edge to a same-namespace class while an aliased local import still resolves. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "app/Http/Request.php": r"""
<?php
namespace BookStack\Http;

class Request
{
}
""".lstrip(),
                    "app/Http/Controller.php": r"""
<?php
namespace BookStack\Http;

use Illuminate\Http\Request;

class Controller
{
    public function __construct(Request $request) {}
}
""".lstrip(),
                    "app/Http/Plain.php": r"""
<?php
namespace BookStack\Http;

class Plain
{
    public function __construct(Request $request) {}
}
""".lstrip(),
                },
            )

        self.assertEqual(
            [],
            [
                edge
                for edge in graph.edges
                if edge["source"] == "app/Http/Controller"
            ],
        )
        self.assertIn(
            ("app/Http/Plain", "app/Http/Request"),
            set(self._imports(graph)),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            aliased = self._graph(
                root,
                {
                    "app/Http/Request.php": r"""
<?php
namespace App\Http;

class Request
{
}
""".lstrip(),
                    "app/Http/Aliased.php": r"""
<?php
namespace BookStack\Http;

use App\Http\Request as LocalRequest;

class Aliased
{
    public function __construct(LocalRequest $request) {}
}
""".lstrip(),
                },
            )

        self.assertIn(
            ("app/Http/Aliased", "app/Http/Request"),
            set(self._imports(aliased)),
        )

    def test_an_absolute_class_reference_is_global_and_keeps_its_backslash(self) -> None:
        """/* REQ-PHP-022: a leading backslash names a global class and survives in the `php-symbol:` import, so `#[\\Attribute]` does not depend on a local Livewire attribute class. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "src/Attribute.php": r"""
<?php
namespace Livewire\Attributes;

class Attribute
{
}
""".lstrip(),
                    "src/TopAttribute.php": r"""
<?php
namespace Livewire;

class Attribute
{
}
""".lstrip(),
                    "src/Async.php": r"""
<?php
namespace Livewire\Attributes;

#[\Attribute]
class Async
{
}
""".lstrip(),
                },
            )

        self.assertEqual(
            [],
            [edge for edge in graph.edges if edge["source"] == "src/Async"],
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self._parse(
                root,
                "src/Caller.php",
                r"""
<?php
namespace Demo;

class Caller
{
    public function run(): void
    {
        \Foo\Bar::baz();
    }
}
""".lstrip(),
            )

        self.assertEqual([r"php-symbol:\Foo\Bar"], result.imports)

    def test_route_and_response_view_calls_create_blade_edges_only_for_literals(self) -> None:
        """/* REQ-PHP-023: `Route::view()` and `response()->view()` with a literal view create Blade edges, while their dynamic forms create none. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            graph = self._graph(
                root,
                {
                    "resources/views/help/tinymce.blade.php": "<div></div>\n",
                    "resources/views/errors/404.blade.php": "<h1>Not found</h1>\n",
                    "app/Http/Controllers/HelpController.php": r"""
<?php
namespace App\Http\Controllers;

class HelpController
{
    public function tinymce(): mixed
    {
        return Route::view('/help/tinymce', 'help.tinymce');
    }

    public function missing(): mixed
    {
        return response()->view('errors.404');
    }

    public function dynamicRoute(): mixed
    {
        return Route::view('/x', $variable);
    }

    public function dynamicResponse(): mixed
    {
        return response()->view($view);
    }
}
""".lstrip(),
                },
            )

        controller = "app/Http/Controllers/HelpController"
        self.assertEqual(
            {
                (controller, "resources/views/help/tinymce"): {"view"},
                (controller, "resources/views/errors/404"): {"view"},
            },
            self._imports(graph),
        )


if __name__ == "__main__":
    unittest.main()