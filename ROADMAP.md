# Roadmap

What is being built next. Items move to the [Changelog](CHANGELOG.md) when they ship.

## In progress

- **Architecture commands:** `wingbox init` detects the architecture style of each part of a repository and writes `.wingbox/config.yaml`; `wingbox place` says where new code belongs; `wingbox align` finds the gaps against a chosen style; `wingbox learn` lets a coding agent read the unclear folders and answer in a fixed format that Wingbox checks; `wingbox compare` lines up the parts of a monorepo. Rules a project already declares (dependency-cruiser layers and similar) come first.
- **`wingbox export coderabbit`:** turns the architecture into CodeRabbit review configuration: review instructions per layer and ast-grep rules that flag forbidden imports.
- **Benchmarks:** a public find-usages benchmark for `wingbox usages`, and build times of every compared tool measured on a quiet machine.

## Next

- **Propose the layers, then hold them:** for a project that has no declared layers, `wingbox align --learn` proposes a full layer table (layers lowest first, folder patterns, the few files pinned to another layer than their folder suggests) from the graph and the architecture library, for a person to edit. Once accepted, the layers are held the way large monorepos hold them by hand today:
  - a check that fails on any new upward import, run locally and in CI;
  - a list of the existing upward imports in `.wingbox/baseline.sexp` that may only shrink: a fixed one must be removed from the list, a new one can be added only on purpose;
  - required imports that can never become exceptions (an entry point must bind its ports);
  - optionally one TypeScript project (or package) per layer, with a check that every file sits in the project its layer names;
  - export of the same rules to the tools a team already runs: CodeRabbit review instructions and ast-grep rules, and a dependency-cruiser configuration.
  - checks of what a layer does, not only what it imports: SQL in the repository (DAO) layer and not run from controllers or services, outbound HTTP in adapters, no web framework in the domain, and the other placement rules of clean architecture styles.
  Proposal and solution: [docs/layer-control.md](docs/layer-control.md).
- **Rules for external packages, added through the agent or by hand:** the graph records which file imports which external package (npm, PyPI, crates, Maven, Composer and the rest), and a project can state rules about them: "the core must not import `vscode`", "only the adapters may import the cloud SDK", "this package is allowed in tests only". A dedicated command adds a rule in two ways: in a dialogue with the coding agent, where Wingbox lists the packages each layer imports today and the agent proposes rules for the operator to confirm, or by hand in `.wingbox/config.yaml`. `wingbox check` holds these rules like the layer rules, with the same shrinking baseline and the line of the import as evidence, and the exports (CodeRabbit, dependency-cruiser) carry them. Today only the built-in capabilities of 0.6.0 (database client, outbound HTTP, web framework) look at external packages; a rule about any other package cannot be stated, and a project's own rule of this kind (for example a dependency-cruiser rule that forbids a package in a layer) is not reproduced.
- **Confidence on every edge:** each dependency carries how it was found (resolved from the import, inferred, or ambiguous), so an agent knows which links to trust.
- **A short verdict before an edit:** `SAFE`, `CAUTION` or `DANGEROUS` for a file, from its blast radius and the layers it touches, with capped lists and counts of what was left out.
- **Failed queries say so:** a query that could not finish is marked as failed, so it never reads as "nothing depends on this".
- **A size limit on answers:** long answers stop at a fixed size and the rest goes to a file in `.wingbox/`, with the path in the answer.
- **A graph of uncommitted changes:** the working tree's edits as a separate layer over the committed graph, so impact can be asked for what is not committed yet.
