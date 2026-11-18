# Edge model: kinds of edges, external packages and outbound access

Status: proposal, not built. Roadmap items "Rules for external packages" and "Edge kinds and outbound access" ([ROADMAP.md](../ROADMAP.md)). Layer rules and their checks: [layer-control.md](layer-control.md).

## 1. Problem

The code graph has one kind of dependency: a repository file imports another repository file. That answers "what does a change break" well, but three things the architecture checks need fall outside it:

- **Rules about packages.** A common rule is "the core must not import `vscode`" or "the domain uses the standard library only". The graph has no node for a package and keeps no record of an import that resolves to no file of the repository, so such a rule cannot be evaluated. A boundary test on public repositories that check their own rules found that every miss came from this: rules whose target is a package, or a generated path that does not exist in the repository.
- **Imports that are not dependencies of the code.** A test that mocks a module (`vi.mock("./x")`, `jest.mock`) is recorded as an import; it matters for impact (the test breaks when the module moves) but not for a layer rule. An import of a JSON, CSS or other non-code file gives no edge at all, so a change to a configuration file shows no dependents.
- **What a file reaches outside the repository.** Databases, HTTP services, message brokers, remote shells and processes are reached through libraries. Today Wingbox finds some of this by the syntax of each line (capability rules); that is approximate and detects the same fact in two ways.

## 2. Goals and non-goals

Goals:

1. Every edge has a kind, so a rule, a query and the map can choose which kinds they follow.
2. External packages are nodes of the graph, reached by edges that carry the line of the import, and they form a layer of their own, so that rules about packages are ordinary layer rules.
3. Access to outside systems is found through the dependency graph from a catalog of libraries, exactly as precise as the import statements.
4. Nothing new appears in an existing answer by default: file and module counts, impact lists, the map and the report totals stay as they are unless a kind is asked for.

Non-goals:

- Following calls at run time, or proving that a file that imports a database library really runs a query. An edge to a system means "this file uses a library that reaches it".
- Versions of packages, licences and vulnerabilities. The package nodes make such a check possible later; it is not part of this design.

## 3. Proposal

### 3.1 Kinds of edges

| Kind | What it is | Followed by default |
|---|---|---|
| `import` | a repository file imports another one (the default, implicit in the graph file) | yes |
| `type` | an import of types only (`import type`), erased by the compiler | yes, as today |
| `re-export` | a module re-exports names of another one | yes, as today |
| `item` | Rust: a file names an item that a module facade re-exports (`pub use child::*`, `pub use child::Name as Alias`); the edge goes to the file that defines the item, next to the edge to the facade (`:item`, with `:candidate` for a glob import whose body names the item; since 0.9.1). Also a member named through a type path (`Type::member`): the edge goes to the file whose inherent `impl` block defines the member | yes |
| `dynamic` | a module loaded at run time from a literal name (`import()`, `importlib`) | yes |
| `runtime` | a module loaded by a name read from configuration (the plugin-loader edges) | yes, as today |
| `mock` | a test mocks a module (`vi.mock`, `jest.mock`) | yes for impact; no for layer rules |
| `asset` | a file imports a non-code file of the repository (JSON, CSS, an image, `?inline` queries included) | no |
| `external` | a file imports a package from outside the repository | no |

The graph file keeps the existing spelling where it already exists (`:type-only`, re-export) and adds one flag per new kind on import edges (`:mock`, `:dynamic`). Asset and external edges are facts of their own, so readers of the graph file that do not know them are not affected.

### 3.2 External packages: nodes and a layer

- One node per package: `pkg:<ecosystem>/<name>` (`pkg:npm/vscode`, `pkg:npm/@aws-sdk/client-s3`, `pkg:pypi/requests`, `pkg:cargo/serde`, `pkg:go/github.com/gin-gonic/gin`, `pkg:maven/org.springframework`, `pkg:nuget/Newtonsoft.Json`, `pkg:gem/faraday`, `pkg:composer/illuminate/support`, `pkg:pub/http`). The standard library and runtime built-ins get `pkg:std/<name>` (`pkg:std/os`, `pkg:std/fs`), so a rule can tell them apart.
- Each importing file has an `external` edge to the package with the line of the import.
- An import that resolves to nothing and is not a package (a relative path, a path alias of the project, a generated file that is not in the repository) stays an `unresolved-import` fact with its line. No node is invented for it.
- Packages form the built-in layer `external` of every part, always the lowest one: any layer may import it unless a rule says otherwise, and it imports nothing. It exists in every style and in declared tables without being written down. On the map it has its own fixed colour, a reserved slot of the palette that does not move the colours of the existing layers, and its name is written as text.

### 3.3 Rules about packages

Package rules are layer rules on the `external` layer, stated per layer in `.wingbox/config.yaml`:

```yaml
parts:
  .:
    style: layered
    external:
      domain: {allow: ["pkg:std/*"], forbid: ["*"]}
      infra:  {allow: ["pkg:npm/@aws-sdk/*"]}
```

A broken rule is an ordinary violation with the line of the import, for example `(violation src/core/task.ts pkg:npm/vscode :src-layer core :tgt-layer external :rule forbid-package :line 1)`. It goes through the same baseline and the same not-checked logic as every other layer rule. A rule of a project's own checker whose target names a package (a dependency-cruiser `to.path` such as `^vscode$`) maps onto this layer.

### 3.4 Outbound access

- **Catalog.** A catalog of libraries that reach outside the process is shipped as data: one TOML file per ecosystem (npm, PyPI, Go, crates, Maven, NuGet, RubyGems, Packagist, pub) and one for the standard libraries. Each entry maps a package, a standard module or an import path prefix to a category, and to a system when the library implies one. Categories: `db` (drivers and ORMs), `http`, `grpc`, `rpc`, `ssh`, `telnet`, `ftp`, `smtp`, `ldap`, `messaging` (rabbitmq, nats, kafka, mqtt, cloud queues, job queues), `cache`, `search`, `object-storage` and cloud SDKs, `process`, `socket`. A test checks that the files parse, have no duplicate keys and use only these categories.
- **Edges from the graph.** A file's `external` edge to a catalogued package gives an outbound edge of that category from the file to a system node: `sys:db`, `sys:db/postgres` when the driver implies it, `sys:http`, `sys:grpc`, `sys:messaging/rabbitmq`, `sys:ssh`, and so on. These nodes belong to the `external` layer as their own group. `wingbox impact sys:messaging/rabbitmq 3` then lists the files that talk to RabbitMQ and what depends on them.
- **Syntax as a supplement.** Line patterns are used only where no import exists (globals such as `fetch` or `WebSocket`) or to refine an edge: a SQL string passed to a call marks the edge and names the tables, a literal URL host or command refines the target (`sys:http/<host>`, `sys:process/<command>`). A query built in a variable and run elsewhere stays a candidate and gets no edge.
- **Project wrappers.** `.wingbox/config.yaml` may add entries, `outbound: {"@acme/db-client": db, "src/lib/http.ts": http}`; a repository module declared as a wrapper becomes the entry point and the files that import it are reached through the graph. `wingbox learn` asks the coding agent to name such wrappers, and `analyze --edges external` lists the packages many files import that the catalog does not know.
- The capability rules read these edges (category to capability), so one fact is not detected twice.

### 3.5 Shown only when asked for

- **Map:** a compact "Edges" switch group with one switch per kind present in the graph and its count: imports on, type-only as today, runtime as today, mocks, assets, external packages and each outbound category group (Database, HTTP, RPC and gRPC, Messaging, Remote shell, Process, Other) off. Each outbound group has its own fixed colour with a text label, checked for contrast and separation like the layer palette. The state is kept in the URL hash next to the view (`#view=layers&edges=import,external`), so a link reproduces it. Package and system nodes and their edges are created only when switched on, so the page stays as fast as today.
- **CLI and MCP:** one flag selects the kinds, `--edges import,type,external`, accepted by `impact`, `usages`, `analyze` and `deps` and by the MCP tools. A `pkg:` or `sys:` target works without the flag: `wingbox impact pkg:npm/lodash` and `wingbox usages pkg:npm/lodash` list the files that use the package and the files that depend on those, the question behind an upgrade or a vulnerability.
- **REPORT.md:** the totals stay as they are; one short table "Other edges" gives the count per kind, and the layer table lists `external` with its number of packages and importing files.

## 4. Solution

- The language adapters already see every import. The builder records the line of each import and the form of the import (mock, dynamic) in the parse result; resolution decides between a repository file, a package, a non-code file and an unresolved specifier.
- External, asset and unresolved records are made per module (so a partial update reuses them) and given their final form when the graph is assembled: a Python import of the repository's own package, a TypeScript path alias or the project's own PHP namespace that resolves to nothing is unresolved, not a package. A partial update publishes the same bytes as a full build.
- Graph file: `(package <id> "pkg:...")`, `(external <file> <package> :line N)`, `(asset <file> "<path>" :line N)`, `(unresolved-import <file> "<specifier>" :line N)`, and the outbound edges with their system nodes. The header counts only repository files and their edges.
- The query script keeps these edges apart from the file edges, follows them only when `--edges` names their kind, and treats `pkg:` and `sys:` targets as nodes of the `external` layer.

## 5. Testing

- Per language adapter that sees imports (Python, TypeScript and JavaScript, Rust, Go, Java, C#, Ruby, PHP, Dart): the external edge with its line and the standard library split.
- TypeScript: a path alias and a missing relative file are unresolved; an existing JSON or CSS file, with a query suffix, is an asset; `vi.mock` gives a mock edge, `import()` a dynamic one.
- Default answers unchanged: header counts, `deps`, `analyze`, `impact`; the published benchmark results stay byte-identical (the impact benchmark's verification on one repository).
- A partial update after adding a package import, an asset import and a mock is byte-identical to a full build.
- Package rules: a forbidden package in a layer is a violation with its line, the baseline accepts it, a part without rules is reported as not checked.
- Catalog files: parse, no duplicate keys, fixed categories; an import of a catalogued driver gives the system edge with the import line.
- Map: headless screenshots with the switches off and on, in both themes.

## 6. Open questions

- Whether `analyze` lists a file's packages by default (an existing answer would change) or only with `--edges`.
- How far the catalog goes for each ecosystem in the first version, and who reviews additions.
- The name of the per-layer key for package rules in `.wingbox/config.yaml` (`external` is used above).
