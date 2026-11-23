"""Write benchmarks/cpp/README.md from the files in results/. No number is typed by hand.

Usage: python3 -I make_report.py <results dir> <README.md>
"""
from __future__ import annotations

import json
import os
import sys

REPOSITORIES = (
    ("fmt", "fmt", "https://github.com/fmtlib/fmt", "C++"),
    ("leveldb", "leveldb", "https://github.com/google/leveldb", "C++"),
    ("libuv", "libuv", "https://github.com/libuv/libuv", "C"),
)


def main() -> None:
    results, out_path = sys.argv[1], sys.argv[2]
    meta = json.load(open(os.path.join(results, "run.json"), encoding="utf-8"))
    rows = []
    doxygen: dict[str, dict] = {}
    for key, name, url, language in REPOSITORIES:
        questions = json.load(open(os.path.join(results, f"{key}.questions.json"), encoding="utf-8"))
        clang = json.load(open(os.path.join(results, f"{key}.clang.json"), encoding="utf-8"))
        doxygen[key] = json.load(open(os.path.join(results, f"{key}.doxygen.json"), encoding="utf-8"))
        rows.append((key, name, url, language, questions, clang))
    asked = sum(len(q["questions"]) for *_rest, q, _c in rows)
    complete = sum(q["complete_and_nothing_extra"] for *_rest, q, _c in rows)
    with_missing = sum(q["with_a_missing_file"] for *_rest, q, _c in rows)
    edges = sum(q["edges"]["reference"] for *_rest, q, _c in rows)
    wrong = sum(q["edges"]["wrong_count"] for *_rest, q, _c in rows)
    missing = sum(q["edges"]["missing_count"] for *_rest, q, _c in rows)
    units = sum(c["translation_units"] for *_rest, c in rows)
    stopped = sum(c["stopped"] for *_rest, c in rows)
    read = sum(c["files_read_by_the_compiler"] for *_rest, c in rows)
    not_reached = sum(c["not_reached"] for *_rest, c in rows)
    sizes = sorted(row["gold"] for *_rest, q, _c in rows for row in q["questions"])
    lines = [
        "# Wingbox benchmark: C and C++ include dependencies on public code",
        "",
        f"{meta['wingbox']}, run of {meta['date']}. This file is written by `make_report.py` from the files in `results/`; "
        "no number is typed by hand.",
        "",
        "## Summary",
        "",
        f"- {asked} questions drawn with a fixed seed from {len(rows)} public repositories: `wingbox impact <file> 2 --json` names the "
        f"complete list of dependent files, with nothing outside it, in {complete} of {asked}. A required file is missing in {with_missing} of {asked}.",
        f"- Every include edge of the graph against the reference: {edges:,} reference edges in all; {wrong} wrong, {missing} missing.",
        f"- The reference itself is checked against a compiler: of the {read:,} files of the repositories that `clang -MM` reads for "
        f"{units - stopped} translation units, {read - not_reached:,} are reached over the reference edges ({not_reached} are not).",
        "- No compilation database was given to Wingbox: the include folders come from the CMake files of each repository and from the tree.",
        "",
        "## The question",
        "",
        "For a file X: every file that includes X directly or through one other file. It is what a developer or an agent asks before "
        "changing a header.",
        "",
        "The reference lists come from `tools/cpp_reference.py`, which shares no code with Wingbox: it reads the text of every file "
        "without its comments, takes every `#include` line (inside `#if` blocks too: the preprocessor is not run), and looks for a "
        "quoted name beside the including file and then in the include roots, for a name in angle brackets in the include roots only. "
        "The include roots are those the CMake files of the project pass to the compiler, given by hand in `run.sh`.",
        "",
        "`tools/clang_check.py` checks that reference against the compiler: `clang -MM -MG` prints the files the preprocessor reads for "
        "each translation unit, and every one of them that is a file of the repository must be reached from the translation unit over "
        "the reference edges. The reference may reach more, because it reads every branch of an `#if` and the compiler one. A "
        "translation unit the compiler stops on (an `#error` meant for another platform) is counted and left out.",
        "",
        f"Sampling: per repository, {len(rows[0][4]['questions'])} files drawn without replacement (seed 20261123) from the files that at "
        f"least one other file includes. The reference lists hold {sizes[0]} to {sizes[-1]} files, median {sizes[len(sizes) // 2]}.",
        "",
        "## Test 1. The impact command",
        "",
        "| Repository | Language | Files | Questions | Complete and nothing extra | With a missing file | Rows marked inferred |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _key, name, _url, language, q, _c in rows:
        inferred = sum(row["inferred_rows"] for row in q["questions"])
        named = sum(row["named"] for row in q["questions"])
        lines.append(f"| {name} | {language} | {q['files']} | {len(q['questions'])} | {q['complete_and_nothing_extra']} | "
                     f"{q['with_a_missing_file']} | {inferred:,} of {named:,} |")
    lines += [
        "",
        "A row is marked `inferred` when its include was found through the CMake files or by the end of its path, not beside the "
        "including file: without a compilation database most includes of a project with one include root are found that way.",
        "",
        "## Test 2. Are the edges in the code?",
        "",
        "| Repository | Include roots given to the reference | Edges in the reference | Edges in the graph | Wrong | Missing |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for _key, name, _url, _language, q, _c in rows:
        roots = ", ".join(f"`{root or '.'}`" for root in q["include_roots"])
        lines.append(f"| {name} | {roots} | {q['edges']['reference']:,} | {q['edges']['graph']:,} | {q['edges']['wrong_count']} | {q['edges']['missing_count']} |")
    lines += [
        "",
        "## Test 3. The reference against the compiler",
        "",
        "| Repository | Translation units | Stopped by the compiler | Files of the repository the compiler reads | Reached by the reference | Not reached |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _key, name, _url, _language, _q, c in rows:
        lines.append(f"| {name} | {c['translation_units']} | {c['stopped']} | {c['files_read_by_the_compiler']:,} | {c['reached_by_the_reference']:,} | {c['not_reached']} |")
    lines += [
        "",
        f"Compiler: {rows[0][5]['compiler']}.",
        "",
        "## Test 4. The graph against Doxygen",
        "",
        "`tools/doxygen_check.py` lets Doxygen read each repository with its own preprocessor (`GENERATE_XML`, the same include "
        "roots as `INCLUDE_PATH`) and compares the includes it resolved to a file of the repository with the include edges of the "
        "Wingbox graph. It is a second parser with rules of its own, not a ground truth: it evaluates `#if` without the definitions "
        "of the build, and it takes the one file of the repository with the right name for an include it cannot find.",
        "",
        "| Repository | Edges of Doxygen | In the graph too | Only Doxygen | of them written `<name>` | Only the graph |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, name, _url, _language, _q, _c in rows:
        d = doxygen[key]
        lines.append(f"| {name} | {d['edges_of_doxygen']:,} | {d['in_both']:,} | {d['only_doxygen_count']} | "
                     f"{d['only_doxygen_by_form']['angle']} | {d['only_the_list_count']} |")
    other = [(name, edge) for key, name, *_rest in rows for edge in doxygen[key]["only_doxygen_not_written_in_angle_brackets"]]
    lines += [
        "",
        "An edge only Doxygen has and that is written `<name>` is a system header for which Doxygen took a file of the repository "
        "with that name (`<errno.h>` for `include/uv/errno.h`); Test 3 shows the compiler reads no such file. An edge only the graph "
        "has stands in a branch of an `#if` that Doxygen left out.",
    ]
    if other:
        lines += [
            "",
            "The edges only Doxygen has that are not of that kind. In these repositories each is a C++20 `import` of the module the "
            "second file exports, which is no include: Wingbox does not read module imports.",
            "",
        ] + [f"- {name}: `{edge[0]}` -> `{edge[1]}`"
             for name, edge in other]
    lines += [
        "",
        f"Doxygen {next(iter(doxygen.values()))['doxygen']}.",
        "",
        "## Scope",
        "",
        "- One kind of question: which files include this file, directly or through one other file.",
        "- The preprocessor is not run by Wingbox or by the reference: both read every branch of an `#if`. Test 3 shows that this "
        "loses no file the compiler reads on the machine of the run; it does not show which listed files another platform leaves out.",
        "- No compilation database: the run measures the answer a fresh checkout gets. With `compile_commands.json` the include "
        "folders are those of the compiler and no row is inferred; that path is covered by `tests/test_cpp_language.py`.",
        "- Tests 1 and 2 are deterministic and need no model. Test 3 needs `clang`, Test 4 `doxygen`.",
        "- The uses of names (`wingbox usages`) are not measured here. Doxygen's cross references are no reference for them: it "
        "attributes a call by its name, to a function of another header with the same name or over a function the file defines itself.",
        "- C++20 modules: an `import` of a module is not an include and is not followed (Test 4 names the one case of these repositories).",
        "",
        "## Versions and commits",
        "",
        "| Item | Version or commit |",
        "|---|---|",
        f"| Wingbox | {meta['wingbox']} |",
    ]
    for key, name, url, _language, _q, _c in rows:
        lines.append(f"| [{name}]({url}) | {meta['commits'][key][:7]} ({meta['tags'][key]}) |")
    lines += [
        "",
        "## Reproduce",
        "",
        "```sh",
        "benchmarks/cpp/run.sh ~/wingbox-bench-cpp        # needs git, uv, clang and doxygen; fetches the three repositories",
        "```",
        "",
        "`WINGBOX_SPEC` names the Wingbox under test (a release, or the path of a checkout).",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


if __name__ == "__main__":
    main()
