"""Independent Rust module-dependency resolver (no tree-sitter, no Wingbox code).

Usage: python3 -I rust_resolve.py <repo_root> <out_json>

Reads only .rs and Cargo.toml files. Builds the module tree from crate roots
(Cargo targets), then extracts references from `mod` declarations, `use`
trees and inline paths, and resolves each reference to the first-party file
that defines the module reached by the path.

Output JSON: {"files": [...], "crates": {...}, "refs": [ {from, line, to,
to_followed, category, text} ], "modules": {modpath: file}}
  to          = file of the module the path reaches (module semantics)
  to_followed = file that defines the item, following `pub use` re-exports
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
    for sec in sections:
        head = sec.split("]", 1)[0].strip()
        body = sec.split("]", 1)[1] if "]" in sec else ""
        kv = dict(re.findall(r'^\s*([\w-]+)\s*=\s*"?([^"\n]*)"?', body, flags=re.M))
        if head == "package":
            pkg_name = kv.get("name")
            if kv.get("autobins", "true").strip() == "false":
                autobins = False
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
        for cand in [j("src/main.rs")] + sorted(
            j("src/bin/" + f) for f in (os.listdir(os.path.join(repo, rel_dir, "src/bin")) if os.path.isdir(os.path.join(repo, rel_dir, "src/bin")) else []) if f.endswith(".rs")
        ):
            if os.path.exists(os.path.join(repo, cand)) and cand not in seen:
                targets.append(("bin", os.path.splitext(os.path.basename(cand))[0], cand))
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
    return [(k, n, p, pkg_name, crate_lib_name) for k, n, p in targets]


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
        self.uses = []  # (segs, alias, glob, abs, pub, line)
        self.parent = None


MOD_RE = re.compile(r"(?<![\w:])((?:pub\s*(?:\([^)]*\))?\s+)?)mod\s+(?:r#)?(\w+)\s*([;{])")
ITEM_RE = re.compile(r"(?<![\w:])(fn|struct|enum|trait|type|const|static|union)\s+(?:r#)?(\w+)")
MACRO_RE = re.compile(r"macro_rules!\s*(\w+)")
USE_RE = re.compile(r"(?<![\w:])((?:pub\s*(?:\([^)]*\))?\s+)?)use\s+")


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
            for kind, name, path, pkg, lib_name in parse_cargo_targets(self.root, pkg_dir):
                cid = f"{pkg_dir or '.'}:{kind}:{name}"
                self.crates[cid] = dict(kind=kind, name=name, root=path, pkg_dir=pkg_dir, lib_name=lib_name)
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
                child = Mod(parent.path + (name,), f, top.crate, True, ob + 1, cb, d[ob] + 1, os.path.join(parent.child_dir, name))
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
            if pm:
                base = os.path.dirname(f) if not parent.inline else parent.child_dir
                cands = [os.path.normpath(os.path.join(base, pm.group(1)))]
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
        # items and uses
        self._owner = owner
        for im in ITEM_RE.finditer(t, top.body[0], top.body[1]):
            o = owner(im.start())
            if d[im.start()] == o.body_depth and im.group(2) not in KEYWORDS_NOT_NAMES:
                o.items.add(im.group(2))
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

    # ------------------------------------------------------------ resolution
    def lib_crate_for(self, cid):
        c = self.crates[cid]
        for oid, o in self.crates.items():
            if o["kind"] == "lib" and o["pkg_dir"] == c["pkg_dir"] and oid != cid:
                return oid, o["lib_name"]
        return None, None

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
        lib_id, lib_name = self.lib_crate_for(cid)
        if leading_abs:
            if lib_name and s0 == lib_name:
                cur = self.mods.get((lib_id, ()))
                i = 1
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
        elif lib_name and s0 == lib_name:
            cur = self.mods.get((lib_id, ()))
            i = 1
        elif allow_alias and s0 in self.module_aliases(m):
            cur = self.module_aliases(m)[s0]
            i = 1
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

    def define_site(self, m, name, seen=None):
        """Follow pub use re-exports from module m for item `name`; return Mod defining it."""
        seen = seen or set()
        key = (m.crate, m.path, name)
        if key in seen:
            return None
        seen.add(key)
        if name in m.items:
            return m
        if m.path == ():
            mf = self.macro_export.get((m.crate, name))
            if mf:
                return ("file", mf)
        for segs, alias, glob, ab, is_pub, ln, s, e in m.uses:
            if glob:
                r = self.resolve(m, segs, ab)
                if r and r[2] == len(segs):
                    d = self.define_site(r[0], name, seen)
                    if d:
                        return d
                continue
            if alias == name and segs:
                r = self.resolve(m, segs, ab)
                if not r:
                    return None
                tgt, _, k = r
                if k == len(segs):
                    return tgt
                if k == len(segs) - 1:
                    d = self.define_site(tgt, segs[-1], seen)
                    return d or tgt
                return tgt
        return None

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
                d = self.define_site(tgt, item)
                if d is not None:
                    followed = self.file_of(d)
        return to, followed, k

    def collect(self):
        refs = []
        seen_mods = set()
        for (cid, path), m in self.mods.items():
            # mod declarations
            for name, ch in m.children.items():
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
                    lib_id, lib_name = self.lib_crate_for(o.crate)
                    if s0 in ("crate", "super", "self") or (lib_name and s0 == lib_name):
                        cat = "inline-path"
                    elif s0 in o.children or s0 in self.module_aliases(o):
                        cat = "inline-alias-path"
                    else:
                        continue
                    r = self.target_of(o, segs, False, False)
                    if r:
                        refs.append(dict(frm=f, line=line_of(t, pm.start()), to=r[0], to_followed=r[1], category=cat, text="::".join(segs), crate=o.crate))
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
