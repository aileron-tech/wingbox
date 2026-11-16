# S-expression Output Notation

Every output line is a self-contained fact. Each fact that references a module
carries a compact `:lang` value such as `py`, `ts`, `rs`, or `rb`.

**Module ID**: relative path from workspace root, no extension, no `/index`. Example: `drive/modules/drive_db/api/drive_db_api.ts` -> `drive/modules/drive_db/api/drive_db_api`.

## Raw Graph Facts (in .code_graph.sexp)

```scheme
(node <id> <lang> "<purpose>" :naming <convention> :pkg <repo>)
(fn <mod> <name> <line> :eid <uuid>)
(cls <mod> <name> <line> :eid <uuid> (:bases A) (:deco D) (:methods m1 m2))
(mod <mod> <name> <line> :eid <uuid> (:methods m1 m2))
(ifc <mod> <name> <line> :eid <uuid> (:methods m1 m2))
(typ <mod> <name> <line> :eid <uuid>)
(enm <mod> <name> <line> :eid <uuid>)
(imports <src> <tgt> [:via-base] [:association|:concern|:job|:mailer|:spec] [:lsp])
(calls-api <src> <tgt> :via-ifc [:lsp])
(data-flow <src> <tgt> [:validated])
(refs <src> <tgt>::<Entity> [:callback] [:lsp])
(calls <src>::<fn> <tgt>::<fn> :lines L1 L2 :lsp)
```

## Use File (.code_graph.uses.sexp, next to the graph)

What only `entity`, `usages` and `entity --positions` read; module IDs are the
short IDs of the graph whose digest the header carries (a file that does not
match the graph is ignored with a warning).

```scheme
(code-graph-uses :schema 1 :key <inputs> :positions no|yes :graph <digest>)
(var <mod> <Name> <line>)                                 ; module-level variable (Python)
(use <mod> <tgt>::<Entity>)                               ; :positions no
(use <mod> <tgt>::<Entity> <line>:<col>[<kind>] ...)      ; :positions yes
(use <mod> <tgt>::<Class>.<method> <line>:<col>c ...)
(unresolved <mod> :method-calls <count>)
(unresolved-call <mod> <method> <line>:<col> ...)
```

`wingbox build` writes the file without positions: one `use` line per file and
definition in another file it uses, from the uses that can name another file
(import and export statements, qualified paths, names a glob import may
provide). That keeps the build as fast as without the use index. The first
query that needs positions has the builder walk every file of a language with
a use index again, from the build cache, and rewrite the file with positions:
every use, also inside its own file. A build without changed files keeps the
file as it is, positions included.

The letter after a position is the kind of use: `i` import, `r` re-export, `c`
call, `t` type, `e` extends or implements, none for any other reference.
`unresolved-call` keeps the sites of unresolved method calls whose method name
some class of the repository defines (the others are only counted).

`use` lists every place where `<mod>` names `<Entity>` defined in `<tgt>`: an
import, a name, a qualified path, a type, a re-export; line and column are
1-based. One line per file and definition. A method call `recv.m()` is a use of
`<Class>.<method>` only when the receiver's class is in sight: `self` or `this`
in the class, a parameter or variable annotated with the class, a variable
built by its constructor; the method may come from a base class. Any other
method call is not guessed: `unresolved` counts them per module.

## boundary Output

```scheme
(boundary <mod> :lang py :file <path> :purpose "<desc>")
(cls <mod> <Name> <line> :lang py (:bases A) (:deco D) (:methods m1 m2))
(boundary-dep <mod> <dep> :lang ts :file <path> :relation imports)
(boundary-rdep <mod> <rdep> :lang py :file <path> :relation imports)
(boundary-summary <mod> :total 10 :deps 7 :rdeps 2)
```

## analyze Output

```scheme
(analyze <mod> :lang py "<purpose>" :file <path>)
(cls <mod> <Name> <line> :lang py (:bases A) (:deco D) (:methods m1 m2))
(analyze-import <mod> <tgt> :lang ts :file <path>)
(analyze-dataflow <mod> <tgt> :lang py :validated)
(refs <src> <tgt>::<Entity>)
(analyze-dependent <mod> <src> :lang tsx :via imports refs :file <path>)
(analyze-more <mod> :dependents 298 :rerun "analyze <mod> --all")
(dep <root> <tgt> :lang py :via imports :depth 1)
(key-entity <mod> <Name> :lang py :kind cls :line 32 :refs 7 :modules 4)
(key-entity-ref <Name> <ref-mod> :lang ts)
(key-entity-more <mod> <Name> :modules 185 :rerun "analyze <mod> --all")
(boundary-file <mod> <file-path>)
(layer <mod> <dep-name> :layer domain)
(analyze-summary <mod> :entities 31 :imports 1 :dataflows 0 :refs 1 :dependents 50 :boundary-files 2)
```

Paths are relative to the repository root. Each dependent file is named once, on
its `analyze-dependent` line; `boundary-file` names the module and the files it
imports. The counts in `key-entity` and `analyze-summary` are always complete.
Without `--all` the answer names at most 200 dependents and 20 modules per key
entity; an `analyze-more` or `key-entity-more` line gives the number left out.

## entity Output

```scheme
(entity-def cls <mod> <Name> <line> :lang py :file <path>)
(entity-def ifc <mod> <Name> <line> :lang ts :file <path>)
(refs <src-mod> <tgt-mod>::<Name>)
(refs <src-mod> <tgt-mod>::<Name> :reexport)                  ; from the use file
(use <src-mod> <line> <col> <tgt-mod>::<Name> :file <path>)   ; with --positions
```

`entity <Name> --positions` adds one `use` line per place that names the
definition, as an editor's find usages lists them, sorted by file, line and
column; `:file` is relative to the repository root.

Each `refs` line names a module that uses `<Name>` defined in `<tgt-mod>`: by an
import, a qualified path (`m.Name`, `m::Name`), a glob import or a re-export
(`__init__.py`, `pub use`). `:reexport` marks a module that reaches the
definition only through a re-export and does not import `<tgt-mod>` itself;
these lines come from the use file, so module-level commands (`impact`,
`deps`, `rdeps`, `analyze`) never see them and keep following imports. A
TypeScript `const` or a Rust `const` or `static` is an `fn` entity; a Python
module-level variable is a `var` definition of the use file.

## usages Output

```scheme
(usages-target <mod>::<Name> :kind cls :file <path> :line <n>)
(usage <src-mod> <line> <col> :kind call :file <path>)
(usage <src-mod> <line> <col> :kind unresolved-call :file <path>)
(usage-file <src-mod> :kind reference :file <path>)    ; a language without use positions
(usages-more <mod>::<Name> :hidden 12 :rerun "usages <symbol> --all")
(usages-summary <mod>::<Name> :uses 7 :files 2 :unresolved 0)
(usages-ambiguous <Name> :definitions 2)
(usages-candidate <mod>::<Name> :kind cls :file <path> :line <n>)
```

`usages` without `--sexp` prints the same facts as text: the target, the
counts, then each file with its `line:col` and kind. Kinds: `import`,
`re-export`, `call`, `type`, `extends` (extends or implements), `reference`,
`unresolved-call`. Uses are sorted by file, line and column; at most 200 unless
`--all`. A bare name with several definitions answers with the candidates.

## deps / rdeps Output

```scheme
(deps-root <mod> :lang py)
(dep <root> <tgt> :lang ts :via imports :depth 1)
(dep <root> <tgt> :lang py :via data-flow :depth 2 :cycle)
(rdeps-root <mod> :lang ts)
(rdep <root> <src> :lang tsx :via imports :depth 1)
```

## flow Output

```scheme
(flow <src>::<fn> <tgt>::<fn> :lang py :via calls :depth 0)
(flow <src>::<fn> <tgt> :lang ts :via imports :depth 1 :module-level)
```

## boundaries Output

```scheme
(violation <src> <tgt> :src-layer domain :tgt-layer api :via imports :lang ts "<reason>")
(violation-type-only <src> <tgt> :src-layer domain :tgt-layer infra :via imports :lang ts "<reason>")
(violation-candidate <src> <tgt> :src-layer infra :tgt-layer api :via imports :lang ts "<reason>")
(boundaries-summary :violations 2 :type-only 6 :candidates 7 :checked 3587)
(boundaries-ok :checked 3587)
```

`violation`: both layers come from folders or the file type. `violation-type-only`:
a TypeScript import of types or interfaces only (`import type`, or every imported
name is a type or an interface of the target); the compiler erases it, so it is
not counted as a violation, but a change of the type still reaches the importer.
`violation-candidate`: a layer is only guessed from the role of a file.

## impact Output (`--sexp`)

```scheme
(impact-root <mod> :lang ts :file <path>)
(impact <root> <src> :lang ts :via imports :depth 1)
(impact-more <root> :hidden 549 :rerun "impact <file> 2 --all")
(impact-summary <root> :direct 650 :total 749 :depth 2)
(read-first <path>)
(read-first-more :hidden 640)
```

The counts are complete. Without `--all` at most 200 dependents are listed,
nearest first, and `read-first` names the file and its first 10 direct dependents.

## invariants Output

```scheme
(invariant-violation <mod> :expected snake_case :actual PascalCase :lang py :pkg <repo>)
(invariants-summary :violations 12 :checked 3587)
(invariants-ok :checked 3587)
```

## layers Output

```scheme
(layer-group api :count 144)
(layer-member api <mod> :lang ts)
```

## find_function / find_type Output

```scheme
(fn-def <mod>::<name> :line 42 :lang ts :file <path> :kind function)
(fn-def <mod>::<cls>.<method> :line 42 :lang py :file <path> :kind method)
(type-def cls <mod>::<Name> :line 10 :lang ts :file <path> (:bases A) (:methods m1 m2))
(type-def ifc <mod>::<Name> :line 10 :lang ts :file <path>)
```

## find_callers / find_calls Output

```scheme
(callers-target <mod>::<fn> :line 42 :lang ts :file <path>)
(caller <src>::<fn> -> <tgt>::<fn> :lang ts :file <path> :lines L1 L2 :lsp)
(caller-module <src> -> <tgt> :via imports :depth 1 :lang ts :file <path>)
(calls-source <mod>::<fn> :line 42 :lang ts :file <path>)
(calls-target <src>::<fn> -> <tgt>::<fn> :lang ts :file <path>)
(calls-module <src> -> <tgt> :via imports :depth 1 :lang ts :file <path>)
```

## find_callchain Output

```scheme
(callchain-step 0 <mod>::<fn> :lang ts :file <path>)
(callchain-step 1 <mod>::<fn> :lang py :file <path>)
(callchain-summary :length 3 :hops 2)
(callchain-module-step 0 <mod> :lang ts :file <path>)
(callchain-module-summary :length 2 :hops 1)
(callchain-none "srcFn" -> "tgtFn" :max-depth 5)
```

## grep_functions Output

```scheme
(grep-fn <mod>::<fn> :line 55 :lang ts :file <path> :match "matched line text")
(grep-functions-summary :matches 12 :pattern "pattern")
(grep-functions-empty "pattern")
```

## diff_functions Output

```scheme
(diff-fn <mod>::<name> :kind fn :line 42 :lang ts :file <path>)
(diff-functions-summary :changed 5 :files 3 :ref "HEAD")
(diff-file <path> :hunks 2)
(diff-functions-no-match :files 3 :ref "HEAD")
(diff-functions-empty "HEAD")
```

## query / repl Output

```scheme
(result <module-or-path>)
(result-empty)
(result-count 42)
```

## :lang Values

| Value | Language | Naming | Example |
|---|---|---|---|
| `py` | Python | snake_case fns, PascalCase classes | `drive_path_to_entry`, `DrivePath` |
| `ts` | TypeScript | camelCase fns, PascalCase classes | `calculateTotal`, `OperativesService` |
| `tsx` | TSX/React | PascalCase components, camelCase hooks | `FileBrowser`, `useQuery` |
| `rs` | Rust | snake_case modules and functions | `drive_path`, `DrivePath` |
| `cs` | C# | PascalCase types | `DriveController` |
| `java` | Java | PascalCase types | `DriveService` |
| `go` | Go | PascalCase exports, camelCase locals | `DrivePath`, `loadPath` |
| `rb` | Ruby/Rails | snake_case files, CamelCase constants | `order_payment`, `OrderPayment` |

## Architecture Layers

| Layer | Path patterns | Role |
|---|---|---|
| `domain` | `/domain/`, `/entities/`, `_schema` | Pure business logic |
| `api` | `/api/`, `/routes/`, `/controllers/` | HTTP/RPC handlers |
| `service` | `/services/`, `/application/` | Business orchestration |
| `infra` | `/repositories/`, `/db/`, `/infra/` | Database, storage, messaging |
| `shared` | `/commons/`, `/utils/`, `/types/` | Cross-cutting utilities |
| `test` | `/tests/`, `.test.`, `.spec.` | Test files |
| `ui` | `/components/`, `/pages/`, `/hooks/` | Frontend UI |
| `config` | `/config/`, `/env/` | Configuration |
| `other` | (no match) | Unclassified |
