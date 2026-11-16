# Wingbox benchmark: PHP and Laravel impact questions on public code

Wingbox 0.3.0 from this checkout, run of 2026-11-16, on the corpora below. This file is written by `make_report.py` from the files in `results/`; no number is typed by hand. `benchmarks/php/Dockerfile.php` reruns the benchmark in a clean container and compares every result file with the committed one.

The results are of the code in this checkout, at the commit that holds `results/`. `WINGBOX_SPEC=wingbox==X.Y.Z` runs the same benchmark against a release once one ships the PHP adapter.

## Summary

- 60 questions drawn with a fixed seed from 3 PHP/Blade corpora: `wingbox impact <file> 2 --all` names the complete list of dependent files, with nothing outside it, in 60 of 60 (95 % interval 94 to 100 %). A required file is missing in 0 of 60.
- Every IMPORTS edge of the graph against the reference: 3 corpora, 6,054 reference dependencies in all; 0 wrong, 0 missing.
- The reference lists hold 1 to 354 files, median 3. The answer is read from the text the command prints, without a model.

## The question

For a file X: every file that depends on X directly or through one other file, where "A depends on B" means that A names B. It is what a developer or an agent asks before changing X.

In PHP, A names B when A's source contains an import that resolves to B's file, or a class reference that resolves to B: `new B`, `B::member`, `B::class`, `extends`, `implements`, a trait `use`, a type hint (parameter, property, return type, union, intersection, nullable), `catch`, `instanceof` or an attribute. In Blade, A names B through `@extends`, `@include*`, `@component`, `@each`, `<x-*>` or `<livewire:*>` when the view or component resolves to B's file. In PHP the helper `view('a.b')`, `View::make('a.b')`, `view()->make('a.b')`, a `->view('a.b')` call (a response) and `Route::view(uri, 'a.b')` are the same kind of edge.

The reference lists and the edge audit come from `tools/php_reference.php`, a resolver written in PHP that shares no code with Wingbox: it uses only `token_get_all`, `json_decode` and the standard library, has no Composer package and no autoloader, and never imports or calls Wingbox. It resolves class names through the declarations of the checkout and through the PSR-4 maps of every `composer.json` in the tree (root and nested, `autoload` and `autoload-dev`, the longest prefix wins), and Blade views through every `resources/views` directory plus `loadViewsFrom` and `View::addNamespace` registrations (an unregistered namespace is the directory of that name under a default root, as the published error views are); Livewire aliases follow `Livewire::component` registrations, then `App\Livewire\<Studly>` and the view `livewire.<alias>`.

Two rules keep the reference honest about names it cannot decide. When a view resolves in several roots, the name is ambiguous and resolves to nothing. When the same class is declared in several files, the file the PSR-4 map names wins (a generated IDE-helper file declaring stub classes for every model is the usual case); if that leaves zero or several files, the name is ambiguous and resolves to nothing. An edge to a candidate of an ambiguous name is accepted when it is in the graph and is never required.

Sampling: per corpus, 20 files drawn without replacement with seed 20261116 from the source files that at least one other file depends on. Nothing else is filtered. The reference lists hold 1 to 354 files, median 3.

## Test 1. The impact command, 60 questions

`wingbox impact <file> 2 --all`, no model. "Complete and nothing extra": every required file is named and no named file is outside the accepted list. A file counts as missing when the required list names it and the answer does not.

| Corpus | Questions | Complete and nothing extra | With a missing file | Median answer, tokens |
|---|---|---|---|---|
| bookstack | 20 | 20 | 0 | 94 |
| livewire | 20 | 20 | 0 | 108 |
| monica | 20 | 20 | 0 | 172 |
| All | 60 | 60 (94 to 100 %) | 0 | 106 |

The interval is a 95 % Wilson interval. Mean recall 1.000, mean precision 1.000. In all 60 answers the list equals the reference list file for file.
Tokens are characters of the output divided by four; the largest answer is 11,566 tokens for a list of 354 files.

## Test 2. Are the edges in the code?

Every IMPORTS edge of the Wingbox graph against every reference edge of the same checkout, compared by module ID (`app/Models/User.php` is module `app/Models/User`; a `.php` and a `.blade.php` file of one stem are one module, as in the graph). Wrong: an edge the reference neither requires nor accepts. Missing: a required edge the graph does not have.

| Corpus | Reference dependencies (module pairs) | Graph edges (PHP only) | Wrong | Missing | Accepted (not required) | Outside PHP |
|---|---|---|---|---|---|---|
| bookstack | 3,236 | 3,236 | 0 | 0 | 0 | 1,318 |
| livewire | 526 | 526 | 0 | 0 | 0 | 0 |
| monica | 2,292 | 2,292 | 0 | 0 | 0 | 7 |

1,325 IMPORTS edges have an endpoint outside the PHP/Blade sources (JavaScript, TypeScript modules and the like). They are counted in the Outside PHP column, not dropped silently: the audit covers every IMPORTS edge whose two endpoints are PHP/Blade files; the breakdown by cause is in `results/edges.json`.

4 module(s) are shared by a `.php` and a `.blade.php` file (single-file Livewire components). The graph has one node for both, and the audit compares modules, so an edge to either file is the same edge.

Every IMPORTS edge of every graph is in the reference and every reference edge is in a graph.

The graph is allowed to hold edges the reference only accepts: a candidate of an ambiguous name, and the conventional template `livewire.<alias>` of a component class (the class and its template name each other in a running application; the reference requires the class edge, not both). Accepted edges are neither wrong nor required.

## Corpora and commits

| Corpus | Source files | Questions | Commit |
|---|---|---|---|
| bookstack | 1837 | 20 | `bf68091` (v26.09.3) |
| livewire | 306 | 20 | `8d7f8fc` (v4.4.8) |
| monica | 1399 | 20 | `32028ce` (v4.1.2) |

Each corpus is exported with `git archive`, so a path the repository marks `export-ignore` (tests and documentation in these projects) is not part of the run.

## What to conclude

`wingbox impact <file> 2 --all` returned the complete reference list with nothing outside it in all 60 questions.

Every IMPORTS edge of every graph is a dependency of the code, and every dependency the reference finds is in the graph.

Read Test 1 as the share of "what depends on this file" questions the command answers completely, and Test 2 as the precision and recall of the graph's IMPORTS edges against a static reading of the same source. The reference is a reader of names, not a runtime: a dependency it cannot see (a class name built in a string, a view chosen at run time) is outside both lists, and a convention the graph records but the reference does not require (a Livewire component's template) is counted as accepted, not wrong.

## Scope

- One kind of question: which files name this file, directly or through one other file. Other questions are not measured here.
- The reference is static. It reads the text of the checkout, never executes it; a view name built at run time or a class name in a string cannot become an edge.
- A name that resolves nowhere in the checkout is external (a Composer package, a framework class) and is not an edge; the reference does not know the packages.
- Questions are drawn inside the corpora, so they are not independent in the strict sense; the per-corpus rows show where a result comes from.

## Versions and commits

| Item | Version or commit |
|---|---|
| Wingbox | 0.3.0 from this checkout (`WINGBOX_SPEC` of the run) |
| PHP | 8.4.27 |
| livewire | 8d7f8fc (v4.4.8) |
| bookstack | bf68091 (v26.09.3) |
| monica | 32028ce (v4.1.2) |

## Reproduce

In a clean container (the corpora are fetched at the commits above; no model, no key):

```sh
git clone https://github.com/aileron-tech/wingbox && cd wingbox
docker build -f benchmarks/php/Dockerfile.php -t wingbox-php-benchmark .
docker run --rm wingbox-php-benchmark        # ends with REPRODUCED, or lists the result files that differ
```

Without a container (needs `git`, `uv`, Python 3.11+ and PHP 8.4 with `token_get_all`):

```sh
benchmarks/php/run.sh ~/wingbox-php-bench                         # the corpora and every result
WINGBOX_SPEC=/path/to/a/checkout benchmarks/php/run.sh ~/other    # the Wingbox under test
```

By default `WINGBOX_SPEC` is the checkout that holds the script, so the run tests this commit. `WINGBOX_SPEC=wingbox==X.Y.Z` tests a released version, and `WINGBOX_SPEC=/path/to/checkout` another checkout. With `VERIFY=0` the run does not compare its results with the committed ones.

| Path | Content |
|---|---|
| `run.sh`, `tools/questions.py`, `tools/verify.py`, `make_report.py` | the run, the sampling, the comparison with `wingbox impact` and the edge audit, this report |
| `tools/php_reference.php`, `tools/list_files.py`, `tools/graph_edges.py` | the reference resolver (PHP), the file list it reads, the graph edges the audit reads; none of them uses Wingbox code |
| `fixture/`, `results/` | the synthetic Laravel project, and the questions, answers, edge audit, versions and commits of one run |
