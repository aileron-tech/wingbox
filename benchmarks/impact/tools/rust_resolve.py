"""Independent Rust module-dependency resolver (no tree-sitter, no Wingbox code).

Usage: python3 -I rust_resolve.py <repo_root> <out_json>

Reads only .rs and Cargo.toml files. Builds the module tree from crate roots
(Cargo targets), then extracts references from `mod` declarations, `use`
trees and inline paths, and resolves each reference to the first-party file
that defines the module reached by the path.

Crates and files follow Cargo and the compiler: the library of a package is named by
`[lib] name` (else the package name), a dependency declared with `package = ".."` by
its key, `extern crate a as b;` binds b; binaries are `src/main.rs`, `src/bin/*.rs`
and `src/bin/<name>/main.rs`; in a package of edition 2015 a `use` path and `::a`
start at the crate root; `#[path]` and every `#[cfg_attr(.., path = "..")]` name the
file of a module, `#[path = "dir"]` on an inline module the folder of its children.

Output JSON: {"files": [...], "crates": {...}, "refs": [ {from, line, to,
to_followed, category, text} ], "modules": {modpath: file}}
  to          = file of the module the path reaches (module semantics)
  to_followed = file that defines the item, following `pub use` re-exports
                (only public items and `pub use` lines once a re-export is followed)
  category "glob-use": a `use m::*` (or `use super::*`) whose module body names an
                item that m provides; to = m, to_followed = the file that defines it.
                Found by scanning the identifiers of the module text, not by a parser.
  category "type-member": a member named through a type path (`Type::member`, `Self::member`,
                `<Type>::member`, `Type::<T>::member`) that an inherent impl block of the type
                defines in another file than the one that names it; to = the file of the type,
                to_followed = the file of the impl block (often the same: a type named through
                an alias of a third module is reached only this way). The compiler finds such a member by
                name among all inherent impl blocks of the type (rustc_hir_typeck, method/probe.rs:
                assemble_inherent_impl_candidates_for_type). A member a trait impl gives and a
                method called on a value (`value.method()`) are not counted: they need the type checker.
"""
import json
import os
import re
import subprocess
import sys

KEYWORDS_NOT_NAMES = {"fn", "unsafe", "extern", "async", "const", "mut", "pub", "in"}
EXTERNAL_ROOTS = {"std", "core", "alloc", "proc_macro", "test"}


# ---------------------------------------------------------------- stripping
def strip_source(src):
    """Replace comments, string and char literals by spaces; keep newlines."""
    out = list(src)
    n = len(src)
    i = 0

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            depth = 1
            j = i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
            continue
        # raw strings r"..", r#".."#, br"..", cr".."
        m = re.compile(r'(?:b|c)?r(#*)"').match(src, i)
        if m and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            hashes = m.group(1)
            end = src.find('"' + hashes, m.end())
            end = n if end < 0 else end + 1 + len(hashes)
            blank(i, end)
            i = end
            continue
        if c == '"' or (c in "bc" and i + 1 < n and src[i + 1] == '"' and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_"))):
            j = i + 1 if c == '"' else i + 2
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
            continue
        if c == "'":
            # char literal vs lifetime
            if i + 1 < n and src[i + 1] == "\\":
                j = src.find("'", i + 2)
                if src.startswith("'\\''", i):
                    j = i + 3
                j = n if j < 0 else j + 1
                blank(i, j)
                i = j
                continue
            if i + 2 < n and src[i + 2] == "'":
                blank(i, i + 3)
                i += 3
                continue
            i += 1
            continue
        i += 1
    return "".join(out)


def line_of(text, pos, _cache={}):
    key = id(text)
    starts = _cache.get(key)
    if starts is None or starts[0] is not text:
        idx = [0]
        for m in re.finditer("\n", text):
            idx.append(m.end())
        starts = (text, idx)
        _cache[key] = starts
    import bisect

    return bisect.bisect_right(starts[1], pos)


def match_brace(text, open_pos):
    depth = 0
    for j in range(open_pos, len(text)):
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(text) - 1


# ---------------------------------------------------------------- cargo
def parse_cargo_targets(repo, rel_dir):
    """Return list of (kind, crate_name, root_rel_path) for the package at rel_dir."""
    path = os.path.join(repo, rel_dir, "Cargo.toml")
    if not os.path.exists(path):
        return []
    txt = open(path, encoding="utf-8").read()
    sections = re.split(r"^\s*\[", txt, flags=re.M)
    pkg_name = None
    autobins = True
    lib_path = None
    lib_name = None
    bins = []
    edition = None
    renames = {}  # name in code -> package name, of dependencies declared with `package = ".."`
    for sec in sections:
        head = sec.split("]", 1)[0].strip()
        body = sec.split("]", 1)[1] if "]" in sec else ""
        kv = dict(re.findall(r'^\s*([\w-]+)\s*=\s*"?([^"\n]*)"?', body, flags=re.M))
        if head == "package":
            pkg_name = kv.get("name")
            if kv.get("autobins", "true").strip() == "false":
                autobins = False
            edition = kv.get("edition", "").strip() or None
            if re.search(r"^\s*edition(?:\.workspace\s*=\s*true|\s*=\s*\{\s*workspace\s*=\s*true)", body, flags=re.M):
                edition = workspace_edition(repo, rel_dir)
        elif head.split(".")[-1] in ("dependencies", "dev-dependencies", "build-dependencies"):
            for name, package in re.findall(r'^\s*([\w-]+)\s*=\s*\{[^}\n]*\bpackage\s*=\s*"([^"]+)"', body, flags=re.M):
                renames[name.replace("-", "_")] = package
        elif "dependencies." in head and kv.get("package"):
            renames[head.split(".")[-1].replace("-", "_")] = kv["package"]
        elif head == "lib":
            lib_path = kv.get("path")
            lib_name = kv.get("name")
        elif head == "[bin":
            bins.append((kv.get("name"), kv.get("path")))
    targets = []
    j = lambda p: os.path.normpath(os.path.join(rel_dir, p)) if rel_dir else os.path.normpath(p)
    lib_rel = j(lib_path or "src/lib.rs")
    crate_lib_name = (lib_name or pkg_name or "").replace("-", "_")
    if os.path.exists(os.path.join(repo, lib_rel)):
        targets.append(("lib", crate_lib_name, lib_rel))
    seen = set()
    for name, p in bins:
        if p:
            targets.append(("bin", (name or "").replace("-", "_"), j(p)))
            seen.add(j(p))
    if autobins or not bins:
        bin_dir = os.path.join(repo, rel_dir, "src/bin")
        names = sorted(os.listdir(bin_dir)) if os.path.isdir(bin_dir) else []
        found = [(pkg_name or "main", j("src/main.rs"))] + [(f[:-3], j("src/bin/" + f)) for f in names if f.endswith(".rs")]
        # src/bin/<name>/main.rs is the binary <name> (Cargo: infer_subdirectory)
        found += [(f, j("src/bin/" + f + "/main.rs")) for f in names if os.path.isfile(os.path.join(bin_dir, f, "main.rs"))]
        for name, cand in found:
            if os.path.exists(os.path.join(repo, cand)) and cand not in seen:
                targets.append(("bin", "main" if cand == j("src/main.rs") else name, cand))
    for kind, d in (("test", "tests"), ("example", "examples"), ("bench", "benches")):
        full = os.path.join(repo, rel_dir, d)
        if os.path.isdir(full):
            for f in sorted(os.listdir(full)):
                if f.endswith(".rs"):
                    targets.append((kind, f[:-3], j(d + "/" + f)))
                elif os.path.isfile(os.path.join(full, f, "main.rs")):
                    targets.append((kind, f, j(d + "/" + f + "/main.rs")))
    if os.path.exists(os.path.join(repo, rel_dir, "build.rs")):
        targets.append(("build", "build_script", j("build.rs")))
    meta = dict(pkg=pkg_name, edition2015=edition in (None, "2015"), renames=renames)
    return [(k, n, p, meta, crate_lib_name) for k, n, p in targets]


def workspace_edition(repo, rel_dir):
    """`[workspace.package] edition` of the nearest Cargo.toml at or above rel_dir that has a workspace table."""
    cur = rel_dir
    while True:
        path = os.path.join(repo, cur, "Cargo.toml")
        txt = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        if re.search(r"^\s*\[workspace[\].]", txt, flags=re.M):
            sec = re.search(r"^\s*\[workspace\.package\]([^\[]*)", txt, flags=re.M)
            found = re.search(r'^\s*edition\s*=\s*"?(\d+)', sec.group(1), flags=re.M) if sec else None
            return found.group(1) if found else None
        if not cur:
            return None
        cur = os.path.dirname(cur)


def find_packages(repo, files):
    """Packages: root Cargo.toml (workspace members) plus every Cargo.toml dir."""
    dirs = set()
    for f in files:
        if os.path.basename(f) == "Cargo.toml":
            dirs.add(os.path.dirname(f))
    return sorted(dirs)


# ---------------------------------------------------------------- use trees
TOK = re.compile(r"::|[{},*]|r#\w+|\w+")


def parse_use_tree(s):
    """Return list of (segments, alias, glob)."""
    toks = TOK.findall(s)
    pos = [0]
    leading_abs = False
    if toks and toks[0] == "::":
        leading_abs = True
        pos[0] = 1

    def parse(prefix):
        res = []
        segs = list(prefix)
        while pos[0] < len(toks):
            t = toks[pos[0]]
            if t == "{":
                pos[0] += 1
                while pos[0] < len(toks) and toks[pos[0]] != "}":
                    res.extend(parse(segs))
                    if pos[0] < len(toks) and toks[pos[0]] == ",":
                        pos[0] += 1
                pos[0] += 1
                return res
            if t == "*":
                pos[0] += 1
                res.append((segs, None, True))
                return res
            if t == "::":
                pos[0] += 1
                continue
            if t in (",", "}"):
                break
            name = t[2:] if t.startswith("r#") else t
            pos[0] += 1
            if name == "as":
                continue
            if pos[0] < len(toks) and toks[pos[0]] == "as":
                alias = toks[pos[0] + 1] if pos[0] + 1 < len(toks) else None
                pos[0] += 2
                if name == "self":
                    res.append((segs, alias, False))
                else:
                    res.append((segs + [name], alias, False))
                return res
            if pos[0] < len(toks) and toks[pos[0]] == "::":
                segs = segs + [name]
                continue
            if name == "self" and segs:
                res.append((segs, segs[-1], False))
            else:
                res.append((segs + [name], name, False))
            return res
        return res

    out = parse([])
    return [(segs, alias, glob, leading_abs) for segs, alias, glob in out]


# ---------------------------------------------------------------- model
class Mod:
    def __init__(self, path, file, crate, inline, body_start, body_end, body_depth, dir_for_children):
        self.path = path  # tuple
        self.file = file
        self.crate = crate
        self.inline = inline
        self.body = (body_start, body_end)
        self.body_depth = body_depth
        self.child_dir = dir_for_children
        self.children = {}
        self.items = set()
        self.private = set()  # items declared without `pub`: no re-export of another module carries them
        self.uses = []  # (segs, alias, glob, abs, pub, line)
        self.parent = None
        self.type_aliases = {}  # name -> segments of the type `type Name = a::Type;` stands for
        self.variants = {}  # enum name -> names of its variants
        self.restricted = {}  # item -> path of the module it is visible in (`pub(super)`, `pub(in path)`)


MOD_RE = re.compile(r"(?<![\w:])((?:pub\s*(?:\([^)]*\))?\s+)?)mod\s+(?:r#)?(\w+)\s*([;{])")
ITEM_RE = re.compile(r"(?<![\w:])(fn|struct|enum|trait|type|const|static|union)\s+(?:r#)?(\w+)")
PUB_BEFORE_RE = re.compile(r"(?<![\w])pub\s*(?:\(([^)]*)\))?\s*(?:(?:unsafe|const|async|default|extern)\s+)*$")
IDENT_RE = re.compile(r"(?<![\w.])(?<!::)(?:r#)?([A-Za-z_]\w*)\b")
RUST_KEYWORDS = frozenset("as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod "
                          "move mut pub ref return self Self static struct super trait true type union unsafe use where while".split())
MACRO_RE = re.compile(r"macro_rules!\s*(\w+)")
USE_RE = re.compile(r"(?<![\w:])((?:pub\s*(?:\([^)]*\))?\s+)?)use\s+")
IMPL_RE = re.compile(r"(?<![\w:])impl(?![\w!])")
PATH_TEXT = r"(?:r#)?\w+(?:\s*::\s*(?:r#)?\w+)*"
TYPE_ALIAS_RE = re.compile(r"(?<![\w:])type\s+(?:r#)?(\w+)\s*(?:<[^=;]*>)?\s*=\s*(" + PATH_TEXT + r")")
MEMBER_RE = re.compile(r"(?<![\w:])(?:fn|const|type)\s+(?:r#)?(\w+)")
TURBOFISH_MEMBER_RE = re.compile(r"(?<![\w:])(" + PATH_TEXT + r")\s*::\s*<")
QUALIFIED_MEMBER_RE = re.compile(r"(?<![\w>)\]])<\s*(" + PATH_TEXT + r")\s*(?:<[^<>;{}]*>)?\s*>\s*::\s*((?:r#)?\w+)")


def path_segments(text):
    return [x[2:] if x.startswith("r#") else x for x in re.findall(r"(?:r#)?\w+", text)]


def skip_angles(text, pos):
    """Position after the `<...>` that starts at pos (pos itself when there is none)."""
    if text[pos:pos + 1] != "<":
        return pos
    depth = 0
    for j in range(pos, len(text)):
        ch = text[j]
        if ch == "<":
            depth += 1
        elif ch == ">" and text[j - 1] != "-":
            depth -= 1
            if depth == 0:
                return j + 1
        elif ch in ";{":
            break
    return pos


class Repo:
    def __init__(self, root):
        self.root = root
        out = subprocess.check_output(["git", "-C", root, "ls-files"], text=True).split("\n")
        self.all_files = [f for f in out if f]
        self.rs_files = sorted(f for f in self.all_files if f.endswith(".rs"))
        self.raw = {}
        self.text = {}
        self.depth = {}
        self.mods = {}  # (crate, path tuple) -> Mod
        self.file_mods = {}  # file -> list of Mod (top-level first)
        self.crates = {}  # crate id -> dict(kind,name,root,lib_name)
        self.macro_export = {}  # (lib crate id, name) -> file
        self.unresolved_mods = []
        self.impls = []  # dict(mod, file, type, trait, members, span): the impl blocks of every module
        self.type_sites = {}  # (crate, module path, written path) -> (Mod, name) of the type, or None
        self.extern_crates = {}  # (crate id, name) -> crate id the name stands for, or None
        self.alternates = []  # (parent Mod, child Mod): the other files of a `#[cfg_attr(.., path = "..")]` module

    def load(self, f):
        if f not in self.text:
            p = os.path.join(self.root, f)
            src = open(p, encoding="utf-8", errors="replace").read()
            self.raw[f] = src
            t = strip_source(src)
            self.text[f] = t
            d = []
            cur = 0
            for ch in t:
                if ch == "{":
                    cur += 1
                    d.append(cur - 1)
                elif ch == "}":
                    cur -= 1
                    d.append(cur)
                else:
                    d.append(cur)
            self.depth[f] = d
        return self.text[f]

    def build(self):
        for pkg_dir in find_packages(self.root, self.all_files):
            for kind, name, path, meta, lib_name in parse_cargo_targets(self.root, pkg_dir):
                cid = f"{pkg_dir or '.'}:{kind}:{name}"
                self.crates[cid] = dict(kind=kind, name=name, root=path, pkg_dir=pkg_dir, lib_name=lib_name, pkg=meta["pkg"],
                                        edition2015=meta["edition2015"], renames=meta["renames"], extern_alias={})
        for cid, c in self.crates.items():
            root = c["root"]
            if root not in self.rs_files:
                continue
            self.load(root)
            m = Mod((), root, cid, False, 0, len(self.text[root]), 0, os.path.dirname(root))
            self.mods[(cid, ())] = m
            self.file_mods.setdefault(root, []).append(m)
            self._scan_file(m)

    def _scan_file(self, top):
        f = top.file
        t = self.text[f]
        d = self.depth[f]
        # inline modules first, so that positions can be mapped
        stack = [top]
        mods_in_file = [top]

        def owner(pos):
            best = top
            for m in mods_in_file:
                if m.body[0] <= pos <= m.body[1] and (m.body[1] - m.body[0]) <= (best.body[1] - best.body[0]):
                    best = m
            return best

        decls = []
        for mm in MOD_RE.finditer(t, top.body[0], top.body[1]):
            decls.append(mm)
        # first pass: inline modules (need brace ranges), in order of position
        for mm in decls:
            if mm.group(3) == "{":
                parent = owner(mm.start())
                ob = mm.end() - 1
                cb = match_brace(t, ob)
                name = mm.group(2)
                attr = re.search(r'#\[path\s*=\s*"([^"]+)"\]\s*(?:#\[[^\]]*\]\s*)*$', self.raw[f][max(0, mm.start() - 300): mm.start()])
                inline_dir = os.path.join(parent.child_dir, name) if not attr else os.path.normpath(
                    os.path.join(os.path.dirname(f) if not parent.inline else parent.child_dir, attr.group(1)))
                child = Mod(parent.path + (name,), f, top.crate, True, ob + 1, cb, d[ob] + 1, inline_dir)
                child.parent = parent
                parent.children[name] = child
                parent.items.add(name)
                self.mods[(top.crate, child.path)] = child
                mods_in_file.append(child)
        self.file_mods.setdefault(f, [])
        for m in mods_in_file[1:]:
            self.file_mods[f].append(m)
        # second pass: file modules
        for mm in decls:
            if mm.group(3) != ";":
                continue
            parent = owner(mm.start())
            if d[mm.start()] != parent.body_depth:
                continue
            name = mm.group(2)
            # #[path] attribute immediately before
            raw = self.raw[f]
            before = raw[max(0, mm.start() - 300): mm.start()]
            pm = re.search(r'#\[path\s*=\s*"([^"]+)"\]\s*(?:#\[[^\]]*\]\s*)*$', before)
            attrs = re.search(r'((?:#\[[^\]]*\]\s*)+)$', before)
            by_cfg = re.findall(r'#\[cfg_attr\([^\]]*?(?<![\w])path\s*=\s*"([^"]+)"', attrs.group(1)) if attrs and not pm else []
            base = os.path.dirname(f) if not parent.inline else parent.child_dir
            if pm:
                cands = [os.path.normpath(os.path.join(base, pm.group(1)))]
            elif by_cfg:
                cands = [c for c in (os.path.normpath(os.path.join(base, x)) for x in by_cfg) if c in self.rs_files]
                pm = True
            else:
                cands = [os.path.join(parent.child_dir, name + ".rs"), os.path.join(parent.child_dir, name, "mod.rs")]
            target = next((c for c in cands if c in self.rs_files), None)
            if target is None:
                self.unresolved_mods.append((f, line_of(t, mm.start()), name, cands))
                continue
            self.load(target)
            is_modrs = os.path.basename(target) == "mod.rs" or bool(pm)
            child_dir = os.path.dirname(target) if is_modrs else os.path.join(os.path.dirname(target), os.path.splitext(os.path.basename(target))[0])
            child = Mod(parent.path + (name,), target, top.crate, False, 0, len(self.text[target]), 0, child_dir)
            child.parent = parent
            child.decl = (f, line_of(t, mm.start()))
            parent.children[name] = child
            parent.items.add(name)
            self.mods[(top.crate, child.path)] = child
            self.file_mods.setdefault(target, []).insert(0, child)
            self._scan_file(child)
            for number, other in enumerate(cands[1:] if by_cfg else [], start=1):
                # The file another cfg chooses for the same module: scanned, declared, reached by no path.
                self.load(other)
                twin = Mod(parent.path + (f"{name}#cfg{number}",), other, top.crate, False, 0, len(self.text[other]), 0, os.path.dirname(other))
                twin.parent = parent
                twin.decl = child.decl
                self.mods[(top.crate, twin.path)] = twin
                self.file_mods.setdefault(other, []).insert(0, twin)
                self.alternates.append((parent, twin))
                self._scan_file(twin)
        # items and uses
        self._owner = owner
        for im in ITEM_RE.finditer(t, top.body[0], top.body[1]):
            o = owner(im.start())
            if d[im.start()] == o.body_depth and im.group(2) not in KEYWORDS_NOT_NAMES:
                o.items.add(im.group(2))
                vis = PUB_BEFORE_RE.search(t[max(0, im.start() - 80): im.start()])
                scope = (vis.group(1) or "").strip() if vis else ""
                if not vis or scope == "self":
                    o.private.add(im.group(2))
                elif scope == "super" or scope.startswith("in"):
                    o.restricted[im.group(2)] = ["super"] if scope == "super" else path_segments(scope[2:])
        for im in MACRO_RE.finditer(t, top.body[0], top.body[1]):
            o = owner(im.start())
            pre = self.raw[f][max(0, im.start() - 200): im.start()]
            if re.search(r"#\[macro_export\]\s*$", pre):
                lib = self.crates[top.crate]
                self.macro_export[(top.crate, im.group(1))] = f
            o.items.add(im.group(1))
        for um in USE_RE.finditer(t, top.body[0], top.body[1]):
            o = owner(um.start())
            end = um.end()
            depth = 0
            while end < len(t):
                ch = t[end]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth < 0:
                        break
                elif ch == ";" and depth == 0:
                    break
                end += 1
            body = t[um.end():end]
            is_pub = bool(um.group(1).strip()) and d[um.start()] == o.body_depth
            ln = line_of(t, um.start())
            for segs, alias, glob, ab in parse_use_tree(body):
                o.uses.append((segs, alias, glob, ab, is_pub, ln, um.start(), end))
        top.use_spans = getattr(top, "use_spans", [])
        if top.path == ():
            for em in re.finditer(r"(?<![\w:])extern\s+crate\s+(\w+)\s+as\s+(\w+)\s*;", t):
                self.crates[top.crate]["extern_alias"][em.group(2)] = em.group(1)
        for em in re.finditer(r"(?<![\w:])enum\s+(?:r#)?(\w+)[^{;]*\{", t[top.body[0]:top.body[1]]):
            o = owner(top.body[0] + em.start())
            open_pos = top.body[0] + em.end() - 1
            if d[top.body[0] + em.start()] != o.body_depth:
                continue
            level, first, names = 0, True, set()
            for tok in re.finditer(r"[{}()\[\],]|#\s*\[[^\]]*\]|[A-Za-z_]\w*", t[open_pos + 1:match_brace(t, open_pos)]):
                x = tok.group(0)
                if x in "{([":
                    level += 1
                elif x in "})]":
                    level -= 1
                elif x == ",":
                    first = level == 0
                elif first and level == 0 and not x.startswith("#"):
                    names.add(x)
                    first = False
            o.variants[em.group(1)] = names
        for am in TYPE_ALIAS_RE.finditer(t, top.body[0], top.body[1]):
            o = owner(am.start())
            if d[am.start()] == o.body_depth:
                o.type_aliases[am.group(1)] = path_segments(am.group(2))
        for im in IMPL_RE.finditer(t, top.body[0], top.body[1]):
            o = owner(im.start())
            open_pos = t.find("{", im.end())
            if d[im.start()] != o.body_depth or open_pos < 0 or ";" in t[im.end():open_pos]:
                continue
            header = t[skip_angles(t, re.compile(r"\s*").match(t, im.end()).end()):open_pos]
            header = re.split(r"(?<![\w])where(?![\w])", header)[0]
            parts = re.split(r"(?<![\w])for(?![\w<])", header, maxsplit=1)
            written = re.match(r"\s*(" + PATH_TEXT + r")\s*(?:<.*>)?\s*$", parts[-1], re.S)
            if written is None:
                continue  # impl for a reference, a tuple, a slice or dyn Trait: no type path
            trait = re.match(r"\s*!?\s*(" + PATH_TEXT + r")", parts[0]) if len(parts) == 2 else None
            close_pos = match_brace(t, open_pos)
            members = {mm.group(1) for mm in MEMBER_RE.finditer(t, open_pos, close_pos) if d[mm.start()] == d[open_pos] + 1}
            self.impls.append(dict(mod=o, file=f, type=path_segments(written.group(1)), members=members,
                                   trait=path_segments(trait.group(1)) if trait else (["?"] if len(parts) == 2 else None),
                                   span=(open_pos, close_pos)))

    # ------------------------------------------------------------ resolution
    def lib_crate_for(self, cid):
        c = self.crates[cid]
        for oid, o in self.crates.items():
            if o["kind"] == "lib" and o["pkg_dir"] == c["pkg_dir"] and oid != cid:
                return oid, o["lib_name"]
        return None, None

    def extern_crate(self, cid, name):
        """The crate that code of crate cid names `name`: what `extern crate .. as name;` of its root binds (the
        crate itself for `self`), a dependency renamed with `package = ".."`, the library of its own package,
        or the library of that name of another package of the tree."""
        key = (cid, name)
        if key not in self.extern_crates:
            c = self.crates[cid]
            bound = c["extern_alias"].get(name)
            wanted = bound or name
            libs = [(oid, o) for oid, o in self.crates.items() if o["kind"] == "lib" and oid != cid]
            if bound == "self":
                found = cid
            elif wanted in c["renames"]:
                found = next((oid for oid, o in libs if o["pkg"] == c["renames"][wanted]), None)
            else:
                found = next((oid for oid, o in libs if o["pkg_dir"] == c["pkg_dir"] and o["lib_name"] == wanted), None) \
                    or next((oid for oid, o in libs if o["lib_name"] == wanted), None)
            self.extern_crates[key] = found
        return self.extern_crates[key]

    def module_aliases(self, m):
        """name -> Mod for `use` items in m that resolve to a module."""
        if hasattr(m, "_aliases"):
            return m._aliases
        m._aliases = {}
        for segs, alias, glob, ab, is_pub, ln, s, e in m.uses:
            if glob or not alias or not segs:
                continue
            r = self.resolve(m, segs, ab, allow_alias=False)
            if r and r[2] == len(segs):
                m._aliases[alias] = r[0]
        return m._aliases

    def resolve(self, m, segs, leading_abs=False, allow_alias=True):
        """Return (Mod reached, external flag, consumed count) or None."""
        if not segs:
            return None
        cid = m.crate
        s0 = segs[0]
        i = 0
        other = None if s0 in ("crate", "super", "self") else self.extern_crate(cid, s0)
        old_edition = self.crates[cid]["edition2015"]
        if leading_abs:
            if other:
                cur = self.mods.get((other, ()))
                i = 1
            elif old_edition:
                cur = self.mods[(cid, ())]  # edition 2015: `::a` is the crate root
            else:
                return None
        elif s0 == "crate":
            cur = self.mods[(cid, ())]
            i = 1
        elif s0 in ("super", "self"):
            cur = m
            while i < len(segs) and segs[i] in ("super", "self"):
                if segs[i] == "super":
                    cur = cur.parent
                    if cur is None:
                        return None
                i += 1
        elif s0 in m.children:
            cur = m
        elif other:
            cur = self.mods.get((other, ()))
            i = 1
        elif allow_alias and s0 in self.module_aliases(m):
            cur = self.module_aliases(m)[s0]
            i = 1
        elif old_edition and s0 in self.mods[(cid, ())].children:
            cur = self.mods[(cid, ())]  # edition 2015: a `use` path starts at the crate root
        else:
            return None
        if cur is None:
            return None
        while i < len(segs):
            s = segs[i]
            if s in cur.children:
                cur = cur.children[s]
                i += 1
                continue
            # re-exported module through pub use (e.g. pub use self::x as y)
            ali = self.module_aliases(cur) if allow_alias else {}
            if s in ali and s not in cur.items:
                cur = ali[s]
                i += 1
                continue
            break
        return (cur, False, i)

    def visible(self, m, name, origin):
        """Whether item `name` of m, reached through a re-export, is visible in module origin: `pub(super)` and
        `pub(in path)` items only inside that module (the compiler: resolve_glob_import, is_accessible_from)."""
        scope = m.restricted.get(name)
        if scope is None or origin is None:
            return True
        r = self.resolve(m, scope)
        return not r or r[2] != len(scope) or self.is_within(origin, r[0])

    def define_site(self, m, name, seen=None, reached=False, origin=None):
        """Follow pub use re-exports from module m for item `name`; return Mod defining it.

        m itself may define the item with any visibility; a module reached through a re-export provides only
        its public items and its `pub use` lines (reached=True)."""
        seen = seen or set()
        key = (m.crate, m.path, name)
        if key in seen:
            return None
        seen.add(key)
        if name in m.items and not (reached and (name in m.private or not self.visible(m, name, origin))):
            return m
        if m.path == ():
            mf = self.macro_export.get((m.crate, name))
            if mf:
                return ("file", mf)
        for segs, alias, glob, ab, is_pub, ln, s, e in m.uses:
            if reached and not is_pub:
                continue
            if glob:
                r = self.resolve(m, segs, ab)
                if r and r[2] == len(segs):
                    d = self.define_site(r[0], name, seen, True, origin)
                    if d:
                        return d
                elif r and r[2] == len(segs) - 1 and name in r[0].variants.get(segs[-1], ()):
                    return self.define_site(r[0], segs[-1], seen, True, origin) or r[0]  # `pub use kinds::Kind::*;`: a variant
                continue
            if alias == name and segs:
                r = self.resolve(m, segs, ab)
                if not r:
                    return None
                tgt, _, k = r
                if k == len(segs):
                    return tgt
                if k == len(segs) - 1:
                    d = self.define_site(tgt, segs[-1], seen, True, origin)
                    return d or tgt
                return tgt
        return None

    # ------------------------------------------------------------ members of a type
    def item_site(self, m, name, seen=None, reached=False, origin=None):
        """(Mod, name there) of the item `name` as module m provides it, followed through re-exports and renames.

        The module the lookup starts in (reached=False) gives every item and every `use` it has; a module reached
        from it gives its public ones, unless the first module lies inside it (a child sees what its ancestors hold)."""
        seen = seen if seen is not None else set()
        key = (m.crate, m.path, name)
        if key in seen:
            return None
        seen.add(key)
        if name in m.items and not (reached and (name in m.private or not self.visible(m, name, origin))):
            return (m, name)
        for segs, alias, glob, ab, is_pub, ln, s, e in m.uses:
            if (reached and not is_pub) or not segs or not (glob or alias == name):
                continue
            r = self.resolve(m, segs, ab)
            if not r:
                continue
            outside = reached or not self.is_within(m, r[0])
            if glob:
                site = self.item_site(r[0], name, seen, outside, origin) if r[2] == len(segs) else None
                if site:
                    return site
            elif r[2] == len(segs) - 1:
                return self.item_site(r[0], segs[-1], seen, outside, origin) or (r[0], segs[-1])
            else:
                return None
        return None

    def type_site(self, m, segs, followed=0):
        """(Mod, name) of the type a path written in module m names; a type alias is followed to its type."""
        key = (m.crate, m.path, tuple(segs))
        if key in self.type_sites:
            return self.type_sites[key]
        if len(segs) == 1:
            site = self.item_site(m, segs[0], None, False, m)
        else:
            r = self.resolve(m, segs)
            site = self.item_site(r[0], segs[-1], None, not self.is_within(m, r[0]), m) if r and r[2] == len(segs) - 1 else None
        if site and followed < 4 and site[1] in site[0].type_aliases:
            site = self.type_site(site[0], site[0].type_aliases[site[1]], followed + 1) or site
        self.type_sites[key] = site
        return site

    def member_refs(self):
        """`Type::member`: the file of the inherent impl block that defines the member."""
        index = {}  # (crate, module path, type name) -> member -> files of the impl blocks
        for impl in self.impls:
            site = self.type_site(impl["mod"], impl["type"]) if impl["trait"] is None else None
            if site:
                for member in impl["members"]:
                    index.setdefault((site[0].crate, site[0].path, site[1]), {}).setdefault(member, set()).add(impl["file"])
        refs = []
        for f, mods in self.file_mods.items():
            t = self.text[f]
            for topm in (x for x in mods if not x.inline):
                inl = [x for x in mods if x.inline and x.crate == topm.crate]
                use_spans = [(s, e) for mm in [topm] + inl for (_, _, _, _, _, _, s, e) in mm.uses]
                blocks = [impl for impl in self.impls if impl["file"] == f and impl["mod"].crate == topm.crate]

                def owner(pos):
                    best = topm
                    for x in inl:
                        if x.body[0] <= pos <= x.body[1] and (x.body[1] - x.body[0]) <= (best.body[1] - best.body[0]):
                            best = x
                    return best

                found = []  # (position, type segments, member)
                for pm in re.finditer(r"(?<![\w:])((?:r#)?\w+)((?:\s*::\s*(?:r#)?\w+)+)", t):
                    segs = path_segments(pm.group(0))
                    found.append((pm.start(), segs[:-1], segs[-1]))
                for pm in TURBOFISH_MEMBER_RE.finditer(t):
                    after = skip_angles(t, pm.end() - 1)
                    mm = re.match(r"\s*::\s*((?:r#)?\w+)", t[after:after + 80]) if after > pm.end() - 1 else None
                    if mm:
                        found.append((pm.start(), path_segments(pm.group(1)), path_segments(mm.group(1))[0]))
                for pm in QUALIFIED_MEMBER_RE.finditer(t):
                    found.append((pm.start(), path_segments(pm.group(1)), path_segments(pm.group(2))[0]))
                for pos, written, member in found:
                    if any(s <= pos < e for s, e in use_spans):
                        continue
                    o = owner(pos)
                    if written == ["Self"]:
                        around = [impl for impl in blocks if impl["span"][0] <= pos <= impl["span"][1]]
                        site = self.type_site(around[-1]["mod"], around[-1]["type"]) if around else None
                    else:
                        site = self.type_site(o, written)
                    for defined in sorted(index.get((site[0].crate, site[0].path, site[1]), {}).get(member, ())) if site else ():
                        if defined != f:
                            refs.append(dict(frm=f, line=line_of(t, pos), to=site[0].file, to_followed=defined, category="type-member",
                                             text="::".join(written) + "::" + member, crate=o.crate))
        return refs

    def is_within(self, m, ancestor):
        while m is not None:
            if m is ancestor:
                return True
            m = m.parent
        return False

    def glob_uses(self):
        """`use x::*` in module m and an identifier of m's own text that x provides: m uses that item."""
        refs = []
        for (cid, path), m in self.mods.items():
            globs = [(segs, ab, ln) for segs, alias, glob, ab, is_pub, ln, s, e in m.uses if glob]
            if not globs:
                continue
            t = self.text[m.file]
            spans = [(s, e) for (_, _, _, _, _, _, s, e) in m.uses]
            inner = [x.body for x in self.file_mods.get(m.file, []) if x.inline and x is not m and self.is_within(x, m)]
            # The variants a local enum declares are names of that enum, not uses of an imported item.
            variants = set()
            for em in re.finditer(r"(?<![\w:])enum\s+\w+[^{;]*\{", t[m.body[0]:m.body[1]]):
                open_pos = m.body[0] + em.end() - 1
                close_pos = match_brace(t, open_pos)
                body = t[open_pos + 1:close_pos]
                depth = 0
                start = True
                for tok in re.finditer(r"[{}()\[\],]|[A-Za-z_]\w*", body):
                    x = tok.group(0)
                    if x in "{([":
                        depth += 1
                    elif x in "})]":
                        depth -= 1
                    elif x == ",":
                        start = depth == 0
                    elif start and depth == 0:
                        variants.add((open_pos + 1 + tok.start()))
                        start = False
            names = set()
            explicit = {alias for segs, alias, glob, ab, is_pub, ln, s, e in m.uses if alias and not glob}
            for im in IDENT_RE.finditer(t, m.body[0], m.body[1]):
                pos = im.start()
                if any(s <= pos < e for s, e in spans) or any(a <= pos <= b for a, b in inner) or pos in variants:
                    continue
                if re.match(r"\s*:(?!:)", t[im.end():im.end() + 8]):
                    continue  # a field, a parameter or a binding with its type: a local name
                name = im.group(1)
                if name not in RUST_KEYWORDS and name not in m.items and name not in explicit:
                    names.add(name)  # a name the module defines or imports by name is not taken from a glob
            for segs, ab, ln in globs:
                r = self.resolve(m, segs, ab)
                if not r or r[2] != len(segs):
                    continue
                source = r[0]
                for name in sorted(names):
                    d = self.define_site(source, name, None, not self.is_within(m, source), m)
                    if d is None:
                        continue
                    defined = self.file_of(d)
                    if defined != m.file:
                        refs.append(dict(frm=m.file, line=ln, to=source.file, to_followed=defined, category="glob-use",
                                         text="::".join(segs) + "::* " + name, crate=cid))
        return refs

    def file_of(self, x):
        if isinstance(x, tuple):
            return x[1]
        return x.file

    def target_of(self, m, segs, ab, glob):
        r = self.resolve(m, segs, ab)
        if not r:
            return None
        tgt, _, k = r
        to = tgt.file
        followed = to
        if k < len(segs):
            item = segs[k]
            # macro_export macro reached through crate root
            if tgt.path == () and (tgt.crate, item) in self.macro_export:
                to = followed = self.macro_export[(tgt.crate, item)]
            else:
                d = self.define_site(tgt, item, None, False, m)
                if d is not None:
                    followed = self.file_of(d)
        return to, followed, k

    def collect(self):
        refs = []
        seen_mods = set()
        for (cid, path), m in self.mods.items():
            # mod declarations
            for name, ch in list(m.children.items()) + [(twin.path[-1].split("#")[0], twin) for parent, twin in self.alternates if parent is m]:
                if not ch.inline:
                    refs.append(dict(frm=m.file, line=ch.decl[1], to=ch.file, to_followed=ch.file, category="mod-decl", text=f"mod {name};", crate=cid))
            for segs, alias, glob, ab, is_pub, ln, s, e in m.uses:
                r = self.target_of(m, segs, ab, glob)
                if r:
                    refs.append(dict(frm=m.file, line=ln, to=r[0], to_followed=r[1], category="pub-use" if is_pub else "use", text="::".join(segs) + ("::*" if glob else ""), crate=cid))
        # inline paths: for each file, every module occurrence
        for f, mods in self.file_mods.items():
            if not mods:
                continue
            t = self.text[f]
            top = [x for x in mods if not x.inline]
            for topm in top:
                inl = [x for x in mods if x.inline and x.crate == topm.crate]
                allm = [topm] + inl
                use_spans = [(s, e) for mm in allm for (_, _, _, _, _, _, s, e) in mm.uses]

                def owner(pos):
                    best = topm
                    for x in inl:
                        if x.body[0] <= pos <= x.body[1] and (x.body[1] - x.body[0]) <= (best.body[1] - best.body[0]):
                            best = x
                    return best

                for pm in re.finditer(r"(?<![\w:])((?:r#)?\w+)((?:\s*::\s*(?:r#)?\w+)+)", t):
                    if any(s <= pm.start() < e for s, e in use_spans):
                        continue
                    segs = [pm.group(1)] + re.findall(r"(?:r#)?\w+", pm.group(2))
                    segs = [x[2:] if x.startswith("r#") else x for x in segs]
                    o = owner(pm.start())
                    s0 = segs[0]
                    pre = t[max(0, pm.start() - 12): pm.start()]
                    if re.search(r"\(\s*in\s+$", pre):
                        continue  # pub(in path) visibility
                    if s0 in ("crate", "super", "self") or self.extern_crate(o.crate, s0):
                        cat = "inline-path"
                    elif s0 in o.children or s0 in self.module_aliases(o):
                        cat = "inline-alias-path"
                    else:
                        continue
                    r = self.target_of(o, segs, False, False)
                    if r:
                        refs.append(dict(frm=f, line=line_of(t, pm.start()), to=r[0], to_followed=r[1], category=cat, text="::".join(segs), crate=o.crate))
                for pm in re.finditer(r"(?<![\w:>)\]])::\s*((?:r#)?\w+(?:\s*::\s*(?:r#)?\w+)+)", t):
                    if any(s <= pm.start() < e for s, e in use_spans):
                        continue
                    segs = path_segments(pm.group(1))
                    o = owner(pm.start())
                    r = self.target_of(o, segs, True, False)  # `::name::item`: a crate, or the crate root in edition 2015
                    if r:
                        refs.append(dict(frm=f, line=line_of(t, pm.start()), to=r[0], to_followed=r[1], category="inline-path",
                                         text="::" + "::".join(segs), crate=o.crate))
        refs.extend(self.glob_uses())
        refs.extend(self.member_refs())
        # dedupe identical refs (same file reached via several crates)
        uniq = {}
        for r in refs:
            k = (r["frm"], r["line"], r["to"], r["category"], r["text"])
            uniq.setdefault(k, r)
        return [r for r in uniq.values() if r["frm"] != r["to"] or r["frm"] != r["to_followed"]]


def main():
    root, out = sys.argv[1], sys.argv[2]
    repo = Repo(root)
    repo.build()
    refs = repo.collect()
    reached = sorted({m.file for m in repo.mods.values()})
    res = dict(
        rs_files=repo.rs_files,
        reached_files=reached,
        crates=repo.crates,
        unresolved_mod_decls=repo.unresolved_mods,
        macro_export={f"{k[0]}::{k[1]}": v for k, v in repo.macro_export.items()},
        refs=[dict(frm=r["frm"], line=r["line"], to=r["to"], to_followed=r["to_followed"], category=r["category"], text=r["text"]) for r in refs],
        modules={f"{cid}::{'::'.join(p)}": m.file for (cid, p), m in repo.mods.items()},
    )
    json.dump(res, open(out, "w"), indent=1)
    print("rs", len(repo.rs_files), "reached", len(reached), "refs", len(refs), "unresolved mods", len(repo.unresolved_mods))


if __name__ == "__main__":
    main()
