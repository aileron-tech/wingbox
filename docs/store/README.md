# Wingbox in the plugin directories

Material for listing the Wingbox plugin (`plugins/wingbox`) in Anthropic's
directory (claude.ai, Cowork, Claude Code). The requirements below were read
on 2026-11-16 from:

- Claude Code: code.claude.com/docs/en/plugins-reference (manifest),
  .../plugins/marketplace-reference, .../plugins/publish,
  .../plugins/cli-reference; claude.com/docs/plugins/submit,
  claude.com/docs/plugins/pre-submission-checklist,
  claude.com/docs/plugins/platform-support; the article
  claude.com/resources/articles/build-plugins-for-claude (2026-09-25).

## Listing

| Field | Value |
|---|---|
| Name | `wingbox`; display name Wingbox |
| Publisher | Aileron Technologies |
| One line (at most 30 characters) | Local code graph, blast radius |
| Category | `development` in our marketplace entry |
| Keywords | code-graph, blast-radius, impact-analysis, dependency-graph, code-search, architecture, local-first, mcp |
| Homepage | https://wingbox.dev |
| Repository | https://github.com/aileron-tech/wingbox |
| Support | https://github.com/aileron-tech/wingbox/issues |
| Privacy | https://github.com/aileron-tech/wingbox/blob/main/docs/store/PRIVACY.md ([PRIVACY.md](PRIVACY.md)) |
| Terms | https://github.com/aileron-tech/wingbox/blob/main/LICENSE (MIT) |
| License | MIT |
| Icon | `plugins/wingbox/assets/icon.png`, 512 x 512 PNG, drawn from `docs/assets/wingbox-social-preview.svg`; source [icon.svg](icon.svg) |
| Screenshots | [screenshots/](screenshots): 1200 x 1000 PNG maps of public repositories (svelte, chrome-devtools-mcp, arcane) with the blast radius panel |

Long description:

> Wingbox builds a code graph of the repository you work in and answers from
> it before the agent reads source files: which modules exist and where their
> boundaries are, what depends on a file, what a change could break two hops
> away, which three or four files to read first, and which dependencies cross
> an architecture layer. It parses Python, TypeScript and TSX, Rust, C#, Java,
> Go, Ruby and Ruby on Rails with tree-sitter, and writes an offline HTML map of
> the repository. Everything runs on your computer with the open-source wingbox
> command (MIT, on PyPI): no code, graph or question is sent anywhere. The
> plugin needs a local shell and the command, installed once with
> pipx install wingbox.

Privacy, in one paragraph: no data leaves the machine. The plugin only runs
the local `wingbox` command; the MCP server is that command over stdio; the
only network access is installing the package from PyPI.

Data handling answers (Anthropic portal): reads or stores personal data: no
(it reads the repository's source files locally); sends data to other
services: no; retention: the graph files stay in the repository until the
user deletes them; intended for people under 18: no.

## Test prompts

Run them in a repository with source files (for example a clone of a public
project). Positive:

1. "Build the code graph of this repository and tell me where to start
   reading." Expected: runs `wingbox build .` (MCP tool `build`), summarizes
   `wingbox-out/REPORT.md`: key findings, most depended-on modules, the map.
2. "What depends on src/orders/order.py, and what could break if I change it?"
   Expected: `wingbox impact` (tool `impact`): direct dependents, those within
   two hops, the files to read first.
3. "Which modules handle authentication, and which files should I read
   first?" Expected: `wingbox quick "authentication"` (tool `quick`).
4. "Show the dependencies of the billing module two levels deep." Expected:
   `wingbox deps <module> 2` (tool `deps`).
5. "Which dependencies cross architecture layers here?" Expected:
   `wingbox layers` or `wingbox boundaries` (tools `layers`, `boundaries`).

Negative (the plugin must not act):

1. "Send this repository's graph to my email." Wingbox sends nothing; the
   agent says so.
2. "Deploy this project to production." Not a Wingbox task; no Wingbox tool.
3. "Delete the files that nothing depends on." Wingbox only reads code; it
   may list such files, the deletion is the user's decision and not done by
   Wingbox.

## Anthropic directory (claude.ai, Cowork, Claude Code)

Submission: developer portal https://claude.ai/directory/manage, Submit new,
Plugin bundle. Repository `aileron-tech/wingbox`, plugin path
`plugins/wingbox`, branch `main` (or a release tag).

| Requirement | Status |
|---|---|
| Paid claude.ai plan (Pro, Max, Ultra; Owner on Team or Enterprise); GitHub account connected on claude.ai with push access to the repository | maintainer |
| Public GitHub repository holding `.claude-plugin/plugin.json` in the plugin folder | ready after merge |
| `claude plugin validate --strict` passes | ready (plugin folder and marketplace pass with no warning on Claude Code 2.1.325) |
| Name: lowercase, distinctive, not reserved; `description`, `author`, `version` set | ready |
| README of at least 40 words and a license in the plugin folder | ready (`plugins/wingbox/README.md`, `LICENSE`, `license: MIT`) |
| No symlinks, submodules, LFS, system files; every file the plugin runs inside the plugin folder; no top-level `bin/` | ready (tested) |
| Directory listing fields `icon`, `documentationUrl`, `supportUrl`, `privacyPolicyUrl` in `plugin.json` | ready; the privacy URL resolves once this branch is merged |
| Launcher pinned to an exact version (`uvx --from wingbox==X.Y.Z`) | ready |
| Data handling questions, compliance contact email, four acknowledgements | maintainer, answers above |

Expected review outcome: the version is "held for a reviewer", not blocked.
The portal always holds a plugin that runs a pinned `uvx` package (finding
"Runs a pinned npx or uvx package") and a hook or MCP script that uses a
launcher ("Scripts the validator couldn't follow"). Wingbox cannot avoid it:
the plugin needs the Python package and its tree-sitter parsers. Hooks and the
local MCP server load in Claude Code and Cowork; claude.ai chat loads only the
skill.

## Our own marketplaces (no review)

Claude Code: `.claude-plugin/marketplace.json` (marketplace `aileron-tech`)
lists `./plugins/wingbox`; installation commands are in the main README,
section "Install as a plugin".
