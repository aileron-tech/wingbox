// CodeGraph's own file-level graph, read through its library API; nothing is resolved here.
//
// Usage (run with the Node runtime that CodeGraph bundles, see run.sh):
//   node cg_graph.js <codegraph lib/dist/index.js> pairs <project> <out.json>
//       every file pair of CodeGraph's file-level queries (getFileDependencyPairs, all edge kinds
//       but `contains`), and the same pairs split by the kind of the symbol edge behind them
//   node cg_graph.js <codegraph lib/dist/index.js> dependents <project> <file> <depth>
//       the files that depend on <file> within <depth> steps: the breadth-first walk over
//       getFileDependents that `codegraph affected` runs, without its test-file filter
'use strict';
const fs = require('fs');
const lib = require(process.argv[2]);
const CodeGraph = lib.default || lib.CodeGraph;
const [action, project] = process.argv.slice(3, 5);
(async () => {
  const cg = await CodeGraph.open(project);
  try {
    if (action === 'pairs') {
      // Every file pair that CodeGraph's file-level queries use (all edge kinds but `contains`),
      // and the same pairs split by the kind of the symbol edge behind them.
      const key = (a, b) => a + '\0' + b;
      const byKind = {};
      const fileOf = new Map();
      for (const file of cg.getFiles()) {
        for (const node of cg.getNodesInFile(file.path)) fileOf.set(node.id, node.filePath);
      }
      for (const [id, source] of fileOf) {
        for (const edge of cg.getOutgoingEdges(id)) {
          const target = fileOf.get(edge.target) || (cg.getNode(edge.target) || {}).filePath;
          if (!target || target === source || edge.kind === 'contains') continue;
          (byKind[edge.kind] = byKind[edge.kind] || new Set()).add(key(source, target));
        }
      }
      const all = new Set(cg.getFileDependencyPairs(0).map((p) => key(p.source, p.target)));
      const union = new Set(Object.values(byKind).flatMap((set) => [...set]));
      if (union.size !== all.size || [...all].some((k) => !union.has(k))) {
        throw new Error(`edge kinds do not add up to getFileDependencyPairs: ${union.size} against ${all.size}`);
      }
      const asPairs = (set) => [...set].sort().map((k) => k.split('\0'));
      const kinds = {};
      for (const kind of Object.keys(byKind).sort()) kinds[kind] = asPairs(byKind[kind]);
      fs.writeFileSync(process.argv[5], JSON.stringify({ pairs: asPairs(all), kinds }));
      console.log('pairs', all.size, Object.fromEntries(Object.entries(kinds).map(([k, v]) => [k, v.length])));
    } else if (action === 'dependents') {
      // The breadth-first walk of `codegraph affected`, without its test-file filter.
      const target = process.argv[5];
      const depth = parseInt(process.argv[6], 10);
      const seen = new Set([target]);
      const found = new Set();
      let frontier = [target];
      for (let level = 0; level < depth; level++) {
        const next = [];
        for (const file of frontier) {
          for (const dep of cg.getFileDependents(file)) {
            if (!seen.has(dep)) { seen.add(dep); found.add(dep); next.push(dep); }
          }
        }
        frontier = next;
      }
      console.log(JSON.stringify([...found].sort()));
    } else {
      throw new Error('action: pairs or dependents');
    }
  } finally {
    cg.destroy();
  }
})().catch((e) => { console.error(e && e.stack || String(e)); process.exit(1); });
