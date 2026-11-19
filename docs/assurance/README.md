# Assurance material: what a team needs to justify the tool

A team that relies on Wingbox to decide what to read, test and review before a change has to justify the tool itself, not only read its output. This folder puts the material for that in one place. All of it is taken from the repository: the tests, the benchmark reports, the README, the changelog and the log. Nothing was measured for these pages.

| Page | What it answers | Kept current by |
|---|---|---|
| [requirements.md](requirements.md) | What is the tool required to do, and which test verifies each requirement? One table per area, one row per test | generated; a test compares it with the generator's output |
| [verification-plan.md](verification-plan.md) | Which claim does Wingbox make, which test class or benchmark answers it, and which command reruns it? What is planned and not done | by hand; a test checks that every cited file and test class exists |
| [known-limits.md](known-limits.md) | What does the graph not see, per language, and what does that mean for an answer? Every line names its source | by hand; the same test |
| [tool-errors.md](tool-errors.md) | Which wrong answer can hide a defect of the user's code, how does the tool report the condition, and which independent check catches it? | by hand; the same test |
| [`release-integrity.md`](release-integrity.md) | What ties the files of a release to the source of its tag: actions at a commit, the checksum file, the SBOM, rebuilding a tag | by hand |

Where the sources of the repository do not say, a page says "not assessed". It does not guess.

## Keeping the pages current

`requirements.md` is generated from the docstrings of `tests/`. After a test is added, removed or reworded:

```bash
python3 tools/requirements_index.py           # rewrites docs/assurance/requirements.md
python3 tools/requirements_index.py --check   # exit 1 when the file is stale, writes nothing
```

`tests/test_assurance_docs.py` fails while the committed file differs from that output (REQ-DOC-004), and when a page of this folder links to a file or names a test class that no longer exists (REQ-DOC-007):

```bash
python -m unittest tests.test_assurance_docs
```

The other pages are written by hand. They change when their sources change: a limit stated in the README or a changelog entry, a benchmark report, the fact or exit status a command prints.

## What this material is not

This folder is a record of the tool: what it is required to do, how that is tested, what it is known to miss and how a wrong answer is caught. A reviewer can use it as technical evidence when deciding whether and how to rely on Wingbox. It is not a qualification: nothing here, alone or together, qualifies Wingbox for a regulated use, certifies it against a standard, or replaces the assessment that the rules of a regulated field ask of the organisation that uses a tool. Wingbox is provided under the MIT license, without warranty.
