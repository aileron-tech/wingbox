"""Third-party check of the Rust reference: file-to-file edges from the front end of the Rust toolchain
(`rust-analyzer scip`: every name of the source with the symbol it resolves to), compared with the pairs
of rust_resolve.py and, with --graph, with the edges of a code graph.

Usage: python3 -I rust_compiler_check.py <repo root> <reference.json> <out.json>
           [--project <dir with Cargo.toml>]...    default: every Cargo.toml with no Cargo.toml above it
           [--scip <project dir>=<index.scip>]...  read these indexes instead of running rust-analyzer
           [--graph <edges.json of wb_edges.py>]   also compare the edges of a graph
           [--keep <dir>]                          keep the indexes there

Needs rustup with a toolchain that has the components rust-analyzer and rust-src (`rustup component add
rust-analyzer rust-src`). Nothing of the analysed repository is executed: rust-analyzer runs with the build
scripts replaced by a command that does nothing and with procedural macros off, so `cargo metadata` (which
reads manifests and downloads the sources of the dependencies) is the only cargo command. Each project is
indexed twice, with all Cargo features and with the default ones, and the two indexes are read together.
Without build scripts and procedural macros, code they generate is not seen; cfg-gated code of other
platforms is not seen either. Edges of such code are reported as not checked, never as wrong.

An edge A -> B is a name written in file A that resolves to a symbol defined in file B. Each occurrence is
classified by how it is written:
  counted by the benchmark question
    mod-decl          `mod b;`
    module            the last module a `use` or a path names (`use crate::b::X`, `b::f()`, `use crate::b;`)
    use-item          an item named in a `use` declaration (followed through `pub use`: the symbol is the definition)
    path-item         an item named after a module in a path (`crate::b::f()`)
    name              a bare name in scope through a `use` or a glob import (`f()`, `Kind`)
    type-member       `Type::member`, `Self::member`, `<Type>::member`: a member of an inherent impl block of the
                      type, found by name among the impl blocks of the type
  accepted, not required
    module-ancestor   a module a path passes through (`crate::a::b::X` names a on the way to b)
  outside the question
    method-or-field   `value.method()`, `value.field`: resolved from the type of the value
    trait-member      `Type::trait_fn`, `Trait::f`, `<T as Tr>::f`: a member a trait or a trait impl gives,
                      selected by the type checker
    assoc-via-type    a variant, a field or an associated item named through a type in any other way
    macro-scope       a macro used by bare name with no `use` of it in the file (textual scope, #[macro_use])
    attribute         a name inside an attribute (`#[derive(Local)]`)
    visibility        the path of `pub(in path)`, `pub(super)`
    module-unresolved a module followed by a name that did not resolve (code of an inactive cfg)
Decision of 2026-11-18 on `use crate::name;` where name is a `#[macro_export]` macro: the path names the crate
root and the macro. The reference requires the file that defines the macro and accepts the crate root file
without requiring it (an exported macro lives at the crate root whatever the root file contains). This check
reports such a name as a `module` edge to the root file, so it is listed under
compiler_counted_missing_in_reference: 18 pairs on claudes-c-compiler (`use crate::emit;` and
`use crate::delegate_to_impl;`, all to src/lib.rs), 0 on agent-browser. No reference list depends on them.

A symbol two crates of one package define under the same name (a helper each integration test declares) has
no single definition; a name that resolves to it in a file that does not define it is counted as ambiguous.
"""
from __future__ import annotations

import bisect
import collections
import json
import os
import re
import subprocess
import sys
import tempfile

COUNTED = ("mod-decl", "module", "use-item", "path-item", "name", "type-member")
ACCEPTED = ("module-ancestor",)
OUTSIDE = ("method-or-field", "trait-member", "assoc-via-type", "macro-scope", "attribute", "visibility", "module-unresolved")


# ---------------------------------------------------------------- protobuf (the wire format, fields of scip.proto)
def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _fields(data: bytes):
    pos, end = 0, len(data)
    while pos < end:
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire == 2:
            size, pos = _varint(data, pos)
            value = data[pos:pos + size]
            pos += size
        elif wire == 1:
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 5:
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, wire, value


def _packed(value: bytes) -> list[int]:
    numbers, pos = [], 0
    while pos < len(value):
        number, pos = _varint(value, pos)
        numbers.append(number)
    return numbers


def read_scip(path: str) -> tuple[dict, list[dict]]:
    """(tool info, documents): a document is {"path", "occurrences": [(line, col, end_col, symbol, roles)]}."""
    data = open(path, "rb").read()
    tool, documents = {}, []
    for number, _wire, value in _fields(data):
        if number == 1:  # Metadata
            for meta_number, _w, meta in _fields(value):
                if meta_number == 2:
                    for tool_number, _w2, text in _fields(meta):
                        if tool_number in (1, 2):
                            tool["name" if tool_number == 1 else "version"] = text.decode("utf-8")
        elif number == 2:  # Document
            document = {"path": "", "occurrences": []}
            for doc_number, _w, item in _fields(value):
                if doc_number == 1:
                    document["path"] = item.decode("utf-8")
                elif doc_number == 2:
                    span: list[int] = []
                    symbol, roles = "", 0
                    for occ_number, occ_wire, part in _fields(item):
                        if occ_number == 1:
                            span = _packed(part) if occ_wire == 2 else [*span, part]
                        elif occ_number == 2:
                            symbol = part.decode("utf-8")
                        elif occ_number == 3:
                            roles = part
                    if symbol and len(span) >= 3 and (len(span) == 3 or span[0] == span[2]):
                        document["occurrences"].append((span[0], span[1], span[-1], symbol, roles))
            documents.append(document)
    return tool, documents


# ---------------------------------------------------------------- symbols
_DESCRIPTOR = re.compile(r"(?:`(?:[^`]|``)*`|[A-Za-z0-9_+\-$]+)?(?:\((?:[^)]*)\)\.|[/#.:!]|\[[^\]]*\]|\([^)]*\))")


def descriptors(symbol: str) -> tuple[tuple[str, ...], list[tuple[str, str]]]:
    """((manager, package, version), [(name, kind)]) of a global symbol; kind is one of / # . : ! () [] (p)."""
    parts = symbol.split(" ", 4)
    if len(parts) < 5 or parts[0] == "local":
        return (), []
    found = []
    for match in _DESCRIPTOR.finditer(parts[4]):
        text = match.group(0)
        if text.endswith(")."):
            found.append((text[:text.rindex("(")].strip("`"), "()"))
        elif text.endswith("]"):
            found.append((text.strip("[]`"), "[]"))
        elif text.endswith(")"):
            found.append((text.strip("()`"), "(p)"))
        else:
            found.append((text[:-1].strip("`"), text[-1]))
    return tuple(parts[1:4]), found


def module_of(symbol: str) -> tuple | None:
    """(package key, namespace path) of the module that holds the definition of a symbol."""
    package, found = descriptors(symbol)
    if not found:
        return None
    path = []
    for name, kind in found[:-1]:
        if kind != "/":
            break
        path.append(name)
    return (package, tuple(path))


def is_module(symbol: str) -> bool:
    _package, found = descriptors(symbol)
    return bool(found) and found[-1][1] == "/"


# ---------------------------------------------------------------- source text
_LITERAL_START = re.compile(rb'//|/\*|(?<![A-Za-z0-9_])[bc]?r#*"|(?<![A-Za-z0-9_])[bc]"|"|\'')
_BLOCK_COMMENT_EDGE = re.compile(rb"/\*|\*/")
_STRING_REST = re.compile(rb'(?:[^"\\]|\\.)*"', re.S)
_CHAR_LITERAL = re.compile(rb"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|.)|[^\\'\n\x80-\xff]|[\xc0-\xff][\x80-\xbf]{1,3})'")
_NOT_NEWLINE = re.compile(rb"[^\n]")


def blank_comments_and_literals(source: bytes) -> bytes:
    """The source with comments, string and char literals replaced by spaces (newlines kept)."""
    out = bytearray(source)
    size, pos = len(source), 0
    while True:
        match = _LITERAL_START.search(source, pos)
        if match is None:
            return bytes(out)
        start, token = match.start(), match.group()
        if token == b"//":
            end = source.find(b"\n", start)
            end = size if end < 0 else end
        elif token == b"/*":
            depth, end = 1, start + 2
            while depth:
                edge = _BLOCK_COMMENT_EDGE.search(source, end)
                if edge is None:
                    end = size
                    break
                depth += 1 if edge.group() == b"/*" else -1
                end = edge.end()
        elif token == b"'":
            literal = _CHAR_LITERAL.match(source, start)
            if literal is None:
                pos = start + 1  # a lifetime
                continue
            end = literal.end()
        elif b"r" in token:
            closing = b'"' + b"#" * token.count(b"#")
            end = source.find(closing, match.end())
            end = size if end < 0 else end + len(closing)
        else:
            rest = _STRING_REST.match(source, match.end())
            end = size if rest is None else rest.end()
        out[start:end] = _NOT_NEWLINE.sub(b" ", source[start:end])
        pos = end


class Source:
    """One file: its text without comments and literals, the `use` declarations and the attributes."""

    def __init__(self, raw: bytes) -> None:
        self.text = blank_comments_and_literals(raw)
        self.line_starts = [0] + [match.end() for match in re.finditer(rb"\n", raw)]
        self.use_spans = [(match.start(), self._statement_end(match.end()))
                          for match in re.finditer(rb"(?<![A-Za-z0-9_])use\s", self.text)
                          if self._starts_item(match.start())]
        self.attribute_spans = [(match.start(), self._bracket_end(match.end() - 1))
                                for match in re.finditer(rb"#!?\s*\[", self.text)]
        self.visibility_spans = [(match.start(), match.end())
                                 for match in re.finditer(rb"(?<![A-Za-z0-9_])pub\s*\(\s*(?:in\s[^)]*|super|self|crate)\s*\)", self.text)]

    def offset(self, line: int, column: int) -> int:
        return self.line_starts[line] + column

    def _starts_item(self, start: int) -> bool:
        before = self.text[max(0, start - 40):start].rstrip()
        return not before or before[-1:] in (b";", b"{", b"}", b"]", b")") or before.endswith(b"pub")

    def _statement_end(self, pos: int) -> int:
        end = self.text.find(b";", pos)
        return len(self.text) if end < 0 else end

    def _bracket_end(self, open_pos: int) -> int:
        depth = 0
        for index in range(open_pos, len(self.text)):
            byte = self.text[index]
            if byte == 91:
                depth += 1
            elif byte == 93:
                depth -= 1
                if depth == 0:
                    return index + 1
        return len(self.text)

    def inside(self, spans: list[tuple[int, int]], pos: int) -> bool:
        """Whether pos lies in one of the spans (sorted by start, not overlapping)."""
        index = bisect.bisect_right(spans, (pos, len(self.text) + 1)) - 1
        return index >= 0 and spans[index][0] <= pos < spans[index][1]

    def back(self, pos: int) -> int:
        """The end of the last token before pos."""
        while pos > 0 and self.text[pos - 1:pos].isspace():
            pos -= 1
        return pos

    def before(self, pos: int) -> bytes:
        end = self.back(pos)
        return self.text[max(0, end - 200):end]

    def skip_space(self, pos: int) -> int:
        while pos < len(self.text) and self.text[pos:pos + 1].isspace():
            pos += 1
        return pos


# ---------------------------------------------------------------- the index of one repository
class Index:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.occurrences: dict[str, list[tuple[int, int, int, str, int]]] = {}
        self.sources: dict[str, Source] = {}
        self.at: dict[str, dict[int, str]] = {}  # file -> offset of a name -> symbol
        self.definition_files: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.module_files: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
        self.tool: dict = {}
        self.ambiguous = 0
        self.modules_by_rule = 0

    def add(self, project: str, scip_path: str) -> None:
        tool, documents = read_scip(scip_path)
        self.tool = tool or self.tool
        for document in documents:
            relative = os.path.normpath(os.path.join(project, document["path"]))
            full = os.path.join(self.repo, relative)
            if relative.startswith("..") or not relative.endswith(".rs") or not os.path.isfile(full):
                continue
            source = self.sources.setdefault(relative, Source(open(full, "rb").read()))
            known = self.occurrences.setdefault(relative, [])
            seen = set(known)  # the same name in a second index of the project (another feature set) counts once
            offsets = self.at.setdefault(relative, {})
            for occurrence in document["occurrences"]:
                line, column, _end, symbol, _roles = occurrence
                if symbol.startswith("local ") or line >= len(source.line_starts) or occurrence in seen:
                    continue
                seen.add(occurrence)
                known.append(occurrence)
                offsets[source.offset(line, column)] = symbol

    def finish(self) -> None:
        declared: list[tuple[str, int, str]] = []
        for file, items in self.occurrences.items():
            source = self.sources[file]
            for line, column, end, symbol, roles in items:
                pos = source.offset(line, column)
                declares_module = is_module(symbol) and re.search(rb"(?<![A-Za-z0-9_])mod$", source.before(pos)) is not None
                if roles & 1 or declares_module:
                    holder = module_of(symbol)
                    if holder is not None:
                        self.module_files[holder][file] += 1
                if roles & 1:
                    self.definition_files[symbol][file] += 1
                if declares_module and not source.text[source.offset(line, end):].lstrip().startswith(b"{"):
                    declared.append((file, pos, symbol))
        # A module whose file defines nothing and declares no module (only `use` lines) leaves no definition in
        # the index: its file is the one the compiler's layout rule gives for the `mod` declaration.
        file_module: dict[str, tuple] = {}
        for (package, path), files in self.module_files.items():
            for file in files:
                if file not in file_module or len(path) < len(file_module[file][1]):
                    file_module[file] = (package, path)
        for file, pos, symbol in declared:
            package, found = descriptors(symbol)
            path = tuple(name for name, _kind in found)
            if (package, path) in self.module_files or file not in file_module:
                continue
            inline = path[len(file_module[file][1]):-1]  # the inline modules around the declaration
            owns = os.path.basename(file) == "mod.rs" or not file_module[file][1]
            folder = os.path.join(os.path.dirname(file) if owns else file[:-3], *inline)
            raw = open(os.path.join(self.repo, file), "rb").read()
            attribute = re.search(rb'#\s*\[\s*path\s*=\s*"([^"]+)"\s*\]\s*(?:pub(?:\s*\([^)]*\))?\s*)?mod\s*$', raw[:pos])
            candidates = ([os.path.join(folder if inline else os.path.dirname(file), attribute.group(1).decode("utf-8"))] if attribute
                          else [os.path.join(folder, path[-1] + ".rs"), os.path.join(folder, path[-1], "mod.rs")])
            for candidate in map(os.path.normpath, candidates):
                if os.path.isfile(os.path.join(self.repo, candidate)):
                    self.module_files[(package, path)][candidate] += 1
                    self.modules_by_rule += 1
                    break

    def _single(self, files: collections.Counter | None, source: str) -> str | None:
        """The one file of a definition as source sees it: source itself when it is among the files; of the
        root files of several targets of one package (they share the symbol `crate`), the one in the nearest
        folder above source, the library first."""
        if not files:
            return None
        if source in files or len(files) == 1:
            return source if source in files else next(iter(files))
        above = [file for file in files if (source + "/").startswith(os.path.dirname(file) + "/") or not os.path.dirname(file)]
        if above:
            depth = max(len(os.path.dirname(file)) for file in above)
            nearest = sorted(file for file in above if len(os.path.dirname(file)) == depth)
            preferred = [file for file in nearest if os.path.basename(file) in ("lib.rs", "main.rs")] or nearest
            if len(nearest) == 1 or len(preferred) <= 2:
                return preferred[0]
        self.ambiguous += 1
        return None

    def definition_file(self, symbol: str, source: str) -> str | None:
        """The file that defines a symbol named in source: its definition occurrence; for a symbol with none
        (an item a macro expands to), the file of the module that holds it."""
        package, found = descriptors(symbol)
        if found and found[-1][1] == "/":
            path = tuple(name for name, _kind in found)
            return self._single(self.module_files.get((package, () if path == ("crate",) else path)), source)
        files = self.definition_files.get(symbol)
        if files:
            return self._single(files, source)
        holder = module_of(symbol)
        return self._single(self.module_files.get(holder) if holder is not None else None, source)

    # ------------------------------------------------------------ classification
    def _continuation(self, file: str, end: int) -> list[str]:
        """What follows a module name in its path: 'end', 'module', 'item', 'glob' or 'self', one per branch."""
        source = self.sources[file]
        text = source.text
        pos = source.skip_space(end)
        if text[pos:pos + 2] != b"::":
            return ["end"]
        pos = source.skip_space(pos + 2)
        if text[pos:pos + 1] == b"*":
            return ["glob"]
        if text[pos:pos + 1] != b"{":
            return [self._kind_at(file, pos)]
        kinds, depth, member = [], 0, True
        while pos < len(text):
            byte = text[pos:pos + 1]
            if byte == b"{":
                depth += 1
                member = depth == 1
            elif byte == b"}":
                depth -= 1
                if depth == 0:
                    break
            elif byte == b"," and depth == 1:
                member = True
            elif member and not byte.isspace():
                kinds.append("glob" if byte == b"*" else self._kind_at(file, pos))
                member = False
            pos += 1
        return kinds or ["end"]

    def _kind_at(self, file: str, pos: int) -> str:
        text = self.sources[file].text
        if re.match(rb"self(?![A-Za-z0-9_])", text[pos:pos + 5]):
            return "self"
        symbol = self.at[file].get(pos)
        if symbol is None and text[pos:pos + 2] == b"r#":
            symbol = self.at[file].get(pos + 2)
        if symbol is None:
            return "unresolved"  # a name of another crate, or of code the compiler did not load (inactive cfg)
        return "module" if is_module(symbol) else "item"

    def _previous_segment(self, file: str, pos: int) -> str | None:
        """Kind of the path segment before `::` at pos: 'module', 'item', or None when there is no `::`."""
        source = self.sources[file]
        end = source.back(pos)
        if source.text[end - 2:end] != b"::":
            return None
        head_end = source.back(end - 2)
        match = re.search(rb"(?:r#)?([A-Za-z_][A-Za-z0-9_]*)$", source.text[max(0, head_end - 200):head_end])
        if match is None:
            return "item"  # `>::` of a qualified path or of generic arguments
        name = match.group(1)
        if name in (b"crate", b"self", b"super"):
            return "module"
        symbol = self.at[file].get(head_end - len(name))
        if symbol is None:
            return "item"
        return "module" if is_module(symbol) else "item"

    def classify(self, file: str, line: int, column: int, end: int, symbol: str, imported: set[str]) -> str:
        source = self.sources[file]
        pos = source.offset(line, column)
        before = source.before(pos)
        if source.inside(source.visibility_spans, pos):
            return "visibility"
        if source.inside(source.attribute_spans, pos):
            return "attribute"
        if is_module(symbol):
            if re.search(rb"(?<![A-Za-z0-9_])mod$", before):
                return "mod-decl"
            kinds = self._continuation(file, source.offset(line, end))
            if all(kind == "unresolved" for kind in kinds):
                return "module-unresolved"
            return "module" if any(kind not in ("module", "unresolved") for kind in kinds) else "module-ancestor"
        if before.endswith(b".") and not before.endswith(b".."):
            return "method-or-field"
        previous = self._previous_segment(file, pos)
        in_use = source.inside(source.use_spans, pos)
        _package, found = descriptors(symbol)
        impl = next((index for index, (name, kind) in enumerate(found) if name == "impl" and kind == "#"), None)
        if previous == "item":
            if impl is not None and len(found) == impl + 3:
                return "type-member"  # impl#[Type]member
            if impl is not None or (len(found) >= 2 and found[-2][1] == "#" and found[-1][1] == "()"):
                return "trait-member"  # impl#[Type][Trait]member, or Trait#member
            return "assoc-via-type"
        if in_use:
            return "use-item"
        if previous == "module":
            return "path-item"
        if found and found[-1][1] == "!" and symbol not in imported:
            return "macro-scope"
        if len(found) >= 2 and found[-2][1] != "/":
            return "assoc-via-type"  # a field in a literal or a pattern, a variant or an associated item by bare name
        return "name"

    def edges(self) -> dict[tuple[str, str], dict]:
        result: dict[tuple[str, str], dict] = {}
        for file, items in sorted(self.occurrences.items()):
            source = self.sources[file]
            imported = {symbol for line, column, _end, symbol, _roles in items
                        if source.inside(source.use_spans, source.offset(line, column))}
            for line, column, end, symbol, roles in sorted(items):
                declares = is_module(symbol) and re.search(rb"(?<![A-Za-z0-9_])mod$", source.before(source.offset(line, column)))
                if roles & 1 and not declares:
                    continue
                target = self.definition_file(symbol, file)
                if target is None or target == file:
                    continue
                kind = self.classify(file, line, column, end, symbol, imported)
                entry = result.setdefault((file, target), {"kinds": collections.Counter(), "examples": {}})
                entry["kinds"][kind] += 1
                entry["examples"].setdefault(kind, {"line": line + 1, "column": column + 1, "symbol": symbol})
        return result


# ---------------------------------------------------------------- running rust-analyzer
def top_manifests(repo: str) -> list[str]:
    tracked = subprocess.check_output(["git", "-C", repo, "ls-files"], text=True).split("\n")
    folders = sorted(os.path.dirname(path) for path in tracked if os.path.basename(path) == "Cargo.toml")
    return [folder for folder in folders
            if not any(folder != other and (other == "" or folder.startswith(other + "/")) for other in folders)]


def run_rust_analyzer(repo: str, project: str, out: str, all_features: bool) -> None:
    cargo: dict = {"buildScripts": {"enable": False, "overrideCommand": [sys.executable, "-c", "pass"]}}
    if all_features:
        cargo["features"] = "all"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"cargo": cargo, "procMacro": {"enable": False}}, handle)
    try:
        subprocess.run(["rust-analyzer", "scip", ".", "--output", out, "--config-path", handle.name],
                       cwd=os.path.join(repo, project), check=True)
    finally:
        os.unlink(handle.name)


def label(kinds: collections.Counter) -> str:
    for group, names in (("counted", COUNTED), ("accepted", ACCEPTED), ("outside", OUTSIDE)):
        if any(kinds.get(name) for name in names):
            return group
    return "outside"


def compare(index: Index, reference: dict, graph: set[tuple[str, str]] | None) -> dict:
    """The edges of the compiler against the required pairs of the reference and, when given, the edges of a graph."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
    from impact100 import ancestor_module_edges, rust_edges  # noqa: E402

    compiler = index.edges()
    required = rust_edges(reference)
    accepted = required | ancestor_module_edges(reference)
    seen_files = set(index.occurrences)
    lines_with_names = {file: {line + 1 for line, *_rest in items} for file, items in index.occurrences.items()}
    references_of: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for ref in reference["refs"]:
        for target in {ref["to"], ref["to_followed"]}:
            if target != ref["frm"]:
                references_of[(ref["frm"], target)].append(ref)

    def checked(pair: tuple[str, str]) -> bool:
        """Whether the compiler read the code that gives the pair: both files, and a line of the reference."""
        return pair[0] in seen_files and pair[1] in seen_files and any(
            ref["line"] in lines_with_names[pair[0]] for ref in references_of[pair])

    counted = {pair for pair, entry in compiler.items() if label(entry["kinds"]) == "counted"}
    only_accepted = {pair for pair, entry in compiler.items() if label(entry["kinds"]) == "accepted"}
    outside = {pair: entry for pair, entry in compiler.items() if label(entry["kinds"]) == "outside"}
    unconfirmed = sorted(required - set(compiler))

    def show(pair: tuple[str, str]) -> dict:
        entry = compiler[pair]
        return {"from": pair[0], "to": pair[1], "kinds": dict(entry["kinds"]), "examples": entry["examples"]}

    def refs(pair: tuple[str, str]) -> dict:
        return {"from": pair[0], "to": pair[1],
                "references": [{"line": ref["line"], "category": ref["category"], "text": ref["text"]} for ref in references_of[pair][:5]]}

    outside_kinds: collections.Counter = collections.Counter()
    occurrences: collections.Counter = collections.Counter()
    for entry in compiler.values():
        occurrences.update(entry["kinds"])
    for entry in outside.values():
        outside_kinds[max(entry["kinds"], key=lambda kind: entry["kinds"][kind])] += 1
    result = {
        "tool": index.tool,
        "method": "rust-analyzer scip, build scripts replaced by a no-op command, procedural macros off, "
                  "all features and default features",
        "files_read_by_the_compiler": len(seen_files), "rs_files": len(reference["rs_files"]),
        "files_not_read": sorted(set(reference["rs_files"]) - seen_files),
        "compiler_edges": len(compiler), "compiler_counted": len(counted), "compiler_accepted_only": len(only_accepted),
        "compiler_outside": len(outside), "compiler_outside_by_kind": dict(outside_kinds),
        "names_between_files_by_kind": dict(occurrences),
        "names_with_an_ambiguous_definition": index.ambiguous,
        "module_files_found_by_the_layout_rule": index.modules_by_rule,
        "reference_required": len(required), "reference_confirmed": len(required & set(compiler)),
        "reference_not_confirmed": [refs(pair) for pair in unconfirmed if checked(pair)],
        "reference_not_checked": [refs(pair) for pair in unconfirmed if not checked(pair)],
        "compiler_counted_missing_in_reference": [show(pair) for pair in sorted(counted - required)],
        "compiler_outside_edges": [show(pair) for pair in sorted(outside)],
    }
    if graph is not None:
        extra = graph - set(compiler) - accepted
        result.update({
            "graph_edges": len(graph),
            "compiler_counted_missing_in_graph": [show(pair) for pair in sorted(counted - graph)],
            "graph_not_confirmed": sorted(pair for pair in extra if pair[0] in seen_files and pair[1] in seen_files),
            "graph_not_checked": sorted(pair for pair in extra if not (pair[0] in seen_files and pair[1] in seen_files)),
        })
    return result


def main() -> None:
    arguments = sys.argv[1:]
    options: dict[str, list[str]] = collections.defaultdict(list)
    positional = []
    while arguments:
        argument = arguments.pop(0)
        if argument.startswith("--"):
            options[argument[2:]].append(arguments.pop(0))
        else:
            positional.append(argument)
    repo, reference_path, out_path = positional
    repo = os.path.abspath(repo)
    index = Index(repo)
    keep = options["keep"][0] if options["keep"] else None
    given = [(value.partition("=")[0], value.partition("=")[2]) for value in options["scip"]]
    projects = sorted({project for project, _path in given}) or options["project"] or top_manifests(repo)
    with tempfile.TemporaryDirectory() as scratch:
        for project, scip_path in given:
            index.add("" if project == "." else project, scip_path)
        for project in projects if not given else ():
            for all_features in (True, False):
                name = f"{project.replace('/', '_') or 'root'}.{'all' if all_features else 'default'}.scip"
                scip_path = os.path.join(os.path.abspath(keep) if keep else scratch, name)
                os.makedirs(os.path.dirname(scip_path), exist_ok=True)
                run_rust_analyzer(repo, project, scip_path, all_features)
                index.add("" if project == "." else project, scip_path)
    index.finish()
    graph = None
    if options["graph"]:
        graph = {tuple(pair) for pair in json.load(open(options["graph"][0]))["edges"].get("imports", [])
                 if all(path.endswith(".rs") for path in pair)}
    result = {"projects": projects, **compare(index, json.load(open(reference_path)), graph)}
    json.dump(result, open(out_path, "w"), indent=1)
    print({key: (value if isinstance(value, (int, str)) else len(value)) for key, value in result.items()
           if key not in ("tool", "projects", "method", "compiler_outside_by_kind", "names_between_files_by_kind")})
    print("outside by kind:", result["compiler_outside_by_kind"])
    for row in result["reference_not_confirmed"][:20]:
        print("reference only:", row["from"], "->", row["to"], row["references"][:1])
    for row in result["compiler_counted_missing_in_reference"][:20]:
        print("compiler only:", row["from"], "->", row["to"], row["kinds"])


if __name__ == "__main__":
    main()
