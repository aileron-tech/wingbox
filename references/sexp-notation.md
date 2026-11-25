# S-expression Output Notation

Every output line is a self-contained fact, except the answers of `impact` and
`usages`, which are one form over several lines. Each fact that references a
module carries a compact `:lang` value such as `py`, `ts`, `rs`, `php`,
`blade`, or `rb`.

## Forms of an answer

The query commands and `advice`, `align`, `place`, `compare` print
S-expressions by default, complete. One option chooses another form; two of
them at once are an invalid argument:

- `--human`: the text for people. `impact`, `usages`, `advice`, `align`,
  `place` and `compare` print readable text; the other query commands print
  their fact lines. Long lists stop at a limit unless `--all`.
- `--json`: one JSON object on one line. `impact` and `usages` have their own
  keys (see their sections). Every other command prints `{"facts": [...]}`,
  one object per fact line in the same order:
  `(dep a b :lang rs :via imports :depth 2 :cycle)` is
  `{"fact": "dep", "args": ["a", "b"], "lang": "rs", "via": "imports", "depth": 2, "cycle": true}`.
  Atoms outside keywords are `args`; a keyword takes the value after it, or is
  `true`; `:suggestions`, `:others` and the `:via` of `analyze-dependent` are
  always lists; a list `(:methods a b)` is `"methods": ["a", "b"]`; the name
  lists of an import (`(:use A B :of 9)`) are collected under `names` as
  `{"way": "use", "through": "", "names": ["A", "B"], "of": 9}`; unquoted
  digits are numbers; a comment line is `{"note": "..."}`.
- `--sexp`: what versions up to 0.13 printed with it, byte for byte, for a
  script written for them: the flat facts of `impact` and `usages` (see
  "Flat facts" in their sections), the facts of `advice`, `align`, `place` and
  `compare`, and the fact lines of the other query commands with their list
  limits.

`init`, `learn`, `install`, `uninstall` and `update` talk to a person: they
print text and print their facts with `--sexp`.

**Module ID**: relative path from workspace root, no extension, no `/index`. Example: `drive/modules/drive_db/api/drive_db_api.ts` -> `drive/modules/drive_db/api/drive_db_api`. A Blade template drops `.blade.php` (`resources/views/layouts/app.blade.php` -> `resources/views/layouts/app`).

## Raw Graph Facts (in .wingbox/index.sexp)

```scheme
(code-graph :schema 1 :files <n> :nodes <n> :edges <n> :violations <n>)
(built-from :tree <digest> [:commit <sha>] [:wingbox <version>])
(node <id> <lang> "<purpose>" :naming <convention> :pkg <repo>)
(fn <mod> <name> <line> :eid <uuid>)
(cls <mod> <name> <line> :eid <uuid> (:bases A) (:deco D) (:methods m1 m2))
(mod <mod> <name> <line> :eid <uuid> (:methods m1 m2))
(ifc <mod> <name> <line> :eid <uuid> (:methods m1 m2))
(typ <mod> <name> <line> :eid <uuid>)
(enm <mod> <name> <line> :eid <uuid>)
(imports <src> <tgt> [:via-base] [:association|:concern|:job|:mailer|:spec|:view|:extends|:include|:component|:livewire|:event] [:item [:candidate]] [:lsp] [:line N | :lines N M ...] [(:use|:reexport|:glob|:member|:macro ...)])
(calls-api <src> <tgt> :via-ifc [:lsp])
(data-flow <src> <tgt> [:validation-module :evidence "<kind> <name>"] [:pipeline])
(registry-wires <src> <tgt> [:config <path>])
(refs <src> <tgt>::<Entity> [:callback] [:lsp] [:line N | :lines N M ...])
(calls <src>::<fn> <tgt>::<fn> :lines L1 L2 [:registry] [:lsp])
```

`built-from` names what the graph was built from. `:tree` is a digest of the
path and the text of every source file in the graph, as the parsers read it
(without a byte order mark, every line ending `\n`): the same files give the
same digest on any machine and with any line endings, and a changed character
gives another. `:commit`
is the commit checked out when Git reported no change and no untracked file
under the project folder at build time; with any change it is left out.
`:wingbox` is the version that built the graph, when Wingbox runs as an
installed package.

`:line N` (or `:lines N M ...`, ascending, for several statements), the last
mark of an `imports` or `refs` fact (the lists of names follow it), is the
1-based line, in the file of `<src>`, where the statement
that states the fact starts: an `import`, `from ... import`, `require`, `use`,
`using` or `uses` statement, an import directive, an `import()` or `require()`
call with a literal name, a `mod` declaration. A fact no single statement
states carries no line: an import followed through a module facade or found by
a naming convention (a constant, a type of the same package or namespace, a
view or route name), a `refs` fact from a class naming its base or from a use
in the code (those positions are in the use file: `usages`, `entity
--positions`), and every `data-flow` and `calls-api` fact. A graph built
before lines were stored reads the same way, without them.

The query commands print these facts as they did before the lines existed;
`module <id> --lines` and `search <pattern> --lines` print them with their
lines (`search --lines` also matches the `built-from` fact).

`:item` (Rust): the file names an item that a module facade re-exports
(`pub use child::*`, `pub use child::Name`); the edge goes to the file that
defines the item, next to the plain edge to the facade, and `impact` follows it.
With `:candidate` the file imports the facade with a glob (`use m::*`) and its
body names the item. A Rust import edge also lists the names the builder
followed to its target, one list per way they arrive, at most three names and
`:of <count>` when there are more: `(:use A B)` items of the module named in
a `use` or a path, `(:reexport "crate::m" A)` items the facade `crate::m`
re-exports, `(:glob "super" A)` names used after `use super::*`,
`(:member "Type" new)` members an impl block of the target defines,
`(:macro m)` an exported macro. A member named through a type path (`Type::member`,
`Self::member`) gives the same edge to the file whose inherent `impl` block of
the type defines the member.

`data-flow ... :validation-module`: `<src>` imports `<tgt>`, and the syntax tree
of `<tgt>` defines a validator. `:evidence` is what was found there, the first
in the file: `base pydantic.BaseModel`, `decorator pydantic.field_validator`,
`call z.object from zod`, `tag validate` (Go struct tag), `annotation
jakarta.validation.constraints.NotNull`, `attribute Required with using
System.ComponentModel.DataAnnotations`, `call validates in User` (Ruby),
`base Illuminate\\Foundation\\Http\\FormRequest` (PHP; a quoted value writes a
backslash twice), `argument validator of TextFormField` (Dart). The name is one the file binds by an import of the
validation library, or writes in full; `with import` and `with using` name a
wildcard import or a using directive that stands beside the bare name. Ruby
and Dart bind no names, so their evidence is the construct alone. A comment, a
string, an unused import and a type-only declaration (`TypedDict`,
`NamedTuple`, a dataclass) give no mark. The mark says nothing about `<src>`:
it is not shown to call the validator. Graphs written up to 0.11.0 carry
`:validated` instead, set from a text match; it is read as no mark.

Modules loaded at run time (Python):

- `importlib.import_module("<name>")`, `import_module(".<name>", __package__)`
  and `__import__("<name>")` with a literal name are `imports`.
- `registry-wires`: `<src>` loads `<tgt>` by a name it does not spell. The
  module name is a literal package path and one variable
  (`f".plugins.{name}"`, `"app.plugins." + name`); the variable is filled with
  the strings of the JSON, YAML and TOML files in the folder of `<src>`, and
  each string that names a module of that package is a wire. `:config` is the
  file that names it.
- `calls ... :registry`: inside a loop, `<src>::<fn>` calls a method on an
  object that a module with wires returns for the loop variable
  (`cls = registry.get(name); obj = cls(); obj.run(x)`); the call goes to each
  wired module that defines the method. Found from the syntax tree, not by a
  language server.
- `data-flow ... :pipeline`: such a call replaces one of its own arguments
  (`data = stage.process(data, config)`), so each module passes its output to
  the next one of the same configuration list, in the order of the list.
  `deps`, `rdeps` and `impact` do not follow it: the next step depends on the
  previous one through the loop, not the other way round.

Architecture of the parts (written by `wingbox build`; see `wingbox init` below):

```scheme
(arch-part <part> :style <style> :state <state> :origin detected|config|declared :level system|container|component :kind "<kind>" :files <n> [:source "<citation>"] [:declared "<file>"])
(arch-deny <part> <from-layer> <to-layer> "<reason>")
(arch-known <part> <src> <tgt> "<baseline file>" [:owner "<who>"] [:reason "<why>"] [:expires <YYYY-MM-DD>])
(arch-known-rule <part> <src> <tgt> "<from -> to>")
(arch-not-parsed <part> "<language>" :files <n>)
(arch-rule-files :config <digest> :baseline <digest> :learned <digest>)
(arch-capability <part> <capability> [:allowed (<layer> ...)] [:forbidden (<layer> ...)] [:fix "<text>"])
(arch-capability-unread <part> "<language>" :files <n>)
(arch-pattern <part> <pattern>)
(capability <mod> <capability> :line <n> :evidence "<source line>" [:suspect])
(capability-known <part> <mod> <capability> "<baseline file>" [:owner "<who>"] [:reason "<why>"] [:expires <YYYY-MM-DD>])
(layer <mod> <layer> :part <part> :style <style> :rule "<rule>" :match exact|spelling|config|file|declared|test|none)
```

- `<part>` is a folder relative to the repository root, `"."` for the root.
- `:state`: `determined`, `undetermined` (candidates too close, or too weak),
  `none` (no layer style), `not-judged` (fewer than 12 source files),
  `declared` (the part's own rules were read), `declared-unread`, `configured`
  (from `.wingbox/config.yaml`).
- `arch-deny`: a boundary rule of the part's style, or, for declared layers,
  "a layer may not import a higher one". Rules apply between two modules of
  the same part only.
- `arch-known`: a pair the project already lists in its own baseline of
  known exceptions (for example `layers.known-upward-imports.json`), or in
  `.wingbox/baseline.sexp`, with the owner, the reason and the end date its
  entry states. A query compares `:expires` with the current day: past it
  (or with a date that is no day of the calendar) the pair is not known any
  more, whatever another entry of the same file says.
  `(arch-known-rule <part> <source> <target> "<rule>")` is the rule an entry
  of `.wingbox/baseline.sexp` states: that entry accepts a break of that rule
  only. A pair with an entry that states no rule has no such fact.
- `(arch-not-parsed <part> "<language>" :files <n>)`: source files of the
  part (`-`: of no part) in a language no parser reads; they are in no
  module. `(arch-capability-unread <part> "<language>" :files <n>)`: files of
  a part with capability rules in a language the capability detector does
  not read. `(arch-rule-files :config <digest> :baseline <digest> :learned
  <digest>)`: what the build read of `.wingbox/config.yaml`,
  `.wingbox/baseline.sexp` and the learned layers of
  `.wingbox/decisions.sexp` (`-`: missing); `boundaries` compares them with
  the files.
- `layer`: `:rule` says what set the layer (`folder routes/ -> api`,
  `file *.tsx -> ui`, `route file page.* in src/app/`, `<file>: layer core`,
  `test file`); `:match` says how the folder name matched the style's word.

## init Output

```scheme
(arch-part <part> :level <c4 level> :kind "<kind>" :files <n> :style <style> :state <state> :origin <origin> [:score 0.81 :coverage 0.47] [:name "<style (author)>"] [:source "<citation>"])
(arch-evidence <part> <style> "<folder/ or file pattern>" :files <n> :group <group> :match exact|spelling|file [:via "<word>"])
(arch-candidate <part> <style> :score 0.34 :coverage 0.20 :status candidate|below :source "<citation>")
(arch-overlay <part> cqrs :score 0.89 :coverage 0.12)
(arch-layer <part> <layer> "<folder>" :files <n> :match <step> [:via "<word>"])
(arch-declared <part> "<file>" :read yes :layers 22)
(arch-declared <part> "<file>" :read no :reason "<why>")
(arch-baseline <part> "<file>" :entries 112)
(arch-target <part> <layer> "<folder>" [:block <id>] :present "<real folder>" | :proposed :source "<citation>")
(arch-not-judged <part> :files <n> :kind "<kind>")
(arch-config-written ".wingbox/config.yaml")
(arch-config-kept ".wingbox/config.yaml" :reason "<why>")
```

The text that `wingbox init` prints for people is a printer over these facts.
`arch-evidence` lists the folders (or file patterns) of each detection group of
the best style with the number of source files below them; `arch-candidate`
lists the other styles that scored: `:status candidate` passed the thresholds
(score 0.6, coverage 0.25), `:status below` did not or lost to a style that
wins over it. `arch-layer` is the folder -> layer mapping
written to `.wingbox/config.yaml` (`routing-root` for a route-tree frontend).
`arch-target` is the full layout of the part's style: one folder per layer,
further folders (use cases, ports) and the domain building blocks (`:block`
entities, value-objects, aggregates, domain-events, domain-services,
repository-interfaces, specifications; Evans 2003, Vernon 2013). `:present`
gives the folder the part already has, `:proposed` marks a folder that would be
new, at the path the style gives. `init` creates no folder.

## init in an empty folder

```scheme
(init-languages "typescript javascript python ...")
(init-choice <n> <style> :language <lang> :from siblings|home|library [:note "same as packages/a"] :name "<style name>" :source "<citation>")
(init-needs "--language <lang> --style STYLE --yes")
(init-new-project :language <lang> :style <style> :from "<--style|siblings|home|library>" :config ".wingbox/config.yaml")
(init-scaffold "<folder>")
```

Without a terminal `init` prints the choices and exits with 2; `--language`,
`--style` and `--yes` make it non-interactive. Choices are ordered by
precedence: siblings (the style of the parts in the same language of the
repository's `.wingbox/config.yaml`), then the `$HOME/.wingbox/config.yaml`
favourite (`defaults: {typescript: onion-clean}`), then the library. A style
file in `$HOME/.wingbox/styles/` replaces the built-in one of the same name.
`--scaffold` creates the empty folders of the target layout, no file.

## Advice (init, analyze, impact, REPORT.md)

```scheme
(advice <part> :id <12 hex> :kind move|fix-violation|missing-folder :target "<path>" :style <style> :rule "<rule>" :reason "<what to do>" :evidence "<why>")
(advice-none <part> :reason "<why there is no advice>")
```

- `move`: a file that dependents of its own part import against the rules
  (at least 2, and at least half of its dependents); `:reason` names the
  nearest layer they may all import, `:evidence` the counts by layer.
- `fix-violation`: a violation (both layers set by rules) with the fix the
  style prescribes; a pair the project already lists in its own baseline is
  not repeated.
- `missing-folder`: a proposed folder of the target layout.
- `:id` is stable: a hash of part, kind, target and rule. A part whose style
  is not determined gets `advice-none`. `analyze` and `impact` print the
  advice that concerns the module; graph facts `(arch-rank <part> <layer>
  <rank>)`, `(arch-target ...)` and `:fix` on `arch-deny` feed it.

## Decisions store (.wingbox/decisions.sexp) and `wingbox advice`

```scheme
(advice-given <id> :part <part> :kind <kind> :target "<path>" :rule "<rule>" [:layer <layer> :destination "<folder>"] :at <date> :graph <hash>)
(advice-outcome <id> :followed :at <date>)
(advice-outcome <id> :done-differently :actual "<path or what changed>" :at <date>)
(build-run :graph <hash> :at <date>)
(advice-outcome <id> :ignored :runs <n> :at <date>)
(advice-outcome <id> :accepted :by user :at <date>)
(advice-outcome <id> :rejected :by user :reason "<why>" :at <date>)
(learned-layer <part> "<folder>" <layer> :from <id> :at <date>)
(learned-removed <part> "<folder>" :by config :at <date>)
```

Only changes of state are logged: one `build-run` per build, new advice,
outcomes, and `:ignored` once, in the build where an open item reaches 3
builds (it is hidden from then on; an accepted item never is). With a
`.wingbox/config.yaml`, the config states the layers: learned folders apply
only without one, and a learned folder the config does not list is recorded
as `learned-removed`. An append-only log, written by `wingbox build` and by `wingbox advice
accept|reject`; commit it to share it. A label that rests on a learned layer
says so: `(layer <mod> service ... :rule "folder core/ -> service (learned
from <id>)" :match learned :learned-from <id>)`. `wingbox advice`
prints the open `(advice ...)` items (rejected ones and those ignored for 3
builds are left out) and `(advice-none ...)`; `--learned` prints the
`learned-layer` facts. A build whose graph equals the one of the build before
is not one of the 3. Advice of kind `fix-violation`, `fix-capability` or
`fix-pattern` is never marked ignored: it stays until its finding is fixed,
accepted in the baseline or rejected, and `wingbox advice reject <id>` takes
it only with `--reason TEXT` (exit status 2 without).

## align Output

```scheme
(align-part <part> :style <style> [:from <current style>] :placed <n> :files <n> :following <n> :dependencies <n> :present "<layers>" :missing "<layers>")
(align-gap <part> :kind unplaced-folder|missing-layer|rule :target "<folder, layer or from -> to>" :count <n> [:example "<a -> b>"])
(align-none <part> :reason "<why>")
(align-style <part> :style <closest style> :score <s> :coverage <c> :runner-up <style> :runner-up-score <s>)
(align-placed <part> :layer <layer> :folders "<folders>" :by style)
(align-learned <part> :folder "<folder>" :layer <layer> :by suffix|name|imports :why "<evidence>")
(align-learned-none <part> :reason "declared rules: nothing is guessed, the baseline comes from them")
(align-config-written ".wingbox/config.yaml")
(align-baseline ".wingbox/baseline.sexp" :entries <n>)
```

`.wingbox/baseline.sexp` holds `(baseline <part> <src> <tgt> :rule "<from -> to>"
:from align|"<project baseline file>")` and `(baseline-capability <part> <module>
<capability> :line <n> :from align)`; the build reads it and the pairs become
`violation-known`, the capabilities `capability-violation-known`. Each entry
may end with `:owner "<who>" :reason "<why>" :expires <YYYY-MM-DD>`, written
by the person who accepts the break; `align --learn` keeps these fields of an
entry when it writes the file again and never adds them itself.

## compare Output

```scheme
(compare-part <part> :language <lang> :style <style> :layers "<layers>")
(compare-diff <lang> :kind missing-layer|folder-meaning|style :detail "<what differs>")
(advice <part> :id <id> :kind deviates :target <part> :style <style> :rule "majority <style>" :reason "..." :evidence "...")
```

## learn Output

```scheme
(learn-mode agent|human :because "--agent|CLAUDECODE=1|GEMINI_CLI=1|stdin or stdout is not a terminal|terminal")
(learn-part <part> :style <closest> :score <s> :runner-up <style> :runner-up-score <s>)
(learn-layer <part> <layer> :means "<placement text or note>")
(learn-placed <part> <layer> "<folders>")
(learn-known <part> "<folder>" <layer> :because "<from learned.sexp>")
(learn-folder <part> "<folder>" :files "<2-3 files>" :imports "<layer count ...>" :imported-by "<layer count ...>" :guess <layer>|- [:weak "<how the style matched it>"] [:breaks <n>])
(learn-more <part> :folders <n>)
(learn-nothing <part> :reason "...")
(learn-question "...") (learn-rule "...") (learn-answer-format "...") (learn-apply "...")
```

The answer (one per line): `(style <part> <style>)` at most once per part,
`(layer <part> <folder> <layer> :because "<files read>")` (a folder is a name
below the source root, `commands`, or a nested path, `lib/hooks`: a path
decides for the files below it before any name rule), `(unknown <part>
<folder> :because "...")`. `wingbox learn --apply` rejects the whole answer
with `(learn-error "...")` lines when any line is invalid; otherwise it prints
`(learn-learned ...)`, `(learn-kept ...)` for folders the config already
states, `(learn-result :build ok :violations <n>)` and `(learn-saved ...)`.
Besides the folders the style does not place, the task lists nested folders
whose files take part in rule breaks or rest on a guess (`:breaks`), at the
deepest folder that holds them, most breaks first, 25 per part
(`learn-more` counts the rest). The rebuild after `--apply` prints nothing
unless `--verbose`. `.wingbox/learned.sexp` keeps each accepted answer: `(learned-answer <part>
:at <date> :mode agent|human [:agent "<name>"] :style <style> :score <s>)`
followed by `(learned-entry <part> "<folder>" <layer>|unknown :because "..."
:files <n>)`; a folder learned before is not asked again unless `--again` or
its files more than doubled.

## place Output

```scheme
(place <part> :layer <layer> :folder "<folder>" :present|:new|:not-stated :style <style>|declared :rule "<placement or declared layer>" :may-import "<layers>|not stated" [:where "<the style's words>"] :examples "<2-3 files>")
(place-none <part> :reason "<why>")
```

The request is matched word by word against the `[[placement]]` texts and the
layer words of the part's style (name, folder words, file patterns); `:new`
marks a folder of the target layout that does not exist yet; `:examples` are
real files of the folder. In a part with declared rules the folder whose words
match the request is named with the project's own layer of its files.

## Use File (.wingbox/index.uses.sexp, next to the graph)

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
query that needs positions has the builder fill them in from the build cache
and rewrite the file with positions: every use, also inside its own file. A
build without changed files keeps the file as it is, positions included.

`.wingbox/index.uses-cache.json`, next to it, keeps per file the content hash,
the full use walk and the resolved uses, with a digest of the surface of the
repository (every path, and the definitions and bindings of the files with a
use index) and the import lookups. Filling positions walks again only the
files whose content changed, and while the surface is the same resolves only
those; a changed surface (a definition, an import or a file added or removed)
resolves every file again. A change in tsconfig, package or Cargo settings
alone is not part of the surface: rebuild with `--full` after one.

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
(analyze-dataflow <mod> <tgt> :lang py [:validation-module :evidence "<kind> <name>"])
(refs <src> <tgt>::<Entity>)
(analyze-dependent <mod> <src> :lang tsx :via imports refs :file <path>)
(analyze-more <mod> :dependents 298 :rerun "analyze <mod> --all")
(dep <root> <tgt> :lang py :via imports :depth 1)
(key-entity <mod> <Name> :lang py :kind cls :line 32 :refs 7 :modules 4)
(key-entity-ref <Name> <ref-mod> :lang ts)
(key-entity-more <mod> <Name> :modules 185 :rerun "analyze <mod> --all")
(boundary-file <mod> <file-path>)
(layer <mod> <dep-name> :layer domain :style layered :rule "folder models/ -> domain")
(analyze-summary <mod> :entities 31 :imports 1 :dataflows 0 :refs 1 :dependents 50 :boundary-files 2)
```

An `analyze-dataflow` line carries the marks of the stored `data-flow` fact and
no other; two facts of one pair with different marks give two lines.

Paths are relative to the repository root. Each dependent file is named once, on
its `analyze-dependent` line; `boundary-file` names the module and the files it
imports. The answer is complete. With `--human` (or `--sexp`) it names at most
200 dependents and 20 modules per key entity unless `--all`, and an
`analyze-more` or `key-entity-more` line gives the number left out; the counts
in `key-entity` and `analyze-summary` are always complete.

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
(usages <Name> cls :at <path>:<line> :uses 7 :files 2
 (<path> import 1:17 2:24 extends 4:13 call 8:11)
 (<path> unresolved-call 2:14)
 (<path> reference))                    ; a language without use positions
(usages <Name>.<method> method :at <path>:<line> :uses 1 :files 1 :unresolved 1 ...)
(usages-ambiguous <Name> :definitions 2
 (<path>::<Name> cls <line>)
 (<path>::<Name> fn <line>))
```

One form per answer. The head names the symbol, the kind of its definition and
where it is defined; each file is named once and its uses are grouped by kind,
each kind followed by its `line:col` positions. Kinds: `import`, `re-export`,
`call`, `type`, `extends` (extends or implements), `reference`,
`unresolved-call`. `:unresolved` is said only when it is not 0. A bare name
with several definitions answers with the candidates, each written as it can
be asked. The form is complete.

`usages --json` prints `{"usages": {"symbol", "kind", "file", "line", "uses",
"files", "unresolved", "rows"}}` with one row `{"file", "line", "column",
"kind"}` per use; an ambiguous name has `"error": "ambiguous"` and
`"candidates"`, an unknown one `"error": "no-match"`. `usages --human` prints
the same answer as text (the target, the counts, then each file with its
`line:col` and kind), at most 200 uses unless `--all`.

### Flat facts (`usages --sexp`)

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

What versions up to 0.13 printed with `--sexp`, kept as it was: one fact per
line, module IDs, uses sorted by file, line and column, at most 200 unless
`--all`.

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
(violation <src> <tgt> :src-layer domain :tgt-layer api :via imports :lang ts :part <part> :style layered :rule "domain -> api" [:known "<baseline file>"] :src-file <path> :tgt-file <path> "<reason>")
(violation-type-only <src> <tgt> :src-layer domain :tgt-layer infra :via imports :lang ts :part <part> :style layered :rule "domain -> infra" :src-file <path> :tgt-file <path> "<reason>")
(violation-candidate <src> <tgt> :src-layer infra :tgt-layer api :via imports :lang ts :part <part> :style layered :rule "infra -> api" :src-file <path> :tgt-file <path> "<reason>")
(violation-known <src> <tgt> ... :known "<baseline file>" [:owner "<who>"] [:reason "<why>"] [:expires <date>] ... "<reason>")
(violation <src> <tgt> ... :rule "domain -> api" :expired <date> [:owner "<who>"] ... "<reason>")   ; its baseline entry is past its end date
(boundaries-summary :violations 2 :known 93 :type-only 6 :candidates 7 :checked 3587 [:no-layer <n>])
(boundaries-ok :checked 3587 [:no-layer <n>])
(boundaries-rules-changed :files (<rule file> ...) :hint "rebuild: wingbox build")
(boundaries-not-checked :part <part> :reason declared-rules-not-read|style-undetermined|no-layer-style|too-few-files|in-no-part|no-file-in-a-layer|not-parsed-language [:files (<rule file> ...)] [:languages (<language> ...)] :files-in-part <n>)
(boundaries-no-layer :part <part> :files-in-part <n>)
(boundaries-partial :checked <n> :not-checked <m> [:no-layer <n>])
; with --strict only, after the lines above:
(boundaries-checked :part <part> :style <style> :modules <n>)
(boundaries-result :status pass|fail|incomplete|error :checked <files> :not-checked <files> :violations <n> :known <n> [:no-layer <files>] [:reason graph-stale|rules-changed|part-not-checked|not-parsed-language|no-files-checked|no-graph|invalid-graph|invalid-argument])
```

`boundaries-ok` is printed only when every part with files was checked against
rules that were read or determined and nothing breaks a rule (a layer rule, a
capability rule or a pattern). What was not checked is listed first as
`boundaries-not-checked`, and `boundaries-partial` replaces `boundaries-ok` (it
follows `boundaries-summary` when there are violations): a part whose declared
rules were not read, whose style is undetermined, that has no layer style or
too few files to judge; the files in no part (`in-no-part`, part `-`); a part
with rules in which no file is in a layer (`no-file-in-a-layer`); the source
files of a part in a language no parser reads (`not-parsed-language`, with
`:languages`; these files are in no module, `:files-in-part` is their number).
`:checked` counts the files a rule places: in a layer, or as a test. A file of
a checked part that no rule places is not judged and not counted as checked:
`boundaries-no-layer` gives the number per part and `:no-layer` the total, on
every line that carries `:checked`; the keyword is absent when the number is
zero. `boundaries-rules-changed` comes first when `.wingbox/config.yaml`,
`.wingbox/baseline.sexp` or the learned layers of `.wingbox/decisions.sexp`
differ from what the build read (`arch-rule-files`): the facts are older than
the rules, and no `boundaries-ok` is printed. Exit status: 2 when a part's
declared rules were not read (that part was not checked) or the scope matches
no module, else 1 when a violation, a capability violation or a pattern break
was found, else 0.

`boundaries --strict` is the form for a gate. It prints the same lines, then a
`boundaries-checked` fact for each part that was checked and `boundaries-result`
as the last line of standard output; `boundaries-ok` is printed only when the
result is `pass`. Its exit status follows `:status`: 0 `pass` (every part with
files was checked, at least one file was checked, the graph is not known to
be older than the files or the rule files, nothing breaks a rule), 1 `fail`
(a violation, a capability violation or a pattern break), 2 `incomplete`, 3
`error` (no graph, a graph that cannot be read, an invalid argument). The
`:reason` of `incomplete` is the first that applies: `graph-stale` (the graph
is known to miss changed files: the refresh is off, failed or timed out),
`rules-changed`, `part-not-checked` (a `boundaries-not-checked` line with one
of the first five reasons), `not-parsed-language`, `no-files-checked` (a part
with no file in a layer, or no file checked at all). `:known` counts the
breaks accepted in a baseline and `:no-layer` the files no rule places; they
do not change the status.

`violation`: both layers come from rules. `violation-known`: the same, but the
pair is listed in a baseline (the project's own, or `.wingbox/baseline.sexp`
written by `wingbox align --learn`); it is not counted as a violation, and the line carries the
owner, the reason and the end date when its entry states them. An entry of `.wingbox/baseline.sexp`
whose `:expires` day has passed, or is not a `YYYY-MM-DD` date, no longer makes the pair known: it is
a `violation` again, marked `:expired <date>`, and so is a capability finding
(`capability-violation ... :expired <date>`). `violation-type-only`:
a TypeScript import of types or interfaces only (`import type`, or every imported
name is a type or an interface of the target); the compiler erases it, so it is
not counted as a violation, but a change of the type still reaches the importer.
`violation-candidate`: a layer is only guessed from the role of a file.

What a layer may do (capability rules) follows, when a file breaks one:

```scheme
(capability-violation <file> :capability sql :layer service :allowed (infra) :line 42 :evidence "<source line>" :part <part> :style layered [:pattern domain-imports-orm] [:fix "<the style's fix>"])
(capability-violation <file> :capability framework :layer domain :forbidden (domain) :line 3 :evidence "..." ...)
(capability-violation-known <file> ... :known .wingbox/baseline.sexp [:owner "<who>"] [:reason "<why>"] [:expires <date>] ...)
(capability-candidate <file> :capability sql ... :evidence "QUERY = \"SELECT ...\"" ...)
(pattern-violation <file> :pattern controller-skips-service :target <repository file> :part <part> :style <style> "<reason>")
(pattern-violation <file> :pattern service-builds-http-response :line 12 :evidence "return JSONResponse(...)" ...)
(capability-summary :violations 3 :known 0 :candidates 1 :patterns 2 :checked 707)
(capability-not-read :part <part> :language <language> :files <n>)   ; before the lines above
```

Capabilities (`scripts/architecture/capabilities.py`), found by syntax in
Python, TypeScript and JavaScript, PHP, Ruby, Java and Kotlin, C# and Go:
`sql` (a string literal that starts a SQL statement passed to a query call,
query-builder and ORM queries such as `session.query`, `prisma.<model>.findMany`,
`DB::select`, `find_by_sql`, `createQuery`, `FromSqlRaw`, `db.Query`),
`db-client` (driver and ORM imports), `http-client`, `framework` (web
framework imports), `http-response`, `env`, `filesystem`, `process`. Each
style states `allowed_in` or `forbidden_in` layers per capability (its
`[capabilities]` table); `capabilities:` of a part in `.wingbox/config.yaml`
replaces a rule. One line per file and capability, at the first line that
shows it. `capability-candidate`: a SQL literal no query call on the line (or
the call opened just above) receives, or a layer guessed from the role of the
file. A composition root, tests and files in no layer are not judged (a
composition root or a test that a file of a layer imports is judged like the
files of its folder); a part with declared rules has no capability rules.
`capability-not-read`: the part has capability rules and files in a language
the detector does not read (Rust, Dart, Pascal, C and C++), so nothing is
said about what those files do; the status does not change. The detector
reads the first 2000 characters of a line. Pattern checks of the style:
`controller-skips-service` (a controller imports a repository and the style
has a service layer), `service-builds-http-response`, `domain-imports-orm`
(set on the db-client violation of a domain file).

## impact Output

```scheme
(graph :built 2026-01-31T09:15:00Z :files 359 :stale 0)   ; only when several files are asked
(impact <file> :direct 2 :total 12
 (1 <file> (:use A B C :of 9)
    <file>)
 (2
  (:through <file>
   <file> (:use A)
   <file> :more 1
   <file> (:glob "super" HarEntry))
  (:through <file>
   <file> :via calls-api))
 (read-first <file> <file> <file>))
(error no-match <file> :suggestions <file> <file>)        ; in place of the answer of a file not in the graph
```

One `impact` form per asked file, in the order asked; `wingbox impact a.rs b.rs
2` asks for several (a last argument that is a number is the depth). Paths are
files relative to the repository root. `:direct` and `:total` are the counts of
depth 1 and of all depths. Rows are grouped by depth and, below depth 1, by the
file of the previous depth they are reached through, named once per group. A
bare path is a plain import; a path is followed by `:via <relation>` when the
relation is not an import (`generated`, `runs`, `reads`), by `:more N` when it
depends on N other files of the previous depth as well, by `:basis declared`
or `:basis inferred` when the source text does not state the edge, and by the
name lists of its edge. `read-first`
names the file and its direct dependents. With `--only tests` the form says
`:only tests` and holds the test files alone; in a graph with `(target ...)`
and `(ctest ...)` facts (the build targets and tests of the CMake files) it
is followed by `(affected-targets <file> <target> ...)` and `(affected-tests
<file> <test> ...)`. Before the form of a C or C++
source file stands `(impact-paired-header <file> <header>)` (the rows marked
`:basis paired-header` include that header) or `(impact-no-includers <file>)`.
`impact --diff [ref]` ends with one `(changed <file>::<name> :line N :used-by
(<file> ...))` fact per changed function, with `:targets (...) :tests (...)`
after `:used-by` under `--only tests` in a graph with build facts. The form is complete: nothing is cut.
The `(advice ...)` facts that concern a file follow its form.

`impact --summary` prints `(impact-summary <file> :direct N :total M :depth D
[:only tests]` in place of that form: `(depths N ...)` the dependents at each
depth, `(kinds :generated N :tests N :entry-points N :other N)`, `(folders
(<folder> N) ... [:more N])` the eight folders with the most dependents,
`(first <file> ... [:more N])` at most twenty files, `(build :targets N :tests
M)` under `--only tests` in a graph with build facts, and `(full "<command>")`,
the command of the complete list. With `--diff` each changed function is one
`(changed-summary <file>::<name> :line N :used-by K [:targets T :tests M])`.

`graph` says when the graph file was written (UTC), how many files it holds
and how many changed files it is known to miss: 0 after the refresh that every
query starts, the number of changed files with `WINGBOX_AUTO_REFRESH=0`, and
`unknown` when nothing checked it (a graph given with `--map`). The exit
status is 1 when a file is not in the graph.

`impact --json` prints the same answer as one object: `{"graph": {"built",
"files", "stale"}, "impact": [{"file", "direct", "total", "rows", "read_first",
"advice"}]}`, each row `{"file", "depth", "via", "through", "more", "reasons"}`, with
`"basis"` (`declared` or `inferred`) when the code does not state the edge, and each reason `{"way",
"through", "names", "of"}`; a name that matches nothing is `{"file", "error",
"suggestions"}`, a file of the repository that is not in the graph `{"file",
"error", "reason"}` with `language` where it has one. `impact --human` prints the text
for people, which lists at most 200 dependents (nearest first) and the first
10 read-first files unless `--all`.

Each row says why the file is on the list. Below depth 1, `:through` is the
file of the previous depth it is reached through (the first in the order of
the list) and `:more` the number of other files of that depth it also depends
on; `:via` and the name lists are those of its edge to that file (to the asked
file at depth 1). The name lists are the ones of the
import edge (see `imports` above): the names the builder followed, never a
text search. The text form prints the same as one line per file:
`imports it`, `imports A, B from it`, `uses A, re-exported by crate::m`,
`uses A by name after `use super::*``, `uses T::new, defined in an impl block
there`, `uses the macro m, exported at the crate root`, and below depth 1
`through <file> (or 2 other files): ...`.

### Flat facts (`impact --sexp`)

```scheme
(impact-root <mod> :lang ts :file <path>)
(impact <root> <src> :lang ts :via imports :depth 1)
(impact <root> <src> :lang ts :via imports :depth 2 :through <mod> :more 1)
(impact <root> <src> :lang rs :via imports :depth 2 :through <mod> (:glob "super" HarEntry))
(impact-more <root> :hidden 549 :rerun "impact <file> 2 --all")
(impact-summary <root> :direct 650 :total 749 :depth 2)
(read-first <path>)
(read-first-more :hidden 640)
```

What versions up to 0.13 printed with `--sexp`, kept as it was for the scripts
that read it (the pull request check calls `impact <file> <hops> --sexp
--all`): one fact per line with module IDs. The counts are complete; without
`--all` at most 200 dependents are listed, nearest first, and `read-first`
names the file and its first 10 direct dependents. A file that is not in the
graph gives its `(error no-match ...)` line and exit status 0.

## invariants Output

```scheme
(invariant-violation <mod> :expected snake_case :actual PascalCase :lang py :pkg <repo>)
(invariants-summary :violations 12 :checked 3587)
(invariants-ok :checked 3587)
```

## layers Output

```scheme
(layer-group api :count 144)
(layer-member api <mod> :lang ts :part <part> :style layered :rule "folder routes/ -> api" :match exact)
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

## files Output

```scheme
(file <path> :module <mod> :lang ts :layer domain)
(files-summary :count 12 :of 340 :pattern "pattern")
(files-empty "pattern")
```

## source Output

```scheme
(source <path> :first 40 :last 80 :of 212 :entity <mod>::<name> :more 30 :next 81)
(line 40 "text of the line, quoted")
(line 41 "the first 2,000 characters of a longer line" :cut 5006)
(error ambiguous "name" :candidates <path>::<name> <path>::<name>)
```

`:entity` is there when the target is a definition; `:more` and `:next` when the
range is longer than one call prints: 400 lines, or 100,000 characters of
text. `:cut N` marks a line of N characters printed up to its first 2,000.
Only source files of the graph are printed; any other path, and a file that
is a symbolic link, is `(error no-match ...)`.

## grep Output

```scheme
(match <path> 42 "text of the line, quoted")
(match <path> 43 "the first 300 characters of a longer line" :cut 512)
(matches-summary :matches 2 :files 1 :searched 340 :pattern "text" :path "tests/.*" :unread 1)
(matches-summary :matches 450 :shown 200 :first 1 :next 201 :files 12 :searched 340 :pattern "text")
(matches-empty "text" :searched 340)
```

One `match` per line that holds the text, sorted by file and line and numbered
as `source` numbers them. `:searched` is the number of files the search
covered (all source files of the graph, or those `:path` selects), `:files`
the number that hold a match. One answer lists at most 200 matches and
100,000 characters: when more match, `:shown` and `:first` say which part is
listed and `:next` the match to go on from (`grep <text> --first 201`).
`:unread N` counts files that could not be
read or are a symbolic link; their lines are never printed.

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
(error invalid-query "(deps \"A\" 1" "a parenthesis is not closed")
(error invalid-query "(nosuch \"A\")" "unknown function: nosuch; (help) lists the functions")
```

A malformed expression answers one `error invalid-query` line and no result:
parentheses that do not match, a string that is not closed, an empty
expression, a function the language does not have.

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
| `php` | PHP/Laravel | PascalCase classes, camelCase methods | `App\Models\User`, `loadUser` |
| `blade` | Blade templates | kebab-case files, dot-separated view references | `users.show`, `components.alert` |

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
