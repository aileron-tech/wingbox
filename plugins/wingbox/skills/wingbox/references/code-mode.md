# Wingbox from code: Claude Code's Code Mode

In Code Mode the assistant writes JavaScript and runs shell commands with `` $`...` ``. Every Wingbox
query answers in JSON with `--json`, so one helper turns each question into one call. An array inside
`` $`...` `` becomes separate arguments, so several files go into one call.

Define the helper once; it stays defined in the blocks that follow (other top-level constants of a block
do not, so keep results you need later in `var` or return them).

```javascript
const wingbox = async (...args) => {
  const answer = (await $`wingbox ${args} --json`.nothrow()).json();
  if (answer.error) throw new Error(`wingbox ${args[0]}: ${answer.error.code} ${answer.error.message}`);
  return answer;
};
```

A call that fails as a whole (no graph in the folder, a name nothing matches, an argument that is not
valid) answers `{"error": {"code", "message"}}`, and the helper throws with that reason.

## Before a change: what depends on these files

```javascript
const { graph, impact } = await wingbox("impact", ...files, "2");
var dependents = Object.fromEntries(impact.map(a => [a.file, a.error ?? a.rows.map(r => r.file)]));
```

One call for all the files. `rows` lists each dependent file once, never the file itself, and is not cut;
`total` is their number. Each row says why it is listed: `depth`, `via`, `through`, `reasons`. The answer
is read from the import statements and uses of every source file of the graph, test files included;
`graph.files` is how many, and `graph.stale` is 0 when none changed since the graph was built. A file that
is not in the graph has `error` in place of `rows`.

## Every use of a function, class or type

```javascript
const { usages } = await wingbox("usages", "retryPolicy");
var places = usages.rows.map(r => `${r.file}:${r.line} ${r.kind}`);
```

`kind` says how the place uses it (`call`, `import`, `type`, ...). A name that two files define is asked
as `module::name`.

## Where a feature lives

```javascript
const found = (await wingbox("quick", "billing")).facts;
var toRead = [...new Set(found.map(f => f.file).filter(Boolean))];
```

## What changed since a commit, and the tests that use it

```javascript
const changed = (await wingbox("diff_functions", "HEAD")).facts.filter(f => f.fact === "diff-fn").map(f => f.args[0]);
var tests = new Set();
for (const item of changed) {
  for (const row of (await wingbox("usages", item)).usages.rows) {
    if (/(^|\/)tests?\/|(_test|\.test|\.spec)\.|(^|\/)test_/.test(row.file)) tests.add(row.file);
  }
}
```

## Architecture rules

```javascript
const rules = (await wingbox("boundaries")).facts;
var violations = rules.filter(f => f.fact.endsWith("violation"));
var notChecked = rules.filter(f => f.fact === "boundaries-not-checked");
```

A part in `notChecked` was not checked: no finding there is not a pass.
