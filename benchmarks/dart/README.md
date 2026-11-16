# Dart and Flutter: graph accuracy check on a real project

`check_accuracy.py` measures one thing on a Dart or Flutter project of your
own: for a file under `lib/`, does the code graph name the same files as the
import statements of the code? It needs no private fixture, no service and no
model: it builds the graph with `scripts/build_code_graph.py`, reads the import
statements of the checkout itself, and compares the two.

It is the check that does not ship a sample project: the acceptance tests in
`tests/test_dart_acceptance.py` prove the adapter on synthetic projects, and
this script proves it on the project you care about, wherever that is.

## Run it

Dependencies of every language plus Dart, then the script:

```bash
cat requirements.txt requirements/*.txt | sort -u > /tmp/wingbox-requirements.txt
uv run --no-project --with-requirements /tmp/wingbox-requirements.txt \
  python benchmarks/dart/check_accuracy.py /path/to/flutter_app
```

Exit status 0 means the graph agrees with the imports of the sampled files;
1 means the aggregate recall is below `--min-recall`, the graph missed an
importer pair, or a sampled file is not a module of the graph; 2 means the path
is not a Dart package.

| Option | Meaning |
|---|---|
| `--sample N` | files to sample from `lib/`, 40 by default; more than the tree holds samples all of them |
| `--seed N` | seed of the random sample, 20261117 by default: the same seed gives the same files |
| `--min-recall X` | exit 1 below this aggregate recall, 0.99 by default |
| `--graph PATH` | read an already built graph instead of building one |
| `--json PATH` | also write every file's result, and every pair that differs, as JSON |
| `--sexp` | print in the graph format instead of text |

The build writes `.code_graph.sexp`, `.code_graph.uses.sexp` and
`.code_graph_cache.json` into the project, as `wingbox build` does; both are in
`.gitignore` of a project that has run the graph once.

## What "agrees" means

The reverse dependents of a file X in the graph are the files with an incoming
`imports` edge to X, the edges `wingbox impact` follows. The reference set is
recomputed on every run from the files on disk: a file names X when one of its
directives resolves to X:

- `import 'package:<name>/<path>'` and `export 'package:<name>/<path>'`, where
  `<name>` is the `name:` of the `pubspec.yaml` that owns the importing file.
- `import '<relative path>'` and `export '<relative path>'`, from the importing
  file's directory after `./` and `../` are dropped.
- `part '<path>'` of a library names that part and `part of '<path>'` names the
  library: the two files share one Dart library and each names the other, so
  each is one dependent of the other.

`dart:` libraries and packages that no pubspec of the project declares are not
project files and are not part of the reference.

Three cases keep the comparison honest:

- A library and one of its parts are linked by two directives, one in each
  file, and the pair is one dependency: the link is counted whichever way the
  `imports` edge runs, and is never an extra dependent. Two parts of the same
  library name neither each other nor the library, so they are not linked.
- A dependent whose edge no import statement backs -- a file that only extends
  a class of X -- is listed as `extra` with its reason (`inheritance-only`) and
  is not counted as a wrong dependent of the import comparison. It is
  reported, not hidden.
- A file of the graph that the disk does not hold (a configuration node of a
  `pubspec.yaml`) is not an importer and is not an extra dependent.

## The report

A sample run on a small synthetic package (`example_app`, 9 files under `lib/`,
one library of two parts), abridged:

```
  file                                           exp found miss extra    right   P     R
  lib/core/models/user.dart                         2     2    0     0    2/2   1.000 1.000
  lib/features/cart/cart.dart                       3     3    0     0    3/3   1.000 1.000
  lib/features/cart/cart_total.dart                 1     1    0     0    1/1   1.000 1.000

  aggregate over 9 sampled files: 11 of 11 importer pairs found; 0 extra dependent(s)
  precision 1.0000  recall 1.0000  (threshold 0.99)
```

`exp` counts the importers on disk, `found` the dependents in the graph, `miss`
the pairs only the disk has, `extra` the graph facts no import backs, and
`right` the importer pairs found out of all of them. The aggregate recall is
the share of importer pairs on disk that the graph holds; precision counts an
`extra` dependent as wrong. A missing importer is printed under the file it
belongs to, so a failure names the file and the specifier class to look at.

The expected dependents of a file are the files whose own directive names that
file: the library's `part` directive names the part, the part's `part of`
directive names the library, and each is one dependent of the other. Nothing
is added by transitivity: a file that imports a part depends on the part, not
on the library that holds it, because it never names the library.

## Limits

- Only direct dependents are compared. A file that reaches X through another
  file is a dependent of that file, not of X, in `wingbox impact` too.
- The reference reads directives with a string scanner that skips comments and
  keeps string literals; a specifier built at run time cannot exist in Dart, so
  nothing is lost there.
- The check never runs `dart analyze` or `flutter`: a project whose generated
  part files (`*.g.dart`, `*.freezed.dart`) are absent fails no more than one
  whose `lib/` was built, because a missing part is not an error to Dart either.
- On a host where the builder's process pool cannot start (no writable
  `/dev/shm`, a sandbox without POSIX semaphores), the script runs the same
  builder with a serial pool in the same process and says so on stderr; the
  numbers are the same, the build is slower.