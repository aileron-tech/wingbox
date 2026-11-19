# Known limits: what the graph does not see

Wingbox answers from the edges of its graph. `impact`, `rdeps` and `usages` list what an edge or a recorded use leads to, and the boundary checks judge the edges that exist. So every limit below reads the same way: **an edge that is missing is a dependent that is not listed**, and a file that is not in the graph is never part of an answer.

This page collects what the repository already says, in one place. Every line names the file it comes from. Where the sources say nothing, the line says "not assessed"; it does not guess. Nothing here was measured for this page. How each kind of wrong answer is reported and caught is in [tool-errors.md](tool-errors.md).

## Every language

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| Behaviour that is not explicit in source: generated imports, dependency injection at run time, reflection, what a framework wires by itself | A file that is reached only this way is not listed as a dependent. Check such a change against the running application or the framework | [README.md](../../README.md), "Accuracy and limits" and the FAQ "Does Wingbox replace a compiler or a language server?" |
| An import of a JSON, CSS or other non-code file | No edge: a change to a configuration file shows no dependents | [docs/edge-model.md](../edge-model.md), section 1 |
| An import that resolves to no file of the repository (a package): the graph has no node for a package and keeps no record of the import | The files that use a package cannot be listed, and a rule about a package ("the core must not import X") cannot be evaluated | [docs/edge-model.md](../edge-model.md), section 1 |
| Precise call sites: the default build records import, reference, data-flow and architecture edges; call hierarchy comes with `--lsp` and an installed language server | How complete the answers of the call commands (`find_callers`, `find_callchain`) are without `--lsp`: not assessed | [README.md](../../README.md), "Call graphs and diagrams" and "Accuracy and limits" |
| The receiver type of a method call that is not in sight | The call is listed as `unresolved-call`, apart from the uses, and never guessed: the uses of a method can be short by the calls listed there | [README.md](../../README.md), "Quick start from the command line"; [benchmarks/usages/README.md](../../benchmarks/usages/README.md), "By language" (0 in its 100 answers) |
| Use positions outside Python, TypeScript, JavaScript and Rust | In the other languages `usages` names the files that reference the symbol, without line and column | [README.md](../../README.md), "Quick start from the command line" |
| Files that are left out of the graph: a vendored bundle, minified and bundled JavaScript, JavaScript under `node_modules`, `dist`, `build` or `vendor`, a `vendor` folder that holds dependencies, every folder named `build` | An import of such a file gives no edge, and the file is never a dependent. One of 901 dependencies of chrome-devtools-mcp is missing for this reason | [README.md](../../README.md), "Measured: 100 questions" and "Dart and Flutter dependency search"; [benchmarks/impact/README.md](../../benchmarks/impact/README.md), Test 3; [tests/test_typescript_language.py](../../tests/test_typescript_language.py) (REQ-TS-JS-005); [tests/test_vendor_folders.py](../../tests/test_vendor_folders.py) (REQ-CS-075) |
| Dependents further away than the depth asked for | `impact <file>` answers within two hops unless a depth is given; a file three imports away is not on the list | [README.md](../../README.md), "What is blast radius analysis?" |
| A pipeline step backwards: `impact` does not follow a `data-flow ... :pipeline` fact from a step to the step before it | The earlier steps of a configured pipeline are not listed as dependents of a later one | [CHANGELOG.md](../../CHANGELOG.md), 0.6.0 |
| Files of a language whose parser is not installed | Every file of that language is missing from the graph and from every answer. The build and every later answer say so (see [tool-errors.md](tool-errors.md)) | [README.md](../../README.md), "Language parsers" |
| Files of a language Wingbox does not parse (Swift, Kotlin, C, C++, Lua and others) | They are not in the graph. The build prints the file suffixes it reads and a count per supported language, not the number of other source files; a question about one of them answers `no-match` | [README.md](../../README.md), "Supported languages and projects" and "Wingbox, Graphify, CodeGraph and OntoIndex on the same tests"; [scripts/build_code_graph.py](../../scripts/build_code_graph.py) (the lines "Found ... source files" and "Supported source"); [tests/test_audit_regressions.py](../../tests/test_audit_regressions.py) (REQ-CS-014) |

## Python

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| A module loaded by a name that is computed at run time. Followed: `importlib` with a literal name, and a name read from a JSON, YAML or TOML file beside the loader | A plug-in that is loaded any other way is not a dependent of what it uses, and its loader does not depend on it | [README.md](../../README.md), "Supported languages and projects" and "Accuracy and limits"; [tests/test_python_dynamic_loading.py](../../tests/test_python_dynamic_loading.py) (REQ-PY-003) |
| Which calls between two files are calls of an API: on the three medium codebases of Theory of Code Space 19 of 39 `CALLS_API` edges are found | The `calls` facts are incomplete. The file is still a dependent, because the files of the missed calls also import each other | [README.md](../../README.md), "Wingbox on Theory of Code Space"; [benchmarks/tocs/README.md](../../benchmarks/tocs/README.md), "What Wingbox still gets wrong" |
| Nothing is missing here, an answer holds more: a package `__init__.py` that Python executes on the way to the imported module is named | A reader of the import statements may or may not count it; the benchmark accepts it and does not require it | [benchmarks/impact/README.md](../../benchmarks/impact/README.md), "The question" |
| Other limits of the Python graph | not assessed | |

Measured: [benchmarks/impact/README.md](../../benchmarks/impact/README.md) (FastAPI: 20 of 20 questions, 1,802 dependencies with 0 wrong and 0 missing), [benchmarks/usages/README.md](../../benchmarks/usages/README.md) (20 of 20).

## TypeScript, TSX and JavaScript

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| An import of a vendored bundle | No edge (the one missing edge of the edge test: `src/third_party/index.ts` to a bundled `.d.ts`) | [benchmarks/impact/README.md](../../benchmarks/impact/README.md), Test 3 |
| A package that no `package.json` of the repository declares; a package name that several `package.json` files declare, seen from an importer outside all of them | No edge | [tests/test_typescript_language.py](../../tests/test_typescript_language.py) (REQ-TS-WS-005, REQ-TS-WS-006) |
| That a test only mocks a module (`vi.mock`, `jest.mock`): the mock is recorded as an import | Right for impact (the test breaks when the module moves); for a layer rule it is an edge the code does not have | [docs/edge-model.md](../edge-model.md), section 1 |
| Nothing is missing here: an import of types only is kept, and `boundaries` reports it apart from the violations | The compiler erases it, but a change of the type still reaches the importing file | [README.md](../../README.md), "Common code searches" |
| Nothing is missing here, an answer holds more: a file that imports the compiled output of X, which `tsconfig.json` maps back to X | Accepted by the benchmark, not required | [benchmarks/impact/README.md](../../benchmarks/impact/README.md), "The question" |
| Other limits of the TypeScript graph | not assessed | |

Measured: [benchmarks/impact/README.md](../../benchmarks/impact/README.md) (40 of 40 questions; 3,795 and 901 dependencies with 0 wrong and 1 missing), [benchmarks/usages/README.md](../../benchmarks/usages/README.md) (40 of 40).

## Rust

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| A method called on a value (`value.method()`) and a member a trait gives: both need the type checker | No edge: a file that uses a type only through such calls is not a dependent of the file that defines the method | [CHANGELOG.md](../../CHANGELOG.md), 0.10.0 |
| The file that defines an item a macro makes, when a facade re-exports the module with a glob | The users of the item are dependents of the facade, not of the file with the macro; the build log counts these items | [CHANGELOG.md](../../CHANGELOG.md), 0.10.0 |
| Nothing is missing here, an answer holds more: a file whose path passes through the module of X to reach a deeper module (`crate::x::y`), and a glob import whose body names the item (marked `:candidate`) | Accepted by the benchmark, not required | [benchmarks/impact/README.md](../../benchmarks/impact/README.md), "The question"; [docs/edge-model.md](../edge-model.md), section 3.1 |
| Code that build scripts and procedural macros generate | not assessed (the reference of the usages benchmark ran with both switched off) | [benchmarks/usages/README.md](../../benchmarks/usages/README.md), "Scope" |

Measured: [benchmarks/impact/README.md](../../benchmarks/impact/README.md) (40 of 40 questions; 456 and 2,084 dependencies with 0 wrong and 0 missing, checked against rust-analyzer), [benchmarks/usages/README.md](../../benchmarks/usages/README.md) (40 of 40).

## C#, Java and Go

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| C#: a namespace `using` that the file does not use; Java: a wildcard import whose types the file does not reference | No edge is made from the number of types in the namespace or package; an edge exists only for a type the source names | [tests/test_csharp_language.py](../../tests/test_csharp_language.py) (REQ-CSHARP-005); [tests/test_java_language.py](../../tests/test_java_language.py) (REQ-JAVA-004, REQ-JAVA-005) |
| C#: generated source under the .NET build-output folders; Go: the `vendor` dependency tree | Not in the graph | [tests/test_csharp_language.py](../../tests/test_csharp_language.py) (REQ-CSHARP-004); [tests/test_go_language.py](../../tests/test_go_language.py) (REQ-GO-004) |
| What else the graph of each of the three languages misses, and how often | not assessed: the repository holds tests for these languages and no benchmark against an independent reference | [README.md](../../README.md), "Supported languages and projects" (what is indexed) |

## Ruby and Rails

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| Anything that needs a booted Rails application: the default map does not boot Rails | Dependencies that exist only at run time are not edges | [README.md](../../README.md), "Ruby and Rails dependency search" |
| A constant that is reopened in several files or is ambiguous | Left unlinked: no dependent is listed through it | the same section; [tests/test_ruby_language.py](../../tests/test_ruby_language.py) (REQ-RUBY-004) |
| The target of a polymorphic association | Stays open: the models behind it are not dependents | the same section; [tests/test_ruby_language.py](../../tests/test_ruby_language.py) (REQ-RUBY-009) |
| External and dynamic Ruby calls | Not guessed: no edge | the same section |
| How often each of these occurs | not assessed: no benchmark against an independent reference in the repository | |

## PHP and Laravel

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| A class name that no file declares, or that several files declare without a single PSR-4 owner; an alias with several targets; a view that exists in several view directories | No edge | [README.md](../../README.md), "PHP and Laravel dependency search" |
| A Livewire alias that the application registers dynamically (in a loop over a directory, for example) and that follows none of the conventions | Left unlinked | the same section |
| A class name built in a string; a view chosen at run time | Cannot become an edge; the benchmark's reference cannot see it either, so it is outside both lists | [benchmarks/php/README.md](../../benchmarks/php/README.md), "What to conclude" and "Scope" |
| A name that resolves nowhere in the checkout (a Composer package, a framework class) | External: no edge | [benchmarks/php/README.md](../../benchmarks/php/README.md), "Scope" |
| Edges with an end outside PHP and Blade (JavaScript, TypeScript): 1,325 in the three corpora | Not part of the PHP edge audit: counted, not checked there | [benchmarks/php/README.md](../../benchmarks/php/README.md), Test 2 |

Measured: [benchmarks/php/README.md](../../benchmarks/php/README.md) (60 of 60 questions; 6,054 reference dependencies with 0 wrong and 0 missing).

## Dart and Flutter

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| What a running Flutter application composes: the routes and pages of a `go_router` configuration, the objects a `get_it` locator registers under a type, the clients `retrofit` generates | Bound at run time or in code that does not exist as source: no edge. A blast radius starts from the interface file, the screen or the repository that names the service | [README.md](../../README.md), "Dart and Flutter dependency search" |
| A `dart:` library and a `package:` name that no local `pubspec.yaml` declares | Not project modules: no edge | the same section |
| A generated part (`*.g.dart`, `*.freezed.dart`) that is not on disk | The directive adds nothing; once the file exists it is indexed like any other | the same section |
| Dart files under `.dart_tool/` and `build/` | Not indexed | the same section |
| Dart files on Windows on arm64: no wheel of the Dart parser exists there | Skipped, with the notice of a skipped language | [README.md](../../README.md), "Language parsers" |
| How the graph compares with the imports of a public Dart project | not assessed: `benchmarks/dart/check_accuracy.py` runs the comparison on a project of your own and the repository holds no result of it | [benchmarks/dart/README.md](../../benchmarks/dart/README.md) |

## Free Pascal and Lazarus

| What the graph does not see | What it means for an answer | Source |
|---|---|---|
| A unit name that several units carry and that neither an explicit `in 'path'` nor a unit of the same directory settles | Left unlinked: no edge | [README.md](../../README.md), "Supported languages and projects"; [tests/test_pascal_language.py](../../tests/test_pascal_language.py) (REQ-PAS-004) |
| Which branch of a compiler conditional is compiled | Indexed conservatively; competing explicit paths select no provider | the same section; [tests/test_pascal_language.py](../../tests/test_pascal_language.py) (REQ-PAS-015) |
| Call detail from a language server: Pascal has no LSP enrichment | not assessed beyond that statement | the same section |
| How often each of these occurs | not assessed: no benchmark against an independent reference in the repository | |

## Infrastructure files

Wingbox can also map relationships from Kustomize, Helm and Terraform ([README.md](../../README.md), "Supported languages and projects"). What that map does not see: not assessed.

## Layers and boundary checks

| What is not judged | What it means for an answer | Source |
|---|---|---|
| A part with fewer than 12 source files | Listed but not judged: no layers, no violations | [README.md](../../README.md), "The architecture of each part: `wingbox init`" |
| A part whose style is not determined (no candidate style leads the next by 0.15) | No layers and no boundary violations are stated for it | the same section |
| A part that declares its own rules in a file Wingbox cannot read | The file is named and no violation is stated for that part | the same section |
| The role of a folder whose name states none: on 240 held-out folders of 19 public repositories the folder names give the right answer for 76.2 %; 22.5 % have a role and get no match, 1.2 % get a wrong match; the style of 31 of 33 parts is detected | A module can be in no layer, or in a wrong one, so a rule is not applied to it or is applied wrongly | [benchmarks/architecture/README.md](../../benchmarks/architecture/README.md) |
| Rules whose target is a package | Cannot be evaluated (see "Every language") | [docs/edge-model.md](../edge-model.md), section 1 |
| What a file reaches outside the repository (databases, HTTP services, brokers, processes) | Found by the syntax of each line: approximate | [docs/edge-model.md](../edge-model.md), section 1 |

## Installing

| Limit | Source |
|---|---|
| Linux with musl on aarch64: `pip install wingbox` fails, because one parser has neither a wheel nor a source package there | [LOG.md](../../LOG.md), 2026-11-19, "The language parsers are dependencies of the package again", "Not covered" |
| Windows on arm64: the extras `dart` and `all` build the Dart grammar from its source package | the same entry |
| `python -m wingbox` typed in a project that holds a folder `wingbox/` starts that folder; the `wingbox` program and `python -P -m wingbox` are not affected | [LOG.md](../../LOG.md), 2026-11-19, "A Python started by Wingbox never imports from the project folder", "Not covered" |

## Defects that changed an answer

They are recorded where they were fixed: under "Fixes" of each version in [CHANGELOG.md](../../CHANGELOG.md), with cause and verification in [LOG.md](../../LOG.md), and each with a test that names its requirement ([requirements.md](requirements.md); the regression tests of reported defects are in `tests/test_issue_1_regressions.py`, `tests/test_audit_regressions.py`, `tests/test_review_fixes.py` and `tests/test_validation_fixes.py`). Open defects are the open issues of the repository. One list of the defects across versions, each with the answer it changed: not written yet.
