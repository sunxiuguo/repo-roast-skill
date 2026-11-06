import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from _load import load

verdict = load("roast-repo", "verdict")
card = load("roast-card", "card")


def judge(key="security", score=4.0, rubric=None, roasts=None, **extra):
    data = {
        "judge": key, "persona": f"The {key.title()}", "score": score,
        "rubric": rubric if rubric is not None else {"a": 1, "b": 1, "c": 1, "d": 0.5, "e": 0.5},
        "verdict": f"{key} verdict", "praise": "something nice",
        "roasts": roasts if roasts is not None else [
            {"line": "jab", "evidence": "app.py:2", "fix": "do the thing", "impact": "high", "effort": "S"}],
    }
    data.update(extra)
    return data


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "db.py").write_text("x\n" * 50, encoding="utf-8")
        self.bailiff = verdict.Bailiff(self.repo, {"ci": {"files": []}, "tests": {"test_files": 0}})

    def tearDown(self):
        self._tmp.cleanup()


class BailiffTests(RepoCase):
    def test_accepts_real_path_and_line(self):
        self.assertIsNone(self.bailiff.check("app.py:3"))
        self.assertIsNone(self.bailiff.check("src/db.py:10-20"))
        self.assertIsNone(self.bailiff.check("`src/db.py:50` builds SQL"))
        self.assertIsNone(self.bailiff.check("./app.py:1"))

    def test_accepts_backtick_quoted_paths_with_spaces(self):
        folder = self.repo / "docs with spaces"
        folder.mkdir()
        (folder / "My Guide.md").write_text("first\nsecond\nthird\n", encoding="utf-8")
        for evidence in [
            "`docs with spaces/My Guide.md:2`",
            "See `docs with spaces/My Guide.md:1-3` for details",
            "`./docs with spaces/My Guide.md:1` and app.py:3",
            "`docs with spaces\\My Guide.md:3`",
        ]:
            with self.subTest(evidence=evidence):
                self.assertIsNone(self.bailiff.check(evidence))

    def test_quoted_paths_keep_file_and_line_validation(self):
        (self.repo / "My Guide.md").write_text("first\nsecond\n", encoding="utf-8")
        for evidence, reason in [
            ("`Missing Guide.md:1`", "not found"),
            ("`My Guide.md:3`", "out of range"),
            ("`My Guide.md:0`", "out of range"),
            ("`My Guide.md:2-1`", "out of range"),
            ("`My Guide.md:1-3`", "out of range"),
            ("app.py:1 and `Missing Guide.md:1`", "not found"),
            ("`My Guide.md:1` and nope.py:1", "not found"),
        ]:
            with self.subTest(evidence=evidence):
                self.assertIn(reason, self.bailiff.check(evidence))

    def test_quoted_paths_cannot_use_a_valid_suffix(self):
        # The full quoted path must be checked, not just its final word.
        for evidence in ["`missing app.py:1`", "`../missing app.py:1`",
                         "`/missing app.py:1`"]:
            with self.subTest(evidence=evidence):
                self.assertIsNotNone(self.bailiff.check(evidence))

    def test_non_location_code_spans_still_validate_citations(self):
        for evidence, reason in [
            ("app.py:1 and `nope.py:1,`", "not found"),
            ("app.py:1 and `app.py:999,`", "out of range"),
            ("`app.py:1 and nope.py:1`", "not found"),
        ]:
            with self.subTest(evidence=evidence):
                self.assertIn(reason, self.bailiff.check(evidence))
        self.assertIsNone(self.bailiff.check("`app.py:1,`"))

    def test_rejects_missing_file(self):
        self.assertIn("not found", self.bailiff.check("nope.py:1"))

    def test_rejects_line_out_of_range(self):
        self.assertIn("out of range", self.bailiff.check("app.py:4"))
        self.assertIn("out of range", self.bailiff.check("src/db.py:40-60"))
        self.assertIn("out of range", self.bailiff.check("app.py:0"))

    def test_rejects_path_escaping_repo(self):
        self.assertIsNotNone(self.bailiff.check("../outside.py:1"))

    def test_rejects_vague_evidence(self):
        self.assertIsNotNone(self.bailiff.check("vibes"))
        self.assertIsNotNone(self.bailiff.check(""))
        self.assertIsNotNone(self.bailiff.check("/etc/passwd:1"))

    def test_command_evidence(self):
        self.assertIsNone(self.bailiff.check("$ git log --oneline | wc -l -> 3"))
        self.assertIsNotNone(self.bailiff.check("$ "))

    def test_facts_evidence(self):
        self.assertIsNone(self.bailiff.check("facts.ci.files"))
        self.assertIsNone(self.bailiff.check("facts.tests.test_files = 0"))
        self.assertIn("does not exist", self.bailiff.check("facts.nothing.here"))

    def test_without_repo_only_format_is_checked(self):
        b = verdict.Bailiff(None, None)
        self.assertIsNone(b.check("anything.py:999"))
        self.assertIsNone(b.check("facts.whatever"))
        self.assertIsNotNone(b.check("vibes"))


class NormaliseTests(RepoCase):
    def test_rejects_roasts_without_fix_or_line(self):
        j = verdict.normalise_judge(judge(roasts=[
            {"line": "ok", "evidence": "app.py:1", "fix": "f"},
            {"line": "no fix", "evidence": "app.py:1", "fix": ""},
            {"line": "", "evidence": "app.py:1", "fix": "f"},
            {"line": "bad ev", "evidence": "nowhere.py:1", "fix": "f"},
        ]), self.bailiff)
        self.assertEqual(len(j["roasts"]), 1)
        self.assertEqual(len(j["rejected"]), 3)
        reasons = " ".join(r["reason"] for r in j["rejected"])
        self.assertIn("no fix", reasons)
        self.assertIn("no roast line", reasons)
        self.assertIn("not found", reasons)

    def test_score_follows_rubric(self):
        j = verdict.normalise_judge(judge(score=9.0, rubric={"a": 1, "b": 1, "c": 1, "d": 1, "e": 0}), self.bailiff)
        self.assertEqual(j["score"], 4.0)
        self.assertTrue(any("rubric total used" in n for n in j["notes"]))

    def test_command_evidence_is_labelled_in_report(self):
        raws = [judge(roasts=[
            {"line": "Lint fails", "evidence": "$ npm run lint -> 12 errors", "fix": "Fix lint", "impact": "low", "effort": "S"}
        ])]
        md = verdict.render_markdown(verdict.build(raws, repo_name="x", mode="default", date="d", headline=None,
                                                   bailiff=self.bailiff))
        self.assertIn("not re-run", md)

    def test_rubric_is_clamped_and_counted(self):
        j = verdict.normalise_judge(judge(score=10, rubric={"Vibes": 10}), self.bailiff)
        self.assertEqual(j["score"], 2.0)
        self.assertTrue(any("1 criteria, expected 5" in n for n in j["notes"]))
        self.assertTrue(any("clamped" in n for n in j["notes"]))

    def test_fix_today_prefers_leaked_credential(self):
        top = [
            {"judge": "newcomer", "impact": "low", "effort": "S", "line": "README typo", "fix": "fix it"},
            {"judge": "security", "impact": "high", "effort": "M",
             "line": "A live API key is committed", "fix": "Rotate the key and load it from the environment"},
        ]
        self.assertEqual(verdict.pick_fix_today(top)["judge"], "security")
        self.assertEqual(verdict.pick_fix_today(top[:1])["judge"], "newcomer")

    def test_missing_rubric_is_noted(self):
        j = verdict.normalise_judge(judge(score=7, rubric={}), self.bailiff)
        self.assertEqual(j["score"], 7.0)
        self.assertTrue(any("unjustified" in n for n in j["notes"]))

    def test_defaults_bad_impact_and_effort(self):
        j = verdict.normalise_judge(judge(roasts=[
            {"line": "x", "evidence": "app.py:1", "fix": "y", "impact": "apocalyptic", "effort": "XL"}]), self.bailiff)
        self.assertEqual(j["roasts"][0]["impact"], "medium")
        self.assertEqual(j["roasts"][0]["effort"], "M")

    def test_judge_prefix_stripped(self):
        self.assertEqual(verdict.normalise_judge(judge(key="judge-ops"), self.bailiff)["judge"], "ops")


class BuildTests(RepoCase):
    def test_overall_is_mean_and_order_fixed(self):
        raws = [judge("ops", 6.0, {"a": 2, "b": 2, "c": 2, "d": 0, "e": 0}),
                judge("security", 4.0)]
        r = verdict.build(raws, repo_name="x", mode="default", date="2026-11-07", headline=None, bailiff=self.bailiff)
        self.assertEqual(r["overall"], 5.0)
        self.assertEqual([j["judge"] for j in r["judges"]], ["security", "ops"])
        self.assertEqual(r["tier"], "Needs Seasoning")
        self.assertTrue(r["headline"])

    def test_ranking_by_impact_over_effort(self):
        raws = [judge("security", roasts=[
            {"line": "big slow", "evidence": "app.py:1", "fix": "f1", "impact": "high", "effort": "L"},
            {"line": "small quick", "evidence": "app.py:2", "fix": "f2", "impact": "low", "effort": "S"},
            {"line": "big quick", "evidence": "app.py:3", "fix": "f3", "impact": "high", "effort": "S"},
            {"line": "mid quick", "evidence": "app.py:3", "fix": "f4", "impact": "medium", "effort": "S"},
        ])]
        r = verdict.build(raws, repo_name="x", mode="savage", date="d", headline="h", bailiff=self.bailiff)
        self.assertEqual([f["fix"] for f in r["top_fixes"]], ["f3", "f4", "f1", "f2"])
        # equal impact/effort ratio: the higher impact wins the tie
        self.assertEqual([f["rank"] for f in r["top_fixes"]], [1, 2, 3, 4])
        self.assertEqual(r["fix_today"]["fix"], "f3")

    def test_top_fixes_capped_at_ten(self):
        roasts = [{"line": f"j{i}", "evidence": "app.py:1", "fix": f"f{i}"} for i in range(15)]
        r = verdict.build([judge(roasts=roasts)], repo_name="x", mode="default", date="d", headline=None,
                          bailiff=self.bailiff)
        self.assertEqual(len(r["top_fixes"]), 10)

    def test_fix_today_falls_back_when_nothing_small(self):
        r = verdict.build([judge(roasts=[{"line": "x", "evidence": "app.py:1", "fix": "big", "effort": "L"}])],
                          repo_name="x", mode="default", date="d", headline=None, bailiff=self.bailiff)
        self.assertEqual(r["fix_today"]["fix"], "big")

    def test_markdown_sections(self):
        raws = [judge("security", roasts=[
            {"line": "jab", "evidence": "app.py:1", "fix": "use a | b", "impact": "high", "effort": "S"},
            {"line": "unproven", "evidence": "trust me", "fix": "f"}])]
        r = verdict.build(raws, repo_name="demo", mode="gentle", date="2026-11-07", headline="Hi.",
                          bailiff=self.bailiff)
        md = verdict.render_markdown(r)
        for section in ["# The Roast of demo", "> Hi.", "## The one thing to fix today",
                        "## Top fixes", "## The panel", "## Thrown out by the bailiff", "## How this was scored",
                        "Scorecard:"]:
            self.assertIn(section, md)
        self.assertIn("| use a \\| b |", md)

    def test_tiers_match_card(self):
        self.assertEqual(verdict.TIERS, card.TIERS)
        for s in [0, 2.9, 3.0, 4.9, 5.0, 6.9, 7.0, 8.4, 8.5, 10]:
            self.assertEqual(verdict.tier(s), card.tier(s))

    def test_card_payload_shape(self):
        r = verdict.build([judge()], repo_name="x", mode="default", date="d", headline="h", bailiff=self.bailiff)
        payload = verdict.card_payload(r)
        self.assertEqual(payload["judges"][0]["name"], "The Security")
        self.assertEqual(payload["judges"][0]["role"], "Security")
        self.assertIn("overall", payload)


class CliTests(RepoCase):
    def run_cli(self, *extra):
        panel = self.repo / "panel"
        panel.mkdir(exist_ok=True)
        (panel / "security.json").write_text(json.dumps(judge()), encoding="utf-8")
        (panel / "ops.json").write_text(json.dumps([judge("ops")]), encoding="utf-8")
        out = self.repo / "roast"
        with redirect_stdout(io.StringIO()) as buf:
            code = verdict.main(["--judges", str(panel), "--repo", str(self.repo), "--out-dir", str(out),
                                 "--date", "2026-11-07", *extra])
        return code, out, buf.getvalue()

    def test_writes_all_outputs(self):
        code, out, printed = self.run_cli()
        self.assertEqual(code, 0)
        for name in ["roast.json", "card.json", "ROAST.md"]:
            self.assertTrue((out / name).exists(), name)
        data = json.loads((out / "roast.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["judges"]), 2)
        self.assertIn("overall", printed)

    def test_archives_previous_roast(self):
        self.run_cli()
        self.run_cli()
        _, out, printed = self.run_cli()
        history = sorted(p.name for p in (out / "history").glob("*.json"))
        self.assertEqual(history, ["roast-2026-11-07-2.json", "roast-2026-11-07.json"])
        self.assertIn("archived", printed)

    def test_no_archive_flag(self):
        self.run_cli()
        _, out, _ = self.run_cli("--no-archive")
        self.assertFalse((out / "history").exists())

    def test_no_judges_is_an_error(self):
        empty = self.repo / "empty"
        empty.mkdir()
        self.assertEqual(verdict.main(["--judges", str(empty), "--out-dir", str(self.repo / "r")]), 2)


class ExampleTests(unittest.TestCase):
    def test_example_panel_produces_committed_example(self):
        root = Path(__file__).resolve().parent.parent
        raws = verdict.load_judges([root / "examples" / "panel.example.json"])
        r = verdict.build(raws, repo_name="fictional-co/snackstack", mode="default", date="2026-11-07",
                          headline="h", bailiff=verdict.Bailiff(None, None))
        self.assertEqual(len(r["judges"]), 6)
        self.assertEqual(r["thrown_out"], 1)
        committed = (root / "examples" / "ROAST.example.md").read_text(encoding="utf-8")
        self.assertIn(f"**Overall: {r['overall']:.1f}/10 ({r['tier']})**", committed)


if __name__ == "__main__":
    unittest.main()
