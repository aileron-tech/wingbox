"""Accepted rule breaks keep asking for a decision: baseline terms and advice that inaction does not hide.

A baseline entry may state who accepted the break, why and until when; past
its end date the break is a violation again. A rebuild of unchanged code is
not a run in which advice was ignored, and the advice to fix a finding is
never hidden by inaction. The commands run as processes, as a user runs them.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.architecture import decisions  # noqa: E402
from scripts.architecture.advice import Advice, advice_id  # noqa: E402
from scripts.architecture.baseline import Acceptance, read_baseline_terms, terms_sexp  # noqa: E402

LAYERED = {
    "package.json": '{"name": "shop"}\n',
    **{f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
       for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)) for i in range(count)},
    "src/models/models0.ts": "import { routes0 } from '../routes/routes0';\nexport const models0 = routes0;\n",
}
ENTRY = '(baseline "." src/models/models0 src/routes/routes0 :rule "domain -> api" :from align'
CAPABILITY_CONFIG = """parts:
  .:
    style: layered
    layers:
      api: [routes]
      service: [services]
      infra: [repositories]
      domain: [models]
"""
SQL_IN_A_SERVICE = {
    "src/repositories/orders_repository.py": 'def find(cursor, order_id):\n    cursor.execute("SELECT id FROM orders WHERE id = %s", (order_id,))\n    return cursor.fetchone()\n',
    "src/services/orders_service.py": 'from src.repositories.orders_repository import find\n\n\ndef total(cursor, order_id):\n    cursor.execute("SELECT total FROM orders WHERE id = %s", (order_id,))\n    return find(cursor, order_id)\n',
    "src/routes/orders.py": 'from src.services.orders_service import total\n\n\ndef get(cursor, order_id):\n    return total(cursor, order_id)\n',
    "src/models/order.py": 'class Order:\n    pass\n',
}


def _advice(kind: str, target: str, rule: str) -> Advice:
    return Advice(advice_id(".", kind, target, rule), ".", kind, target, "why", "layered", rule, "evidence")


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment["WINGBOX_AUTO_REFRESH"] = "0"
    environment.pop("CLAUDECODE", None)
    return environment


class IgnoredRunsTest(unittest.TestCase):
    def test_a_rebuild_of_the_same_graph_is_not_an_ignored_run(self) -> None:
        """/* REQ-ARCH-046: a build whose graph hash equals the one of the run before does not count as a run in which open advice was ignored: six builds of the same graph hide nothing, and the count goes on with the next graph that differs. */"""
        item = _advice("missing-folder", "src/domain/aggregates", "target domain/aggregates")
        text = "\n".join(decisions.reconcile(decisions.Store(), [item], (), "2026-01-01", "g1"))
        for day in range(2, 8):
            text += "\n" + "\n".join(decisions.reconcile(decisions.read_store(text), [item], (), f"2026-01-0{day}", "g1"))
        self.assertNotIn(":ignored", text)
        self.assertEqual([item], decisions.visible(decisions.read_store(text), [item]))
        self.assertEqual(1, decisions.read_store(text).runs)
        for graph in ("g2", "g3"):
            text += "\n" + "\n".join(decisions.reconcile(decisions.read_store(text), [item], (), "2026-01-09", graph))
        self.assertNotIn(":ignored", text)
        text += "\n" + "\n".join(decisions.reconcile(decisions.read_store(text), [item], (), "2026-01-10", "g4"))
        self.assertIn(f"(advice-outcome {item.id} :ignored :runs 3 :at 2026-01-10)", text)
        self.assertEqual([], decisions.visible(decisions.read_store(text), [item]))

    def test_advice_to_fix_a_finding_is_never_hidden_by_inaction(self) -> None:
        """/* REQ-ARCH-047: advice of kind fix-violation, fix-capability or fix-pattern is not marked ignored and stays shown however many builds of changed code pass, while a layout suggestion (missing-folder) of the same store is hidden after 3. */"""
        fixes = [_advice("fix-violation", "src/models/a.ts", "domain -> api"),
                 _advice("fix-capability", "src/services/b.py", "sql in service"),
                 _advice("fix-pattern", "src/routes/c.py", "controller-skips-service")]
        folder = _advice("missing-folder", "src/domain/aggregates", "target domain/aggregates")
        text = "\n".join(decisions.reconcile(decisions.Store(), [*fixes, folder], ("src/models/a.ts",), "2026-01-01", "g0"))
        for run in range(1, 7):
            text += "\n" + "\n".join(
                decisions.reconcile(decisions.read_store(text), [*fixes, folder], ("src/models/a.ts",), "2026-01-02", f"g{run}"))
        self.assertEqual([f"(advice-outcome {folder.id} :ignored :runs 3 :at 2026-01-02)"],
                         [line for line in text.splitlines() if ":ignored" in line])
        self.assertEqual(fixes, decisions.visible(decisions.read_store(text), [*fixes, folder]))

    def test_a_fix_hidden_by_an_older_version_is_shown_again(self) -> None:
        """/* REQ-ARCH-048: a decisions store that already holds an ignored outcome for fix-violation advice shows the item again; a rejection with a reason is what hides it, and needs_reason is true for fix advice only. */"""
        fix = _advice("fix-violation", "src/models/a.ts", "domain -> api")
        folder = _advice("missing-folder", "src/domain/aggregates", "target domain/aggregates")
        text = "\n".join(decisions.reconcile(decisions.Store(), [fix, folder], ("src/models/a.ts",), "2026-01-01", "g0"))
        text += f"\n(advice-outcome {fix.id} :ignored :runs 3 :at 2026-01-04)"
        store = decisions.read_store(text)
        self.assertEqual([fix, folder], decisions.visible(store, [fix, folder]))
        self.assertTrue(decisions.needs_reason(store, fix.id))
        self.assertFalse(decisions.needs_reason(store, folder.id))
        self.assertFalse(decisions.needs_reason(store, "000000000000"))
        rejected = decisions.user_outcome(store, fix.id, False, "generated code, removed in the next release", "2026-01-05")
        self.assertEqual([folder], decisions.visible(decisions.read_store(text + "\n" + rejected), [fix, folder]))


class BaselineTermsUnitTest(unittest.TestCase):
    def test_terms_are_read_and_written_back(self) -> None:
        """/* REQ-ARCH-049: read_baseline_terms returns owner, reason and end date of the entries that state any of them, for baseline and baseline-capability lines; terms_sexp writes exactly the stated fields; an entry is expired from the day after its end date and when the date cannot be read. */"""
        text = (
            ENTRY + ' :owner "Ada" :reason "legacy import, ticket 12" :expires 2027-01-31)\n'
            '(baseline "." src/models/models1 src/routes/routes1 :rule "domain -> api" :from align)\n'
            '(baseline-capability "." src/services/orders_service sql :line 5 :from align :owner "Bo")\n'
        )
        terms = read_baseline_terms(text)
        self.assertEqual({
            ("baseline", ".", "src/models/models0", "src/routes/routes0"): Acceptance("Ada", "legacy import, ticket 12", "2027-01-31"),
            ("baseline-capability", ".", "src/services/orders_service", "sql"): Acceptance("Bo", "", ""),
        }, dict(terms))
        self.assertEqual(' :owner "Ada" :reason "legacy import, ticket 12" :expires 2027-01-31',
                         terms_sexp(Acceptance("Ada", "legacy import, ticket 12", "2027-01-31")))
        self.assertEqual("", terms_sexp(Acceptance()))
        until = Acceptance(expires="2027-01-31")
        self.assertFalse(until.expired("2027-01-31"))
        self.assertTrue(until.expired("2027-02-01"))
        self.assertTrue(Acceptance(expires="next spring").expired("2020-01-01"))
        self.assertFalse(Acceptance(owner="Ada").expired("2999-01-01"))


class BaselineTermsCommandTest(unittest.TestCase):
    def project(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name)) / "project"
        root.mkdir()
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        self.build(root)
        return root

    def wingbox(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", *args], capture_output=True, text=True,
                              cwd=root, env=_environment(), timeout=600)

    def build(self, root: Path) -> None:
        built = self.wingbox(root, "build", str(root))
        self.assertEqual(0, built.returncode, built.stderr)

    def boundaries(self, root: Path) -> tuple[int, list[str]]:
        done = self.wingbox(root, "boundaries", "all")
        return done.returncode, done.stdout.splitlines()

    def test_an_accepted_break_is_listed_with_its_owner_reason_and_end_date(self) -> None:
        """/* REQ-ARCH-050: a baseline entry that states :owner, :reason and an :expires date not yet passed keeps its break a violation-known, printed with the three fields after :known; boundaries exits 0. */"""
        files = dict(LAYERED)
        files[".wingbox/baseline.sexp"] = ENTRY + ' :owner "Ada" :reason "legacy import, ticket 12" :expires 2999-12-31)\n'
        status, lines = self.boundaries(self.project(files))
        self.assertEqual(0, status, lines)
        known = [line for line in lines if line.startswith("(violation-known ")]
        self.assertEqual(1, len(known), lines)
        self.assertIn(':rule "domain -> api" :known .wingbox/baseline.sexp :owner "Ada" '
                      ':reason "legacy import, ticket 12" :expires 2999-12-31 :src-file ', known[0])
        self.assertIn("(boundaries-summary :violations 0 :known 1 ", "\n".join(lines))

    def test_an_entry_past_its_end_date_is_a_violation_again(self) -> None:
        """/* REQ-ARCH-051: a baseline entry whose :expires date has passed, or cannot be read as a date, no longer hides its break: boundaries prints it as violation with :expired and the owner, counts it, exits 1, and `wingbox advice` asks to fix it again. */"""
        for expires, shown in (("2020-01-31", "2020-01-31"), ('"end of March"', '"end of March"')):
            with self.subTest(expires=expires):
                files = dict(LAYERED)
                files[".wingbox/baseline.sexp"] = ENTRY + f' :owner "Ada" :reason "legacy import" :expires {expires})\n'
                root = self.project(files)
                status, lines = self.boundaries(root)
                self.assertEqual(1, status, lines)
                broken = [line for line in lines if line.startswith("(violation ")]
                self.assertEqual(1, len(broken), lines)
                self.assertIn(f':rule "domain -> api" :expired {shown} :owner "Ada" :src-file ', broken[0])
                self.assertFalse(any(line.startswith("(violation-known ") for line in lines), lines)
                self.assertIn("(boundaries-summary :violations 1 :known 0 ", "\n".join(lines))
                advice = self.wingbox(root, "advice", "--sexp").stdout
                self.assertIn(':kind fix-violation :target "src/models/models0.ts"', advice)

    def test_an_entry_without_terms_prints_as_before(self) -> None:
        """/* REQ-ARCH-052: a baseline entry that states no owner, reason or end date gives the same violation-known line and the same arch-known fact as before the terms existed: nothing is added. */"""
        files = dict(LAYERED)
        files[".wingbox/baseline.sexp"] = ENTRY + ")\n"
        root = self.project(files)
        status, lines = self.boundaries(root)
        self.assertEqual(0, status, lines)
        known = [line for line in lines if line.startswith("(violation-known ")]
        self.assertRegex(known[0], r':rule "domain -> api" :known \.wingbox/baseline\.sexp :src-file ')
        graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertRegex(graph, r'(?m)^\(arch-known "\." \S+ \S+ "\.wingbox/baseline\.sexp"\)$')

    def test_align_learn_keeps_the_terms_a_person_wrote(self) -> None:
        """/* REQ-ARCH-053: `wingbox align --learn` writes new entries without terms, as before, and keeps the owner, the reason and the end date of an entry that states them; an entry past its end date stays past it, so learning again does not accept it again. */"""
        root = self.project(LAYERED)
        learned = self.wingbox(root, "align", "--learn", "--sexp")
        self.assertEqual(0, learned.returncode, learned.stderr)
        path = root / ".wingbox" / "baseline.sexp"
        self.assertIn(ENTRY + ")\n", path.read_text(encoding="utf-8"))
        path.write_text(ENTRY + ' :owner "Ada" :reason "legacy import" :expires 2020-01-31)\n', encoding="utf-8")
        (root / "src" / "models" / "models1.ts").write_text(
            "import { routes1 } from '../routes/routes1';\nexport const models1 = routes1;\n", encoding="utf-8")
        self.build(root)
        self.assertEqual(0, self.wingbox(root, "align", "--learn", "--sexp").returncode)
        text = path.read_text(encoding="utf-8")
        self.assertIn(ENTRY + ' :owner "Ada" :reason "legacy import" :expires 2020-01-31)\n', text)
        self.assertIn('(baseline "." src/models/models1 src/routes/routes1 :rule "domain -> api" :from align)\n', text)
        self.build(root)
        status, lines = self.boundaries(root)
        self.assertEqual(1, status, lines)
        self.assertTrue(any(line.startswith("(violation src/models/models0 ") and ":expired 2020-01-31" in line
                            for line in lines), lines)
        self.assertTrue(any(line.startswith("(violation-known src/models/models1 ") for line in lines), lines)

    def test_a_capability_entry_has_the_same_terms(self) -> None:
        """/* REQ-ARCH-054: a baseline-capability entry with an owner and an end date not yet passed prints capability-violation-known with them; past the date the finding is a capability-violation again with :expired, and boundaries exits 1. */"""
        files = {**SQL_IN_A_SERVICE, ".wingbox/config.yaml": CAPABILITY_CONFIG}
        line = '(baseline-capability "." src/services/orders_service sql :line 5 :from align :owner "Bo" :expires {date})\n'
        files[".wingbox/baseline.sexp"] = line.format(date="2999-12-31")
        root = self.project(files)
        status, lines = self.boundaries(root)
        self.assertEqual(0, status, lines)
        known = [item for item in lines if item.startswith("(capability-violation-known src/services/orders_service.py ")]
        self.assertEqual(1, len(known), lines)
        self.assertIn(' :known .wingbox/baseline.sexp :owner "Bo" :expires 2999-12-31', known[0])
        (root / ".wingbox" / "baseline.sexp").write_text(line.format(date="2020-01-31"), encoding="utf-8")
        self.build(root)
        status, lines = self.boundaries(root)
        self.assertEqual(1, status, lines)
        broken = [item for item in lines if item.startswith("(capability-violation src/services/orders_service.py ")]
        self.assertEqual(1, len(broken), lines)
        self.assertIn(' :expired 2020-01-31 :owner "Bo"', broken[0])

    def test_four_builds_of_unchanged_code_leave_the_fix_visible(self) -> None:
        """/* REQ-ARCH-055: after `wingbox init`, four builds of unchanged code leave the fix-violation recommendation in `wingbox advice`, and the decisions store holds no ignored outcome. */"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name)) / "project"
        root.mkdir()
        for name, text in LAYERED.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        self.assertEqual(0, self.wingbox(root, "init", str(root)).returncode)
        for _ in range(4):
            self.build(root)
        advice = self.wingbox(root, "advice", "--sexp").stdout
        self.assertIn(':kind fix-violation :target "src/models/models0.ts"', advice)
        self.assertNotIn(":ignored", (root / ".wingbox" / "decisions.sexp").read_text(encoding="utf-8"))

    def test_fix_advice_is_rejected_only_with_a_reason(self) -> None:
        """/* REQ-ARCH-056: `wingbox advice reject <id>` on fix-violation advice without --reason is refused with exit status 2 and writes nothing; with --reason it records the rejection and the item leaves `wingbox advice`. */"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name)) / "project"
        root.mkdir()
        for name, text in LAYERED.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        self.assertEqual(0, self.wingbox(root, "init", str(root)).returncode)
        self.build(root)
        advice = self.wingbox(root, "advice", "--sexp").stdout
        found = re.search(r":id (\w+) :kind fix-violation", advice)
        self.assertIsNotNone(found, advice)
        advice_id_ = found.group(1)
        store = root / ".wingbox" / "decisions.sexp"
        before = store.read_text(encoding="utf-8")
        refused = self.wingbox(root, "advice", "reject", advice_id_)
        self.assertEqual(2, refused.returncode, refused.stdout + refused.stderr)
        self.assertIn("--reason", refused.stderr)
        self.assertEqual(before, store.read_text(encoding="utf-8"))
        done = self.wingbox(root, "advice", "reject", advice_id_, "--reason", "generated file, removed next release")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn(advice_id_, self.wingbox(root, "advice", "--sexp").stdout)


if __name__ == "__main__":
    unittest.main()
