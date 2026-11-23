# Wingbox benchmark: Python names that collide, against the interpreter

This file is written by `run.py --report` from the files in `results/`; no number is typed by hand.

## The question

Which file does an import statement load, when two files could answer to the name? The corpus is 7 small projects (42 files) written by `run.py`, each with a collision on purpose:

- a command line tool `depot.py` beside the package `depot/` it drives, at the root and two folders down;
- a module named like its package (`depot/depot.py`);
- a folder without `__init__.py` beside a module file of its name;
- a package under `src/` with a tool of its name beside it;
- a script that imports the file beside it by its bare name, while a file of that name lies at the root;
- two packages with the same last name, and an import of a dotted chain;
- relative imports at the top of a file, in a function and in a method.

## The reference

Python itself. Every file of the corpus is loaded by the interpreter: a file of a package by its dotted name with the folder above its top package on the path, any other file as a script with its own folder first, as `python file.py` does; the functions and methods of the file are called, so that the imports inside them run. A hook on `__import__` records the file each import statement of that file loads. No resolver is written for the reference.

Required: the file of the module a statement names, and of a submodule named after `from`. Accepted and not required: a package `__init__.py` Python runs on the way. Wrong: an edge of the graph that is neither. Missing: a required edge the graph lacks.

## Results

| Wingbox | Loads seen by the interpreter | Required edges | Edges in the graph | Wrong | Missing |
|---|---:|---:|---:|---:|---:|
| 0.16.0 | 64 | 51 | 48 | 8 | 11 |
| 0.16.1 | 64 | 51 | 51 | 0 | 0 |
| 0.17.0 | 64 | 51 | 51 | 0 | 0 |

Python 3.14.3. A version with a wrong or a missing edge takes a module file for the package of its name (issue #177, fixed in 0.16.1): the files of the package become dependents of the tool beside it.

## Reproduce

```sh
python3 -I benchmarks/collisions/run.py /tmp/collisions "uvx --from wingbox==0.17.0 wingbox" result.json
```

The exit status is 1 when an edge is wrong or missing. `tests/test_benchmark_cpp_collisions.py` runs the same corpus against the checkout.
