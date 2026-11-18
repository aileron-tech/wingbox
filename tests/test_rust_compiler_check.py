"""The compiler check of the Rust reference (tools/rust_compiler_check.py) reads a SCIP index and classifies each edge."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "rust-analyzer cargo app 0.1.0 "
FILES = {
    "src/lib.rs": "mod model;\nmod seed;\nmod user;\n",
    "src/model.rs": "pub struct Store;\n",
    "src/seed.rs": "use crate::model::Store;\nimpl Store {\n    pub fn open() -> Store { Store }\n    pub fn size(&self) -> u8 { 1 }\n}\n",
    "src/user.rs": "use crate::model::Store;\npub(super) fn f() -> u8 { Store::open().size() }\n",
}
# (file, token, which occurrence of the token in the file, symbol, definition)
NAMES = [
    ("src/lib.rs", "model", 0, "model/", False), ("src/lib.rs", "seed", 0, "seed/", False), ("src/lib.rs", "user", 0, "user/", False),
    ("src/model.rs", "Store", 0, "model/Store#", True),
    ("src/seed.rs", "model", 0, "model/", False), ("src/seed.rs", "Store", 0, "model/Store#", False),
    ("src/seed.rs", "Store", 1, "model/Store#", False),
    ("src/seed.rs", "open", 0, "seed/impl#[Store]open().", True), ("src/seed.rs", "size", 0, "seed/impl#[Store]size().", True),
    ("src/user.rs", "model", 0, "model/", False), ("src/user.rs", "Store", 0, "model/Store#", False),
    ("src/user.rs", "super", 0, "", False), ("src/user.rs", "f", 0, "user/f().", True),
    ("src/user.rs", "Store", 1, "model/Store#", False),
    ("src/user.rs", "open", 0, "seed/impl#[Store]open().", False), ("src/user.rs", "size", 0, "seed/impl#[Store]size().", False),
]


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        out.append((value & 0x7F) | (0x80 if value > 0x7F else 0))
        value >>= 7
        if not value:
            return bytes(out)


def message(number: int, payload: bytes) -> bytes:
    return varint((number << 3) | 2) + varint(len(payload)) + payload


def position(text: str, token: str, which: int) -> tuple[int, int]:
    import re

    offset = [match.start() for match in re.finditer(rf"(?<![A-Za-z0-9_]){token}(?![A-Za-z0-9_])", text)][which]
    line = text.count("\n", 0, offset)
    return line, offset - (text.rfind("\n", 0, offset) + 1)


def scip_index() -> bytes:
    documents = b""
    for path, text in FILES.items():
        body = message(1, path.encode())
        for file, token, which, symbol, definition in NAMES:
            if file != path:
                continue
            line, column = position(text, token, which)
            span = b"".join(varint(number) for number in (line, column, column + len(token)))
            body += message(2, message(1, span) + message(2, (PACKAGE + (symbol or "crate/")).encode())
                            + varint(3 << 3) + varint(1 if definition else 0))
        documents += message(2, body)
    return message(1, message(2, message(1, b"rust-analyzer") + message(2, b"test"))) + documents


class RustCompilerCheckTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "the benchmark references run on Linux and macOS (run.sh)")
    def test_edges_of_an_index_are_classified_and_compared_with_the_reference(self) -> None:
        """/* REQ-BENCH-017: tools/rust_compiler_check.py reads a SCIP index of rust-analyzer without any dependency, finds the file of each module and definition, and classifies each name that resolves into another file: a `mod` declaration, the last module of a path, an item in a `use`, a member of an inherent impl block named through a type path (`Store::open`, counted), a method called on a value (`.size()`, outside the question), a visibility path (`pub(super)`, outside); a required pair of the reference the index does not have is listed as not confirmed, a counted edge the reference lacks as missing in the reference. */"""
        spec = importlib.util.spec_from_file_location("rust_compiler_check", ROOT / "benchmarks" / "impact" / "tools" / "rust_compiler_check.py")
        check = importlib.util.module_from_spec(spec)
        sys.modules["rust_compiler_check"] = check
        spec.loader.exec_module(check)
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in FILES.items():
                (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / name).write_text(text, encoding="utf-8")
            scip = Path(tmp) / "index.scip"
            scip.write_bytes(scip_index())
            index = check.Index(tmp)
            index.add("", str(scip))
            index.finish()
            edges = {pair: dict(entry["kinds"]) for pair, entry in index.edges().items()}
            self.assertEqual({
                ("src/lib.rs", "src/model.rs"): {"mod-decl": 1},
                ("src/lib.rs", "src/seed.rs"): {"mod-decl": 1},
                ("src/lib.rs", "src/user.rs"): {"mod-decl": 1},
                ("src/seed.rs", "src/model.rs"): {"module": 1, "use-item": 1, "name": 1},
                ("src/user.rs", "src/model.rs"): {"module": 1, "use-item": 1, "name": 1},
                ("src/user.rs", "src/seed.rs"): {"type-member": 1, "method-or-field": 1},
                ("src/user.rs", "src/lib.rs"): {"visibility": 1},
            }, edges)
            refs = [{"frm": a, "to": b, "to_followed": b, "line": 1, "category": "use", "text": ""} for a, b in (
                ("src/lib.rs", "src/model.rs"), ("src/lib.rs", "src/seed.rs"), ("src/lib.rs", "src/user.rs"),
                ("src/seed.rs", "src/model.rs"), ("src/user.rs", "src/model.rs"), ("src/model.rs", "src/user.rs"))]
            reference = {"rs_files": sorted(FILES), "refs": refs, "modules": {}}
            result = check.compare(index, reference, {("src/lib.rs", "src/model.rs"), ("src/model.rs", "src/seed.rs")})
        self.assertEqual(7, result["compiler_edges"])
        self.assertEqual(6, result["compiler_counted"])
        self.assertEqual({"visibility": 1}, result["compiler_outside_by_kind"])
        self.assertEqual([("src/model.rs", "src/user.rs")], [(row["from"], row["to"]) for row in result["reference_not_confirmed"]])
        self.assertEqual([("src/user.rs", "src/seed.rs")], [(row["from"], row["to"]) for row in result["compiler_counted_missing_in_reference"]])
        self.assertEqual(5, len(result["compiler_counted_missing_in_graph"]))
        self.assertEqual([("src/model.rs", "src/seed.rs")], [tuple(pair) for pair in result["graph_not_confirmed"]])


if __name__ == "__main__":
    unittest.main()
