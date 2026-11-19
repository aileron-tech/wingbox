# Layer control: propose the layers of a project, then hold them

Status: proposal, not built. Roadmap item "Propose the layers, then hold them" ([ROADMAP.md](../ROADMAP.md)). Rules about external packages and the kinds of edges they rely on: [edge-model.md](edge-model.md).
Shipped in 0.6.0: the open points settled through the agent (`wingbox learn`, the questionnaire without an agent, the instruction header of `.wingbox/config.yaml`) and the capability rules with their three pattern checks (section 4.4); `wingbox check`, pinned files, `required` imports and the exports are not built yet.

## 1. Problem

Large monorepos split their code into layers so that a lower layer never imports a higher one. The reasons are practical: the code stays understandable, a change reaches a known set of files, and the build can be split into smaller projects that type-check within a fixed memory budget.

Teams that do this well today write and keep the layers by hand:

- a table of layers, lowest first, with a path pattern per layer and a list of single files pinned to another layer than their folder suggests;
- a dependency checker in CI (for example dependency-cruiser) configured with that table, which fails on an import that goes up;
- a list of the upward imports that already exist, which the check accepts and which may only shrink;
- sometimes one compiler project per layer, with a script that checks every file sits in the project of its layer.

That table can run to a thousand lines and is written by people. Most projects never get one, and coding agents, which add imports quickly, push such a project's structure further apart with every change.

Wingbox already builds the graph this needs: every file-to-file dependency, checked against the import statements (0 wrong, 0 missing in the published benchmarks). It already reads a project's own declared layers and reproduces that project's own check exactly. What is missing is the rest of the loop: proposing the table for a project that has none, and holding it afterwards.

## 2. Goals and non-goals

Goals:

1. Propose a complete layer table for a project without one, from the graph and the architecture library, and settle the unclear points with the coding agent (and, through it, with the operator) - not by asking a person to edit files.
2. Hold the accepted layers: fail on a new upward import, keep the existing ones visible in a list that can only shrink, and never let a required rule become an exception.
3. Check what a layer does, not only what it imports: SQL in the repository (DAO) layer and not in controllers or services, outbound HTTP in adapters, no web framework in the domain, and the other placement rules of clean architecture styles.
4. Work the same locally, in an agent's hooks and in CI, with no network and no model.
5. Export the same rules to the tools a team already runs: CodeRabbit (review instructions and ast-grep rules) and dependency-cruiser.

Non-goals:

- Moving files or rewriting imports. Wingbox proposes and checks; people and agents change the code.
- Replacing declared rules. A project that already declares its layers (dependency-cruiser, layers tables and similar) keeps them as the source of truth; Wingbox reads them and holds them, it does not propose different ones.
- Splitting the build into compiler projects automatically. Wingbox can check the split; creating it stays with the team.

## 3. Proposal (who does what)

Nobody edits the layer table by hand in the normal flow. Wingbox drafts it, and the coding agent settles what Wingbox cannot decide:

- **With an agent (the normal case).** `wingbox align --learn` (run by the agent, or `/wingbox learn`) proposes the table and prints, for the agent, the points it could not decide: unplaced folders with example files and their imports, ties between styles, files that could be pinned lower. The agent reads the code and answers in the fixed format `learn` defines; Wingbox checks the answer and writes the table. When the agent cannot tell from the code (for example, whether a folder is meant as a public API or an internal helper), it asks the operator in its chat and passes the answer on. Every answer is kept in `.wingbox/learned.sexp` with its reason, so the next run does not ask again.
- **Without an agent (a person in a terminal).** `wingbox align --learn` runs an interactive questionnaire: which style the part follows (the closest candidates with their scores and one-line descriptions), then one question per unclear folder with its example files. It writes the table and then offers to open `.wingbox/config.yaml` in the editor for a final look.
- **The file explains itself.** `.wingbox/config.yaml` starts with a header of instructions (what each key means, the rule that decides a file's layer, how to add a pinned file or a required import, and that `wingbox check` must pass after a change), so that an agent asked to "move this folder to the services layer" can edit it correctly, and a person can too.

```sh
wingbox build .
wingbox align --learn            # draft the layer table; the agent (or the questionnaire) settles the open points
wingbox check                    # exit 1 on any new upward import; run in CI
wingbox check --update           # rewrite the baseline after fixes; refuses to add new entries
wingbox check --update --allow-new   # add new entries on purpose, each with a reason
wingbox export coderabbit        # review instructions and ast-grep rules under .wingbox/coderabbit/
wingbox export dependency-cruiser    # a .dependency-cruiser config the team can run as it is
```

The output of every command is a list of S-expression facts; `--human` prints them as text for people and `--json` as one JSON object (`init` and `learn` print text and give the facts with `--sexp`).

## 4. Solution

### 4.1 The layer table

Kept in `.wingbox/config.yaml`, committed with the project. Wingbox writes it; agents (and people, rarely) change it by following the header:

```yaml
# Wingbox layer table. Read this before you change the file.
# - Each part starts at its root folder. Layers are listed lowest first: a file
#   may import its own layer and the layers below it, never a layer above.
# - A file belongs to the layer that lists it under `files`, else to the first
#   layer whose `paths` match it. Files matching `tests` are in no layer.
# - To move a folder to another layer, move its pattern; to move one file, add
#   it under `files` of the target layer with a `reason`.
# - `required`: imports that must exist; they can never become exceptions.
# - After any change run `wingbox check`; it must pass, or the new upward
#   imports must be fixed or added to .wingbox/baseline.sexp on purpose with
#   `wingbox check --update --allow-new` and a reason.
# - Unsure where something belongs? Run `wingbox align --learn` and answer its
#   questions instead of guessing.
parts:
  - root: packages/backend
    style: layered            # from the architecture library, or "declared"
    layers:                   # lowest first
      - name: schema
        paths: ["src/types/**", "src/database/schemas/**"]
      - name: core
        paths: ["src/config.ts", "src/logging/**", "src/database/**"]
        files: ["src/routes/route-paths.ts"]   # pinned: belongs here, not where its folder suggests
        reason: {"src/routes/route-paths.ts": "read by config.ts"}
      - name: services
        paths: ["src/services/**"]
      - name: routes
        paths: ["src/routes/**"]
    tests: ["**/*.test.ts", "src/test/**"]    # in no layer; may import anything
    required:
      - from: "src/server/*.ts"
        must_import: "src/composition/**"      # an entry point binds its ports
```

A file belongs to the layer that pins it, else to the first layer whose path matches it; tests belong to none. This is the same rule a hand-written table uses, so a declared table and a proposed one mean the same thing.

### 4.2 Proposing the table (`align --learn`)

1. Detect the style of each part (the architecture library: layered, onion and clean, hexagonal, MVC, route-tree, feature-sliced and others), take the closest one.
2. Place each folder by the style; place the folders the style does not cover by their names and file patterns, as `learn` already does.
3. Order the layers by the graph: count imports between candidate layers and choose the order with the fewest upward imports.
4. Pin single files: a file whose importers sit below its folder's layer, and which imports nothing above them, is proposed for the lower layer, with the reason. On a real monorepo this kind of pinning removed about a third of the upward imports without touching code.
5. Settle the open points with the agent (the `learn` task and answer format) or, without an agent, with the interactive questionnaire; then write `.wingbox/config.yaml` with its instruction header, and the remaining upward imports into the baseline.

A part with declared rules skips steps 1 to 4: its rules are read as they are.

### 4.3 Holding the table (`wingbox check`)

- The check computes every dependency that goes from a layer to a higher one, using the graph and the table.
- `.wingbox/baseline.sexp` lists the accepted ones, one fact per import, each with the date and the reason:
  `(upward src/services/a.ts src/routes/b.ts :since 2026-11-17 :reason "legacy, ticket 123")`.
- Exit 1 on an upward import that is not in the baseline, and on a baseline entry that no longer exists (so a fix must also remove the entry and the list only shrinks).
- `--update` rewrites the baseline without the entries that are gone and refuses to add new ones; `--allow-new` adds them on purpose and asks for a reason.
- `required` rules are never accepted by the baseline: they fail every time.
- Type-only imports are reported apart: the compiler erases them, but a change of the type still reaches the importer.
- The check reads the graph that the freshness hooks keep current, so in an agent session it answers within the hook's time budget; in CI it runs after `wingbox build`.

### 4.4 What a layer may do: capability rules

The direction of imports is not the whole architecture. Layered, clean and hexagonal styles also say what kind of work belongs where: SQL lives in the repository (DAO) layer and is not run from controllers or services; outbound HTTP lives in adapters; the domain does not import the web framework; configuration is read in one place. Wingbox checks these as capabilities.

A capability is something a file does that the parser can see without running the code:

| Capability | What counts (examples) |
|---|---|
| `sql` | a string literal that starts with a SQL statement passed to a call (`execute`, `query`, `raw`), a query-builder chain, a session or entity-manager query |
| `db-client` | an import of a database driver or ORM client (psycopg, SQLAlchemy, Prisma, Knex, Drizzle, TypeORM, ActiveRecord, Eloquent, JDBC, Entity Framework) |
| `http-client` | outbound HTTP (requests, httpx, fetch, axios, HttpClient) |
| `framework` | web framework imports and decorators (FastAPI, Express, NestJS, Spring, Rails controllers) |
| `env` | reading the environment (`os.environ`, `process.env`) |
| `filesystem`, `process` | file I/O; starting processes |

Each style of the architecture library states where a capability is allowed, and the config can change it:

```yaml
    capabilities:
      sql: {allowed_in: [repository, infra]}
      db-client: {allowed_in: [repository, infra]}
      http-client: {allowed_in: [infra]}
      framework: {forbidden_in: [domain]}
      env: {allowed_in: [config]}
```

- `wingbox check` reports a capability outside its layers with the line that shows it:
  `(capability-violation src/services/order.ts :capability sql :layer service :allowed (repository infra) :line 42 :evidence "db.query(\"SELECT ...\")")`.
- The same shrinking baseline applies, and the same exports: an ast-grep rule per capability and layer path, so a review tool flags `db.query(...)` added to a controller.
- A few pattern checks of the chosen style ride on the same data: a controller that calls a repository and skips the service; a domain entity that imports the ORM; a service that builds an HTTP response.
- Detection is by syntax (imports, call names, string literals). What it finds carries its evidence; what it only suspects (a query built in a variable and run elsewhere) is listed as a candidate, not a violation. SQL assembled at run time or hidden behind a helper in another package is outside what static parsing sees, and the report says so.

### 4.5 Optional: one project per layer

When a team splits its build into compiler projects (TypeScript project references, packages), `wingbox check --projects` verifies that every source file is in exactly the project its layer names and that each project references only lower layers. Wingbox does not create the projects.

### 4.6 Export

- `wingbox export coderabbit`: per layer, a `reviews.path_instructions` entry (what the layer is, what it may and may not import, how many files depend on it), and ast-grep rules that flag a forbidden import per language, written under `.wingbox/coderabbit/`; an existing `.coderabbit.yaml` is never overwritten, the block to merge is printed.
- `wingbox export dependency-cruiser`: the same table and baseline as a dependency-cruiser configuration, for teams that already run it.

## 5. Testing

- A fixture monorepo per language with a known table: the proposal places every folder as expected; the check passes; adding one upward import fails it; removing a baselined import without `--update` fails it.
- A public repository with a declared table: reading it gives exactly the project's own known upward imports (this already holds on one repository: the same 112 of 112).
- Capabilities: fixtures per language with SQL in a repository (passes), the same query moved to a controller or a service (fails with the line), a framework import in a domain file (fails), a query built in a variable (candidate).
- Exports: the generated dependency-cruiser config, run by dependency-cruiser, reports the same upward imports as `wingbox check`; the ast-grep rules match the same imports on fixtures.
- Determinism: the same code gives the same proposal and the same baseline, byte for byte.

## 6. Open questions

- The command name for the check: `wingbox check`, or a flag of `boundaries`.
- Whether `--allow-new` should require a reason for every entry.
- How to present a proposal for a repository with many parts: one config, or one per part with a summary.
