// "Find usages" in TypeScript with the TypeScript language service (findReferences), as an editor does.
//
// Usage: node ts_refs.mjs <typescript package dir> <repository root> symbols <out.json>
//        node ts_refs.mjs <typescript package dir> <repository root> refs <candidates.json> <need> <out.json>
//        node ts_refs.mjs <typescript package dir> <repository root> time <questions.json> <out.json>
//
// The program: compilerOptions of <root>/tsconfig.json, and as root files every TypeScript and JavaScript
// file of the repository outside node_modules and hidden folders (allowJs on, nothing emitted), so that a
// usage in a file that tsconfig.json does not include (scripts, tools) is found too, as in an editor with
// the whole folder open. Nothing of the repository is executed: the compiler only reads files.
//
// symbols: every top-level declaration with an `export` modifier (not `export default`) of every
//          non-declaration file: function, class, interface, type alias, enum, const/let/var.
// refs:    findReferences on the name of each candidate, in the given order, until <need> candidates
//          have a reference in another file; every tried candidate is written with its files.
// time:    load time of the program and time of each query, for the timing table.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const [tsDir, rootArg, mode, ...rest] = process.argv.slice(2);
const ts = createRequire(path.join(path.resolve(tsDir), "package.json"))(path.resolve(tsDir));
const root = path.resolve(rootArg);
const SOURCE = /\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/;

function walk(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (entry.isFile() && SOURCE.test(entry.name)) out.push(full);
  }
  return out;
}

function compilerOptions() {
  const configPath = path.join(root, "tsconfig.json");
  if (!fs.existsSync(configPath)) return { allowJs: true };
  const parsed = ts.getParsedCommandLineOfConfigFile(configPath, {}, { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => {} });
  return { ...parsed.options, allowJs: true, checkJs: false, noEmit: true, incremental: false, composite: false, tsBuildInfoFile: undefined };
}

function makeService() {
  const files = walk(root, []).sort();
  const options = compilerOptions();
  const snapshots = new Map();
  const host = {
    getScriptFileNames: () => files,
    getScriptVersion: () => "1",
    getScriptSnapshot: (name) => {
      if (!snapshots.has(name)) {
        snapshots.set(name, fs.existsSync(name) ? ts.ScriptSnapshot.fromString(fs.readFileSync(name, "utf8")) : undefined);
      }
      return snapshots.get(name);
    },
    getCurrentDirectory: () => root,
    getCompilationSettings: () => options,
    getDefaultLibFileName: (o) => ts.getDefaultLibFilePath(o),
    fileExists: ts.sys.fileExists,
    readFile: ts.sys.readFile,
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
    realpath: ts.sys.realpath,
  };
  return { service: ts.createLanguageService(host, ts.createDocumentRegistry()), files };
}

const relative = (file) => path.relative(root, file).split(path.sep).join("/");
const exported = (node) => (ts.getCombinedModifierFlags(node) & ts.ModifierFlags.Export) !== 0
  && (ts.getCombinedModifierFlags(node) & ts.ModifierFlags.Default) === 0;
const KINDS = new Map([
  [ts.SyntaxKind.FunctionDeclaration, "function"], [ts.SyntaxKind.ClassDeclaration, "class"],
  [ts.SyntaxKind.InterfaceDeclaration, "interface"], [ts.SyntaxKind.TypeAliasDeclaration, "type"],
  [ts.SyntaxKind.EnumDeclaration, "enum"],
]);

function symbols(service, files) {
  const program = service.getProgram();
  const found = [];
  for (const file of files) {
    if (/\.d\.[cm]?ts$/.test(file)) continue;
    const source = program.getSourceFile(file);
    if (!source) continue;
    const add = (nameNode, kind) => {
      const { line, character } = source.getLineAndCharacterOfPosition(nameNode.getStart(source));
      found.push({ file: relative(file), name: nameNode.text, kind, line: line + 1, col: character });
    };
    for (const statement of source.statements) {
      if (KINDS.has(statement.kind) && statement.name && exported(statement)) add(statement.name, KINDS.get(statement.kind));
      if (statement.kind === ts.SyntaxKind.VariableStatement && exported(statement)) {
        for (const declaration of statement.declarationList.declarations) {
          if (ts.isIdentifier(declaration.name)) add(declaration.name, "variable");
        }
      }
    }
  }
  return found;
}

function position(service, question) {
  const source = service.getProgram().getSourceFile(path.join(root, question.file));
  return source.getPositionOfLineAndCharacter(question.line - 1, question.col);
}

function references(service, question) {
  const file = path.join(root, question.file);
  const started = performance.now();
  const groups = service.findReferences(file, position(service, question)) || [];
  const seconds = (performance.now() - started) / 1000;
  const files = new Set();
  let count = 0;
  for (const group of groups) {
    for (const ref of group.references) {
      const name = relative(ref.fileName);
      if (name.startsWith("..") || name.includes("node_modules/")) continue;
      if (!ref.isDefinition) count += 1;
      if (name !== question.file) files.add(name);
    }
  }
  return { files: [...files].sort(), references: count, seconds };
}

const started = performance.now();
const { service, files } = makeService();
service.getProgram();
const loadSeconds = (performance.now() - started) / 1000;

if (mode === "symbols") {
  const found = symbols(service, files);
  fs.writeFileSync(rest[0], JSON.stringify({ tool: `typescript ${ts.version}`, files: files.length, symbols: found }, null, 1));
  console.log(`${found.length} exported symbols in ${files.length} files`);
} else if (mode === "refs") {
  const candidates = JSON.parse(fs.readFileSync(rest[0], "utf8"));
  const need = Number(rest[1]);
  const tried = [];
  let used = 0;
  for (const candidate of candidates) {
    if (used >= need) break;
    const result = references(service, candidate);
    tried.push({ ...candidate, files: result.files, references: result.references });
    if (result.files.length) used += 1;
  }
  fs.writeFileSync(rest[2], JSON.stringify({ tool: `typescript ${ts.version}`, tried }, null, 1));
  console.log(`${tried.length} candidates tried, ${used} used by another file`);
} else if (mode === "time") {
  const questions = JSON.parse(fs.readFileSync(rest[0], "utf8"));
  const rows = questions.map((q) => ({ id: q.id, seconds: references(service, q).seconds }));
  fs.writeFileSync(rest[1], JSON.stringify({ load_seconds: loadSeconds, queries: rows }, null, 1));
  console.log(`load ${loadSeconds.toFixed(1)} s, ${rows.length} queries`);
} else {
  throw new Error("mode: symbols, refs or time");
}
