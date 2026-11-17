"""The Rust reference of the benchmarks (tools/rust_resolve.py) counts the files that define the items a file names."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\n',
    "src/main.rs": "mod domain;\nmod app;\nfn main() {}\n",
    "src/domain/mod.rs": "mod stats;\nmod timeline;\nmod types;\npub use stats::*;\npub use timeline::*;\npub use types::*;\n",
    "src/domain/stats.rs": "fn parse_time() {}\npub struct Stats;\n",
    "src/domain/timeline.rs": "pub(crate) fn parse_time() {}\npub enum Kind { A, B }\n",
    "src/domain/types.rs": "pub struct Fork;\n",
    "src/app/mod.rs": "mod named;\nmod glob;\n",
    "src/app/named.rs": "use crate::domain::{parse_time, Fork};\npub fn n(_: Fork) { parse_time(); }\n",
    "src/app/glob.rs": (
        "use crate::domain::*;\n"
        "pub enum Local { Stats(u8) }\n"
        "pub fn g(fork: u8) -> Kind { let _ = Fork; Kind::A }\n"
    ),
}


class RustReferenceTest(unittest.TestCase):
    def test_the_reference_follows_items_to_their_definers(self) -> None:
        """/* REQ-BENCH-015: both copies of tools/rust_resolve.py give, for a name used through a facade, the file that defines it (to_followed), skipping a private item a glob re-export cannot carry; a glob import (`use crate::domain::*`) gives a glob-use reference for each item its module text names, also as the head of a path (`Kind::A`), and none for the variant of a local enum or a typed local name; the impact benchmark requires both the facade and the definer, and still draws its questions from the facade edges. */"""
        sys.path.insert(0, str(ROOT / "benchmarks" / "impact"))
        import impact100  # noqa: E402

        for copy in ("impact", "graphify"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "tree"
                for name, text in FILES.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(text, encoding="utf-8")
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
                out = Path(tmp) / "out" / "app.rust_refs.json"
                out.parent.mkdir()
                subprocess.run([sys.executable, "-I", str(ROOT / "benchmarks" / copy / "tools" / "rust_resolve.py"), str(root), str(out)],
                               check=True, capture_output=True)
                data = json.loads(out.read_text(encoding="utf-8"))
                posix = lambda path: path.replace(os.sep, "/")
                named = {(r["text"], posix(r["to_followed"])) for r in data["refs"] if posix(r["frm"]) == "src/app/named.rs"}
                self.assertIn(("crate::domain::parse_time", "src/domain/timeline.rs"), named, copy)
                self.assertIn(("crate::domain::Fork", "src/domain/types.rs"), named, copy)
                globs = {(r["text"].split()[-1], posix(r["to_followed"])) for r in data["refs"] if r["category"] == "glob-use"}
                self.assertEqual({("Fork", "src/domain/types.rs"), ("Kind", "src/domain/timeline.rs")}, globs, copy)
                required = {(posix(a), posix(b)) for a, b in impact100.rust_edges(data)}
                self.assertIn(("src/app/glob.rs", "src/domain/timeline.rs"), required)
                self.assertIn(("src/app/glob.rs", "src/domain/mod.rs"), required)
                drawn = {(posix(a), posix(b)) for a, b in impact100.drawn_from(tmp, "app", "rust", set())}
                self.assertNotIn(("src/app/glob.rs", "src/domain/timeline.rs"), drawn)
                self.assertIn(("src/app/named.rs", "src/domain/mod.rs"), drawn)


if __name__ == "__main__":
    unittest.main()
