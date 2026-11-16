"""Independent import extractor and resolver for a TypeScript monorepo (no tree-sitter, no Wingbox code).

Usage: python3 -I ts_resolve.py <repo_root> <scope_prefix> <out.json>
Reads the list of tracked files from `git ls-files`, scans every JS/TS source under the scope,
extracts import-like statements with a small comment/string-aware scanner and resolves each
specifier the way a bundler-mode TypeScript project does: relative paths, tsconfig `paths`
(following `extends`), workspace packages by name (package.json `exports` / `types` / `main`).
"""
from __future__ import annotations
import json, os, re, subprocess, sys

repo, scope, out_path = sys.argv[1], (sys.argv[2].rstrip("/") + "/") if sys.argv[2] not in ("", ".") else "", sys.argv[3]
SRC_EXT = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")

tracked = [p for p in subprocess.run(["git", "-C", repo, "ls-files", "-z"] + ([scope] if scope else []), capture_output=True).stdout.decode("utf-8", "replace").split("\0") if p]
tracked_set = set(tracked)
dirs: set[str] = set()
for p in tracked:
    d = os.path.dirname(p)
    while d and d not in dirs:
        dirs.add(d)
        d = os.path.dirname(d)


def read(p: str) -> str:
    with open(os.path.join(repo, p), encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------- scanner
REGEX_PREV = set("(,=:[!&|?{;+-*%~^")
REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "void", "delete", "throw", "new", "yield", "await"}


def _skip_template(src: str, i: int) -> tuple[int, bool]:
    """i at opening backtick; returns (index after closing backtick, has_substitution)."""
    n = len(src)
    j = i + 1
    subst = False
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            return j + 1, subst
        if c == "$" and j + 1 < n and src[j + 1] == "{":
            subst = True
            j = _skip_braced(src, j + 2)
            continue
        j += 1
    return n, subst


def _skip_braced(src: str, j: int) -> int:
    """j just after an opening brace of a template substitution; returns index after the matching brace."""
    n = len(src)
    depth = 1
    while j < n:
        c = src[j]
        two = src[j:j + 2]
        if two == "//":
            k = src.find("\n", j)
            j = n if k < 0 else k
            continue
        if two == "/*":
            k = src.find("*/", j + 2)
            j = n if k < 0 else k + 2
            continue
        if c in "\"'":
            k = j + 1
            while k < n and src[k] != c and src[k] != "\n":
                if src[k] == "\\":
                    k += 1
                k += 1
            j = k + 1
            continue
        if c == "`":
            j, _ = _skip_template(src, j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def scan(src: str) -> tuple[str, list[str]]:
    """Return code text with comments removed and every string literal replaced by a numbered placeholder."""
    out: list[str] = []
    strings: list[str] = []
    n = len(src)
    i = 0
    prev_sig = ""
    word = ""
    prev_word = ""
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            k = src.find("\n", i)
            i = n if k < 0 else k
            continue
        if two == "/*":
            k = src.find("*/", i + 2)
            end = n if k < 0 else k + 2
            out.append("\n" * src.count("\n", i, end))
            i = end
            continue
        if c in "\"'":
            k = i + 1
            while k < n and src[k] != c and src[k] != "\n":
                if src[k] == "\\":
                    k += 1
                k += 1
            if k < n and src[k] == c:
                strings.append(src[i + 1:k])
                out.append(f'"\x00{len(strings) - 1}\x00"')
                i = k + 1
                prev_sig = '"'
                word = ""
                continue
            out.append(" ")
            i += 1
            continue
        if c == "`":
            end, subst = _skip_template(src, i)
            body = src[i + 1:end - 1]
            strings.append("\x01TEMPLATE_WITH_SUBSTITUTION" if subst else body)
            out.append(f'"\x00{len(strings) - 1}\x00"' + "\n" * src.count("\n", i, end))
            i = end
            prev_sig = '"'
            word = ""
            continue
        if c == "/":
            if prev_sig == "" or prev_sig in REGEX_PREV or prev_word in REGEX_WORDS:
                k = i + 1
                in_class = False
                ok = False
                while k < n and src[k] != "\n":
                    ch = src[k]
                    if ch == "\\":
                        k += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        ok = True
                        break
                    k += 1
                if ok:
                    out.append(" 0 ")
                    i = k + 1
                    prev_sig = "0"
                    word = ""
                    continue
        out.append(c)
        if c.isalnum() or c in "_$":
            word += c
        else:
            if word:
                prev_word = word
                word = ""
            if not c.isspace():
                prev_word = ""
        if not c.isspace():
            prev_sig = c
        i += 1
    return "".join(out), strings


S = r'"\x00(\d+)\x00"'
RE_IMPORT_FROM = re.compile(r'(?<![.\w$])import(?=[\s{*])\s*((?:type\s+)?)([\w$\s,{}*]+?)\s*from\s*' + S)
RE_SIDE = re.compile(r'(?<![.\w$])import\s*' + S)
RE_EXPORT_FROM = re.compile(r'(?<![.\w$])export\s+((?:type\s+)?)(?:\*(?:\s*as\s+[\w$]+)?|\{[^}]*\})\s*from\s*' + S)
RE_DYNAMIC = re.compile(r'(?<![.\w$])import\s*\(\s*' + S)
RE_REQUIRE = re.compile(r'(?<![.\w$])require\s*\(\s*' + S)
RE_OTHER = re.compile(r'(?<![\w$])(vi\.mock|vi\.doMock|vi\.unmock|vi\.doUnmock|vi\.importActual|vi\.importMock|jest\.mock|require\.resolve|import\.meta\.resolve)\s*(?:<[^>()]*>)?\s*\(\s*' + S)


def extract(src: str) -> list[dict]:
    code, strings = scan(src)
    found: list[dict] = []
    seen_pos: set[int] = set()

    def add(m: re.Match, kind: str, group: int) -> None:
        pos = m.start(group)
        if pos in seen_pos:
            return
        seen_pos.add(pos)
        spec = strings[int(m.group(group))]
        found.append({
            "line": code.count("\n", 0, pos) + 1,
            "stmt_line": code.count("\n", 0, m.start()) + 1,
            "spec": spec,
            "kind": kind,
        })

    for m in RE_IMPORT_FROM.finditer(code):
        clause = m.group(2)
        kind = "import-type" if m.group(1) else "import"
        if kind == "import" and "{" in clause:
            names = [x.strip() for x in clause[clause.index("{") + 1:clause.rindex("}")].split(",") if x.strip()] if "}" in clause else []
            default_part = clause[:clause.index("{")].strip().strip(",").strip()
            if names and not default_part and all(x.startswith("type ") for x in names):
                kind = "import-inline-type-only"
        add(m, kind, 3)
    for m in RE_EXPORT_FROM.finditer(code):
        add(m, "export-type-from" if m.group(1) else "export-from", 2)
    for m in RE_SIDE.finditer(code):
        add(m, "side-effect", 1)
    for m in RE_DYNAMIC.finditer(code):
        before = code[max(0, m.start() - 12):m.start()]
        add(m, "type-import-expr" if re.search(r"typeof\s*$", before) else "dynamic", 1)
    for m in RE_REQUIRE.finditer(code):
        add(m, "require", 1)
    for m in RE_OTHER.finditer(code):
        add(m, "other:" + m.group(1), 2)
    found.sort(key=lambda x: (x["line"], x["kind"]))
    return found


# ---------------------------------------------------------------- tsconfig / packages
def load_jsonc(p: str):
    code, strings = scan(read(p))
    # put strings back as JSON strings, drop trailing commas
    def back(m: re.Match) -> str:
        return json.dumps(strings[int(m.group(1))].replace('\\"', '"').replace("\\\\", "\\")) if False else '"' + strings[int(m.group(1))] + '"'
    text = re.sub(S, back, code)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


_tsconfig_cache: dict[str, tuple] = {}


def effective_paths(cfg_path: str, depth: int = 0) -> tuple[dict, str] | None:
    """(paths, base_dir) of a tsconfig after following extends; None when no paths anywhere."""
    if cfg_path in _tsconfig_cache:
        return _tsconfig_cache[cfg_path]
    result = None
    try:
        data = load_jsonc(cfg_path)
    except Exception as error:  # noqa: BLE001 - report and continue
        print(f"[resolve] cannot parse {cfg_path}: {error}", file=sys.stderr)
        data = {}
    co = data.get("compilerOptions") or {}
    here = os.path.dirname(cfg_path)
    if isinstance(co.get("paths"), dict):
        base = os.path.normpath(os.path.join(here, co["baseUrl"])) if isinstance(co.get("baseUrl"), str) else here
        result = (co["paths"], base)
    else:
        ext = data.get("extends")
        for e in ([ext] if isinstance(ext, str) else ext or []):
            if not e.startswith("."):
                continue
            parent = os.path.normpath(os.path.join(here, e))
            if not parent.endswith(".json"):
                parent += ".json"
            if parent in tracked_set and depth < 8:
                got = effective_paths(parent, depth + 1)
                if got:
                    result = got
    _tsconfig_cache[cfg_path] = result
    return result


def nearest(filename: str, start_dir: str) -> str | None:
    d = start_dir
    while True:
        cand = (d + "/" if d else "") + filename
        if cand in tracked_set:
            return cand
        if not d or d + "/" == scope or d == scope.rstrip("/"):
            return None
        d = os.path.dirname(d)


packages: dict[str, dict] = {}
for p in tracked:
    if os.path.basename(p) == "package.json":
        try:
            data = json.loads(read(p))
        except Exception as error:  # noqa: BLE001
            print(f"[resolve] cannot parse {p}: {error}", file=sys.stderr)
            continue
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            packages[data["name"]] = {"dir": os.path.dirname(p), "data": data}
pkg_names = sorted(packages, key=len, reverse=True)


def try_file(base: str) -> str | None:
    base = os.path.normpath(base)
    for js, alts in ((".js", (".ts", ".tsx", ".d.ts")), (".jsx", (".tsx",)), (".mjs", (".mts", ".d.mts")), (".cjs", (".cts", ".d.cts"))):
        if base.endswith(js):
            for a in alts:
                if base[: -len(js)] + a in tracked_set:
                    return base[: -len(js)] + a
    if base in tracked_set:
        return base
    for ext in (".ts", ".tsx", ".d.ts", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json"):
        if base + ext in tracked_set:
            return base + ext
    if base in dirs:
        pj = base + "/package.json"
        if pj in tracked_set:
            try:
                data = json.loads(read(pj))
                for key in ("types", "typings", "module", "main"):
                    if isinstance(data.get(key), str):
                        got = try_file(os.path.join(base, data[key]))
                        if got:
                            return got
            except Exception as error:  # noqa: BLE001
                print(f"[resolve] cannot parse {pj}: {error}", file=sys.stderr)
        for idx in ("index.ts", "index.tsx", "index.d.ts", "index.js", "index.jsx", "index.mjs", "index.cjs"):
            if base + "/" + idx in tracked_set:
                return base + "/" + idx
    return None


def pick_condition(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("types", "import", "default", "require", "node"):
            if key in value:
                got = pick_condition(value[key])
                if got:
                    return got
        for v in value.values():
            got = pick_condition(v)
            if got:
                return got
    return None


def resolve_workspace(spec: str) -> tuple[str, str | None, str] | None:
    """(package name, resolved file or None, detail) when spec names a workspace package."""
    for name in pkg_names:
        if spec == name or spec.startswith(name + "/"):
            pkg = packages[name]
            sub = "." if spec == name else "./" + spec[len(name) + 1:]
            exports = pkg["data"].get("exports")
            d = pkg["dir"]
            if exports is not None:
                target = None
                if isinstance(exports, str):
                    target = exports if sub == "." else None
                elif isinstance(exports, dict):
                    if not any(k.startswith(".") for k in exports):
                        target = pick_condition(exports) if sub == "." else None
                    elif sub in exports:
                        target = pick_condition(exports[sub])
                    else:
                        for key, val in exports.items():
                            if "*" in key:
                                pre, post = key.split("*", 1)
                                if sub.startswith(pre) and sub.endswith(post) and len(sub) >= len(pre) + len(post):
                                    star = sub[len(pre):len(sub) - len(post) if post else len(sub)]
                                    picked = pick_condition(val)
                                    if picked:
                                        target = picked.replace("*", star)
                                        break
                if target is None:
                    return name, None, "not in package exports"
                got = try_file(os.path.join(d, target))
                return name, got, "exports" if got else f"exports target not tracked: {target}"
            if sub == ".":
                for key in ("types", "typings", "module", "main"):
                    val = pkg["data"].get(key)
                    if isinstance(val, str):
                        got = try_file(os.path.join(d, val))
                        if got:
                            return name, got, key
                got = try_file(os.path.join(d, "index"))
                if got:
                    return name, got, "index"
                declared = [pkg["data"].get(k) for k in ("types", "main") if isinstance(pkg["data"].get(k), str)]
                return name, None, f"entry not tracked: {declared}"
            got = try_file(os.path.join(d, sub[2:]))
            return name, got, "subpath" if got else "subpath not found"
    return None


def match_alias(pattern: str, spec: str) -> str | None:
    if "*" not in pattern:
        return "" if spec == pattern else None
    pre, post = pattern.split("*", 1)
    if spec.startswith(pre) and spec.endswith(post) and len(spec) >= len(pre) + len(post):
        return spec[len(pre):len(spec) - len(post) if post else len(spec)]
    return None


def resolve(spec: str, src: str) -> dict:
    if spec == "\x01TEMPLATE_WITH_SUBSTITUTION":
        return {"how": "non-literal", "target": None}
    clean = spec.split("?", 1)[0]
    if clean.startswith("."):
        got = try_file(os.path.join(os.path.dirname(src), clean))
        return {"how": "relative", "target": got}
    if clean.startswith("/"):
        return {"how": "absolute", "target": None}
    cfg = nearest("tsconfig.json", os.path.dirname(src))
    if cfg:
        eff = effective_paths(cfg)
        if eff:
            paths, base = eff
            for pattern in sorted(paths, key=len, reverse=True):
                star = match_alias(pattern, clean)
                if star is None:
                    continue
                for target in paths[pattern]:
                    got = try_file(os.path.join(base, target.replace("*", star, 1)))
                    if got:
                        return {"how": "alias", "target": got, "alias": pattern, "tsconfig": cfg}
                ws = resolve_workspace(clean)
                if ws and ws[1]:
                    return {"how": "workspace", "target": ws[1], "package": ws[0], "detail": ws[2]}
                return {"how": "alias", "target": None, "alias": pattern, "tsconfig": cfg}
    ws = resolve_workspace(clean)
    if ws:
        return {"how": "workspace", "target": ws[1], "package": ws[0], "detail": ws[2]}
    return {"how": "external", "target": None}


files = [p for p in tracked if p.endswith(SRC_EXT)]
result: dict[str, list[dict]] = {}
for p in files:
    try:
        items = extract(read(p))
    except Exception as error:  # noqa: BLE001
        print(f"[resolve] cannot scan {p}: {error}", file=sys.stderr)
        continue
    for it in items:
        it.update(resolve(it["spec"], p))
    result[p] = items

json.dump({"scope": scope, "files": result, "packages": {n: v["dir"] for n, v in packages.items()},
           "tsconfig_paths": {k: (v[0], v[1]) if v else None for k, v in _tsconfig_cache.items()}}, open(out_path, "w"))
stats: dict[str, int] = {}
for items in result.values():
    for it in items:
        key = f'{it["how"]}:{"ok" if it["target"] else "none"}'
        stats[key] = stats.get(key, 0) + 1
print(len(files), "source files scanned")
print(json.dumps(stats, indent=1, sort_keys=True))
print(json.dumps({k: (v[0], v[1]) if v else None for k, v in _tsconfig_cache.items()}, indent=1))
