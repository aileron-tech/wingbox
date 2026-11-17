# Roadmap

What is being built next. Items move to the [Changelog](CHANGELOG.md) when they ship.

## Recently shipped

- **0.7:** native Windows support; hooks and the plugin work without Git Bash; the test suite runs on Linux, macOS and Windows with Python 3.11 to 3.13.
- **0.6:** architecture commands (`init`, `learn`, `align`, `place`, `advice`, `compare`); checks of what a layer does (SQL in repositories, outbound HTTP in infrastructure, no web framework in the domain); `boundaries` says "not checked" where it did not read a project's rules; the find-usages benchmark; the MCP Registry entry.

Details are in the [Changelog](https://github.com/aileron-tech/wingbox/blob/main/CHANGELOG.md).

## In progress

- **Windows in real sessions:** confirming hooks, the plugin and the background refresh inside Claude Code and Codex on Windows with the people who asked for it.
- **Benchmarks:** build times of every compared tool, measured one at a time on a quiet machine.

## Next

- **`wingbox export coderabbit`:** turns the architecture into CodeRabbit review configuration: review instructions per layer and ast-grep rules that flag forbidden imports.
- **Propose the layers, then hold them:** for a project that has no declared layers, `wingbox align --learn` proposes a full layer table (layers lowest first, folder patterns, the few files pinned to another layer than their folder suggests) from the graph and the architecture library, for a person to edit. Once accepted, the layers are held the way large monorepos hold them by hand today:
  - a check that fails on any new upward import, run locally and in CI;
  - a list of the existing upward imports in `.wingbox/baseline.sexp` that may only shrink: a fixed one must be removed from the list, a new one can be added only on purpose;
  - required imports that can never become exceptions (an entry point must bind its ports);
  - optionally one TypeScript project (or package) per layer, with a check that every file sits in the project its layer names;
  - export of the same rules to the tools a team already runs: CodeRabbit review instructions and ast-grep rules, and a dependency-cruiser configuration.
  Proposal and solution: [docs/layer-control.md](docs/layer-control.md).
- **Rules for external packages, added through the agent or by hand:** the graph records which file imports which external package (npm, PyPI, crates, Maven, Composer and the rest), and a project can state rules about them: "the core must not import `vscode`", "only the adapters may import the cloud SDK", "this package is allowed in tests only". A dedicated command adds a rule in two ways: in a dialogue with the coding agent, where Wingbox lists the packages each layer imports today and the agent proposes rules for the operator to confirm, or by hand in `.wingbox/config.yaml`. `wingbox check` holds these rules like the layer rules, with the same shrinking baseline and the line of the import as evidence, and the exports (CodeRabbit, dependency-cruiser) carry them. Today only the built-in capabilities of 0.6.0 (database client, outbound HTTP, web framework) look at external packages; a rule about any other package cannot be stated, and a project's own rule of this kind (for example a dependency-cruiser rule that forbids a package in a layer) is not reproduced. The design: packages become nodes (`pkg:npm/vscode`, `pkg:std/os`) in a built-in lowest layer `external`, and these rules are allow and forbid lists per layer on that layer ([docs/edge-model.md](docs/edge-model.md)).
- **Edge kinds and outbound access:** every edge has a kind (import, type, re-export, dynamic, runtime, mock, asset, external), so rules, queries and the map choose what they follow; access to databases, HTTP, gRPC, messaging, remote shells and processes comes from a shipped catalog of libraries through the dependency graph, as edges to system nodes (`sys:db/postgres`, `sys:messaging/rabbitmq`). Everything new is hidden by default: a switch per kind on the map, `--edges` in the CLI and MCP tools, and `pkg:` and `sys:` targets for `impact` and `usages` ([docs/edge-model.md](docs/edge-model.md)).
- **Confidence on every edge:** each dependency carries how it was found (resolved from the import, inferred, or ambiguous), so an agent knows which links to trust.
- **A short verdict before an edit:** `SAFE`, `CAUTION` or `DANGEROUS` for a file, from its blast radius and the layers it touches, with capped lists and counts of what was left out.
- **Failed queries say so:** a query that could not finish is marked as failed, so it never reads as "nothing depends on this".
- **A size limit on answers:** long answers stop at a fixed size and the rest goes to a file in `.wingbox/`, with the path in the answer.
- **A graph of uncommitted changes:** the working tree's edits as a separate layer over the committed graph, so impact can be asked for what is not committed yet.

## Site and reach

So that people who search for this kind of tool can find it. In this order:

- **Directories:** list Wingbox where MCP servers, agent skills and Claude Code plugins are looked up: the official MCP Registry, mcpservers.org, Glama, PulseMCP, the Claude plugin directories and the curated lists of MCP servers and Claude Code tools.
- **A page per question on wingbox.dev:** one page per integration (Claude Code, Codex, Cursor, MCP), per task (blast radius of a change, find usages, architecture rules, monorepos) and per language, each with a real example and the measured numbers; the term people search for, code knowledge graph, used where it applies.
- **Comparisons kept current:** the public comparison with other code-graph tools rerun on new versions, and offered to the lists and comparison sites that cover this category.
- **Articles:** the benchmark method and results written up where developers read (DEV Community, Hacker News), with the scripts to reproduce them.

Distribution channels, each one a place where a developer or an agent already looks for tools:

- **Editor marketplaces:** a VS Code extension and a JetBrains Marketplace plugin that register the `wingbox mcp` server and the skill, so the IntelliJ family and VS Code get the graph without a manual setup.
- **Assistant directories:** the Claude plugin directory and a Claude Desktop extension for the local MCP server; the ChatGPT and Codex plugin marketplace when it takes local tools.
- **GitHub and GitLab, in the review itself:** a GitHub Action (its own repository, `aileron-tech/wingbox-action`, listed in GitHub Marketplace) and a GitLab CI/CD component that run Wingbox on a pull or merge request and write one comment: the files that depend on the change and were not touched by it, and the layer findings the change introduces. Both are run on our own repositories and their past requests before they are published.
- **An architecture picture for the README:** `wingbox export svg` draws the layers of a repository as a static picture (layers lowest first, the modules in them, the dependencies between layers, counts), in a light and a dark version, straight from the graph and the same for the same code. The GitHub Action keeps it current on every change of the default branch (in a branch of its own by default, so the project's history stays clean), together with a small badge (layers and findings), and prints the lines to paste into a README. Each picture carries a link back to Wingbox.
- **Rankings and the remaining MCP catalogues:** the catalogues that read the official MCP Registry, and the ones that take a submission (Best MCP, PulseMCP, mcp.so, Smithery).
- **Documentation for agents:** every page of wingbox.dev also served as Markdown, next to `llms.txt`.
- **Community:** GitHub Discussions first, a chat when there are users to fill it; issues marked for first-time contributors; a free architecture review of an open-source repository on request, published as an example page.
- **Other audiences:** the same material as short videos and posts on LinkedIn and YouTube; talks at meetups and online events; start-up and open-source programmes that list their members.
- **What users say:** every public mention answered and reposted; a milestone (stars, downloads, a new directory) announced the day it happens.
