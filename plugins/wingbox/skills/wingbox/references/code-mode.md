# Wingbox from code: Claude Code's Code Mode

In Code Mode the assistant writes JavaScript and runs shell commands with `` $`...` ``. Every Wingbox
query answers in JSON with `--json`. For each question below: what one call returns, then what to do
with it. Make the call in one block of code and let the value of that block be the answer, so that the
reply can be written from it at once.

## Before a change: what depends on these files

When the project has a code graph (`.wingbox/` in its folder), one shell command answers for all the
files at once: `wingbox impact <file> <file> ... 2 --json`. In its JSON, `impact` has one entry per file,
in the order given; the `file` of each of its `rows` is a file that depends on it, directly (`depth` 1)
or through one other file (`depth` 2). Each dependent is listed once, never the file itself, and the list
is not cut. It is read from the import statements and uses of every source file of the graph, test files
included; `graph.files` says how many files that is, and `graph.stale` is 0 when none changed since the
graph was built. Make this one call in one block of code and let the value of that block be the object
that maps each file to the list of its rows' files, so that the answer can be written from it at once.

```javascript
const answer = (await $`wingbox impact ${files} 2 --json`.nothrow()).json();
Object.fromEntries(answer.impact.map(a => [a.file, a.error ?? a.rows.map(r => r.file)]));
```

An array inside `` $`...` `` becomes separate arguments. Each row also says why it is listed: `via`,
`through`, `reasons`. A file that is not in the graph has `error` in place of `rows`. A call that fails
as a whole (no graph in the folder, an argument that is not valid) answers
`{"error": {"code", "message"}}`.

## Every use of a function, class or type

`wingbox usages <Name> --json` returns `usages.rows`: every place that uses the name, with `file`, `line`
and `kind` (how the place uses it: `call`, `import`, `type`, ...). The list is complete: every source file
of the graph is read, test files included. A name that two files define is asked as `module::name`.

```javascript
const { usages } = (await $`wingbox usages ${name} --json`.nothrow()).json();
usages.rows.map(r => `${r.file}:${r.line} ${r.kind}`);
```

## Where a feature lives

`wingbox quick <keyword> --json` returns `facts`: the modules the keyword names, with their files.

```javascript
const { facts } = (await $`wingbox quick ${keyword} --json`.nothrow()).json();
[...new Set(facts.map(f => f.file).filter(Boolean))];
```

## What changed since a commit, and the tests that use it

`wingbox diff_functions <ref> --json` returns a `diff-fn` fact for every function or type changed since
the commit; its first argument is the name to give to `usages`. The test files among the uses are the
tests to run.

```javascript
const run = async (...args) => (await $`wingbox ${args} --json`.nothrow()).json();
const changed = (await run("diff_functions", ref)).facts.filter(f => f.fact === "diff-fn").map(f => f.args[0]);
const tests = new Set();
for (const item of changed) {
  for (const row of (await run("usages", item)).usages.rows) {
    if (/(^|\/)tests?\/|(_test|\.test|\.spec)\.|(^|\/)test_/.test(row.file)) tests.add(row.file);
  }
}
({ changed, tests: [...tests] });
```

## Architecture rules

`wingbox boundaries --json` returns `facts`: a fact whose name ends in `violation` is a broken rule, and
a `boundaries-not-checked` fact names a part that was not checked: no finding there is not a pass.

```javascript
const { facts } = (await $`wingbox boundaries --json`.nothrow()).json();
({ violations: facts.filter(f => f.fact.endsWith("violation")), notChecked: facts.filter(f => f.fact === "boundaries-not-checked") });
```
