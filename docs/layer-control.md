# Layer control: propose the layers of a project, then hold them

Status: proposal, not built. Roadmap item "Propose the layers, then hold them" ([ROADMAP.md](../ROADMAP.md)).

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

1. Propose a complete layer table for a project without one, from the graph and the architecture library, for a person to review and edit.
2. Hold the accepted layers: fail on a new upward import, keep the existing ones visible in a list that can only shrink, and never let a required rule become an exception.
3. Work the same locally, in an agent's hooks and in CI, with no network and no model.
4. Export the same rules to the tools a team already runs: CodeRabbit (review instructions and ast-grep rules) and dependency-cruiser.

Non-goals:

- Moving files or rewriting imports. Wingbox proposes and checks; people and agents change the code.
- Replacing declared rules. A project that already declares its layers (dependency-cruiser, layers tables and similar) keeps them as the source of truth; Wingbox reads them and holds them, it does not propose different ones.
- Splitting the build into compiler projects automatically. Wingbox can check the split; creating it stays with the team.

## 3. Proposal (what a user does)

```sh
wingbox build .
wingbox align --learn            # propose the layer table; a person edits .wingbox/config.yaml
wingbox check                    # exit 1 on any new upward import; run in CI
wingbox check --update           # rewrite the baseline after fixes; refuses to add new entries
wingbox check --update --allow-new   # add new entries on purpose, each with a reason
wingbox export coderabbit        # review instructions and ast-grep rules under .wingbox/coderabbit/
wingbox export dependency-cruiser    # a .dependency-cruiser config the team can run as it is
```

The output of every command is a list of S-expression facts, printed as text for people (`--sexp` gives the facts).

## 4. Solution

### 4.1 The layer table

Kept in `.wingbox/config.yaml`, which people edit and commit:

```yaml
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
5. Write the proposal into `.wingbox/config.yaml` with comments, and the remaining upward imports into the baseline.

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

### 4.4 Optional: one project per layer

When a team splits its build into compiler projects (TypeScript project references, packages), `wingbox check --projects` verifies that every source file is in exactly the project its layer names and that each project references only lower layers. Wingbox does not create the projects.

### 4.5 Export

- `wingbox export coderabbit`: per layer, a `reviews.path_instructions` entry (what the layer is, what it may and may not import, how many files depend on it), and ast-grep rules that flag a forbidden import per language, written under `.wingbox/coderabbit/`; an existing `.coderabbit.yaml` is never overwritten, the block to merge is printed.
- `wingbox export dependency-cruiser`: the same table and baseline as a dependency-cruiser configuration, for teams that already run it.

## 5. Testing

- A fixture monorepo per language with a known table: the proposal places every folder as expected; the check passes; adding one upward import fails it; removing a baselined import without `--update` fails it.
- A public repository with a declared table: reading it gives exactly the project's own known upward imports (this already holds on one repository: the same 112 of 112).
- Exports: the generated dependency-cruiser config, run by dependency-cruiser, reports the same upward imports as `wingbox check`; the ast-grep rules match the same imports on fixtures.
- Determinism: the same code gives the same proposal and the same baseline, byte for byte.

## 6. Open questions

- The command name for the check: `wingbox check`, or a flag of `boundaries`.
- Whether `--allow-new` should require a reason for every entry.
- How to present a proposal for a repository with many parts: one config, or one per part with a summary.
