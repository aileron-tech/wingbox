"""`wingbox align`: gaps, learning and baseline."""
import unittest

from scripts.architecture.library import load_library

LIBRARY = load_library()


class AlignTest(unittest.TestCase):
    def _fixture(self):
        from scripts.architecture.advice import ModuleFact, PartRules

        style = LIBRARY.style("layered")
        part = PartRules(".", "layered", "determined", {l.id: l.rank for l in style.layers},
                         {(d.source, d.target): (d.reason, d.severity, d.fix) for d in style.denies})
        modules = {}
        for folder, layer, count in (("routes", "api", 4), ("services", "service", 4), ("models", "domain", 4),
                                     ("repositories", "infra", 4), ("gateways2", "other", 3)):
            for i in range(count):
                mid = f"src/{folder}/m{i}"
                modules[mid] = ModuleFact(mid, mid + ".ts", ".", layer, layer != "other")
        dependencies = {f"src/routes/m{i}": (f"src/services/m{i}",) for i in range(4)}
        dependencies.update({f"src/services/m{i}": (f"src/repositories/m{i}", f"src/gateways2/m{i % 3}") for i in range(4)})
        dependencies.update({f"src/repositories/m{i}": (f"src/models/m{i}",) for i in range(4)})
        dependencies.update({f"src/models/m{i}": (f"src/repositories/m{i}",) for i in range(4)})   # domain -> infra: broken
        return part, modules, dependencies

    def test_align_reports_placed_share_rules_followed_and_gaps(self) -> None:
        """/* REQ-ARCH-029: align gives a part the share of placed files, of dependencies that follow the rules, the layers present and missing, the folders without a layer and the broken rules with a count and an example. */"""
        from scripts.architecture.align import align_part

        part, modules, dependencies = self._fixture()
        result = align_part(part, modules, dependencies, ("ui", "api", "service", "infra", "domain", "shared", "config"))
        self.assertEqual((16, 19), (result.placed, result.files))
        self.assertEqual((12, 16), (result.following, result.dependencies))
        self.assertIn("ui", result.layers_missing)
        gaps = {(gap.kind, gap.target): gap for gap in result.gaps}
        self.assertEqual(3, gaps[("unplaced-folder", "gateways2")].count)
        self.assertEqual(4, gaps[("rule", "domain -> infra")].count)
        self.assertEqual("src/models/m0.ts -> src/repositories/m0.ts", gaps[("rule", "domain -> infra")].example)

    def test_learning_keeps_the_closest_style_and_derives_the_rest_by_names_patterns_imports(self) -> None:
        """/* REQ-ARCH-030: learning takes the closest style of the library as the base; a folder it does not place is derived by its file names (*.controller.ts), then by its name in another style (strategies is an api folder in nestjs), then by its imports; folders the style places are never relabelled, and what still breaks a rule is the baseline. */"""
        from scripts.architecture.align import baseline, layers_by_imports
        from scripts.architecture.detect import judge, split_parts
        from scripts.architecture.learn import closest_style, derive, unplaced_folders
        from scripts.architecture.plan import PartPlan, layer_folders
        from scripts.architecture.words import DEFAULT_SETTINGS

        paths = ("package.json",) + tuple(
            f"src/{folder}/{name}{i}{suffix}" for folder, name, suffix, count in (
                ("routes", "r", ".ts", 4), ("services", "s", ".ts", 4), ("models", "m", ".ts", 4),
                ("repositories", "p", ".ts", 4), ("albums", "a", ".controller.ts", 2), ("strategies", "t", ".ts", 2),
                ("gateways2", "m", ".ts", 3))
            for i in range(count)
        )
        part_files = split_parts(paths, LIBRARY)[0]
        closest = closest_style(judge(part_files, LIBRARY))
        self.assertEqual("layered", closest.style)
        style = LIBRARY.style("layered")
        layers, routing = layer_folders(style, part_files, DEFAULT_SETTINGS, LIBRARY)
        base = PartPlan(".", "npm package", "container", 23, "layered", "configured", "config", None, None, layers, routing, None)
        unplaced = unplaced_folders(base, part_files.files, LIBRARY)
        self.assertEqual({"albums", "strategies"}, set(unplaced))
        part, modules, dependencies = self._fixture()
        derived = {folder: derive(folder, members, style, LIBRARY, layers_by_imports(part, modules, dependencies))
                   for folder, members in unplaced.items()}
        self.assertEqual(("api", "suffix"), (derived["albums"].layer, derived["albums"].by))
        self.assertEqual(("api", "name"), (derived["strategies"].layer, derived["strategies"].by))
        self.assertEqual({"gateways2": ("infra", "4 dependencies, 0 against the rules as infra")},
                         {k: (v[0], v[1]) for k, v in layers_by_imports(part, modules, dependencies).items()})
        entries = baseline(part, modules, dependencies, {"gateways2": "infra"}, {})
        self.assertEqual({"domain -> infra"}, {entry.rule for entry in entries})

    def test_align_and_boundaries_count_the_same_breaks(self) -> None:
        """/* REQ-ARCH-039: align and its baseline use the rule set of `boundaries`: a pair imported for types only and a pair whose source is a composition root (a module without a rule-based layer) are not breaks; a candidate rule gives no break. */"""
        import dataclasses

        from scripts.architecture.align import align_part, baseline

        part, modules, dependencies = self._fixture()
        typed = frozenset({("src/models/m0", "src/repositories/m0")})
        modules = dict(modules)
        modules["src/models/m1"] = dataclasses.replace(modules["src/models/m1"], certain=False)
        result = align_part(part, modules, dependencies, ("api", "service", "infra", "domain"), None, typed)
        self.assertEqual(2, next(gap.count for gap in result.gaps if gap.target == "domain -> infra"))
        self.assertEqual(2, len(baseline(part, modules, dependencies, {}, {}, typed)))


if __name__ == "__main__":
    unittest.main()
