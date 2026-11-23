# Wingbox benchmark: C and C++ include dependencies on public code

Wingbox 0.17.0, run of 2026-11-23. This file is written by `make_report.py` from the files in `results/`; no number is typed by hand.

## Summary

- 60 questions drawn with a fixed seed from 3 public repositories: `wingbox impact <file> 2 --json` names the complete list of dependent files, with nothing outside it, in 60 of 60. A required file is missing in 0 of 60.
- Every include edge of the graph against the reference: 1,345 reference edges in all; 0 wrong, 0 missing.
- The reference itself is checked against a compiler: of the 3,699 files of the repositories that `clang -MM` reads for 435 translation units, 3,699 are reached over the reference edges (0 are not).
- No compilation database was given to Wingbox: the include folders come from the CMake files of each repository and from the tree.

## The question

For a file X: every file that includes X directly or through one other file. It is what a developer or an agent asks before changing a header.

The reference lists come from `tools/cpp_reference.py`, which shares no code with Wingbox: it reads the text of every file without its comments, takes every `#include` line (inside `#if` blocks too: the preprocessor is not run), and looks for a quoted name beside the including file and then in the include roots, for a name in angle brackets in the include roots only. The include roots are those the CMake files of the project pass to the compiler, given by hand in `run.sh`.

`tools/clang_check.py` checks that reference against the compiler: `clang -MM -MG` prints the files the preprocessor reads for each translation unit, and every one of them that is a file of the repository must be reached from the translation unit over the reference edges. The reference may reach more, because it reads every branch of an `#if` and the compiler one. A translation unit the compiler stops on (an `#error` meant for another platform) is counted and left out.

Sampling: per repository, 20 files drawn without replacement (seed 20261123) from the files that at least one other file includes. The reference lists hold 1 to 313 files, median 10.

## Test 1. The impact command

| Repository | Language | Files | Questions | Complete and nothing extra | With a missing file | Rows marked inferred |
|---|---|---:|---:|---:|---:|---:|
| fmt | C++ | 69 | 20 | 20 | 0 | 143 of 240 |
| leveldb | C++ | 132 | 20 | 20 | 0 | 378 of 378 |
| libuv | C | 354 | 20 | 20 | 0 | 637 of 863 |

A row is marked `inferred` when its include was found through the CMake files or by the end of its path, not beside the including file: without a compilation database most includes of a project with one include root are found that way.

## Test 2. Are the edges in the code?

| Repository | Include roots given to the reference | Edges in the reference | Edges in the graph | Wrong | Missing |
|---|---|---:|---:|---:|---:|
| fmt | `include`, `test`, `test/gtest` | 161 | 161 | 0 | 0 |
| leveldb | `.`, `include`, `third_party/googletest/googletest/include`, `third_party/googletest/googlemock/include`, `third_party/benchmark/include` | 488 | 488 | 0 | 0 |
| libuv | `include`, `src` | 696 | 696 | 0 | 0 |

## Test 3. The reference against the compiler

| Repository | Translation units | Stopped by the compiler | Files of the repository the compiler reads | Reached by the reference | Not reached |
|---|---:|---:|---:|---:|---:|
| fmt | 45 | 2 | 235 | 235 | 0 |
| leveldb | 76 | 0 | 913 | 913 | 0 |
| libuv | 316 | 0 | 2,551 | 2,551 | 0 |

Compiler: Apple clang version 16.0.0 (clang-1600.0.26.6).

## Test 4. The graph against Doxygen

`tools/doxygen_check.py` lets Doxygen read each repository with its own preprocessor (`GENERATE_XML`, the same include roots as `INCLUDE_PATH`) and compares the includes it resolved to a file of the repository with the include edges of the Wingbox graph. It is a second parser with rules of its own, not a ground truth: it evaluates `#if` without the definitions of the build, and it takes the one file of the repository with the right name for an include it cannot find.

| Repository | Edges of Doxygen | In the graph too | Only Doxygen | of them written `<name>` | Only the graph |
|---|---:|---:|---:|---:|---:|
| fmt | 158 | 157 | 1 | 0 | 4 |
| leveldb | 487 | 487 | 0 | 0 | 1 |
| libuv | 731 | 672 | 59 | 59 | 24 |

An edge only Doxygen has and that is written `<name>` is a system header for which Doxygen took a file of the repository with that name (`<errno.h>` for `include/uv/errno.h`); Test 3 shows the compiler reads no such file. An edge only the graph has stands in a branch of an `#if` that Doxygen left out.

The edges only Doxygen has that are not of that kind. In these repositories each is a C++20 `import` of the module the second file exports, which is no include: Wingbox does not read module imports.

- fmt: `test/module-test.cc` -> `src/fmt.cc`

Doxygen 1.11.0.

## Scope

- One kind of question: which files include this file, directly or through one other file.
- The preprocessor is not run by Wingbox or by the reference: both read every branch of an `#if`. Test 3 shows that this loses no file the compiler reads on the machine of the run; it does not show which listed files another platform leaves out.
- No compilation database: the run measures the answer a fresh checkout gets. With `compile_commands.json` the include folders are those of the compiler and no row is inferred; that path is covered by `tests/test_cpp_language.py`.
- Tests 1 and 2 are deterministic and need no model. Test 3 needs `clang`, Test 4 `doxygen`.
- The uses of names (`wingbox usages`) are not measured here. Doxygen's cross references are no reference for them: it attributes a call by its name, to a function of another header with the same name or over a function the file defines itself.
- C++20 modules: an `import` of a module is not an include and is not followed (Test 4 names the one case of these repositories).

## Versions and commits

| Item | Version or commit |
|---|---|
| Wingbox | Wingbox 0.17.0 |
| [fmt](https://github.com/fmtlib/fmt) | 0c9fce2 (11.0.2) |
| [leveldb](https://github.com/google/leveldb) | 99b3c03 (1.23) |
| [libuv](https://github.com/libuv/libuv) | e9f29cb (v1.48.0) |

## Reproduce

```sh
benchmarks/cpp/run.sh ~/wingbox-bench-cpp        # needs git, uv, clang and doxygen; fetches the three repositories
```

`WINGBOX_SPEC` names the Wingbox under test (a release, or the path of a checkout).
