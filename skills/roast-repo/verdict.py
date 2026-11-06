#!/usr/bin/env python3
"""Assemble the panel's verdicts into roast.json, card.json and ROAST.md.

Each judge hands back a JSON object (or a file holding a list of them):

    {"judge": "security", "persona": "The Paranoid", "score": 4.5,
     "rubric": {"Secrets hygiene": 1, ...}, "verdict": "one-liner",
     "praise": "one thing done well",
     "roasts": [{"line": "...", "evidence": "src/db.py:42", "fix": "...",
                 "impact": "high", "effort": "S"}]}

This script is the bailiff. A roast is admitted only if it has a line, a fix
and evidence that is either `path:line` (checked against the repo when --repo
is given), `$ command -> output`, or `facts.<key>` (checked against facts.json).
Everything else is thrown out and listed as such. Scores must match the rubric.

Usage:
    python3 verdict.py --judges roast/panel --repo . --facts roast/facts.json \
        --out-dir roast --repo-name my-app --mode default --headline "..."
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sys
from pathlib import Path

JUDGE_ORDER = ["security", "performance", "newcomer", "maintainer", "product", "ops"]
IMPACT = {"high": 3, "medium": 2, "low": 1}
EFFORT = {"S": 1, "M": 2, "L": 3}
RUBRIC_CRITERIA = 5   # every judge scores five criteria...
RUBRIC_MAX = 2.0      # ...from 0 to 2 each, so a score is at most 10
MODES = {"gentle", "default", "savage"}

# Keep in sync with roast-card/card.py (tests/test_verdict.py checks they agree).
TIERS = [(3.0, "Dumpster Fire"), (5.0, "Smoke Alarm"), (7.0, "Needs Seasoning"),
         (8.5, "Nicely Done"), (10.01, "Chef's Kiss")]

DEFAULT_HEADLINES = {
    "Dumpster Fire": "The panel called the fire department, then filed a bug.",
    "Smoke Alarm": "Something is burning and the panel found the receipts.",
    "Needs Seasoning": "Solid ingredients, questionable cooking.",
    "Nicely Done": "The panel came to roast and left taking notes.",
    "Chef's Kiss": "The panel tried very hard to be mean. It did not go well for them.",
}

LOCATION = re.compile(r"(?<![\w/\\.-])([\w.\-][\w.\-/\\]*?):(\d+)(?:-(\d+))?\b")

# Quoting makes spaces unambiguous without absorbing prose around bare citations.
QUOTED_TEXT = re.compile(r"`([^`\r\n]*)`")
QUOTED_LOCATION = re.compile(r"([\w.\-/\\][\w.\-/\\ ]*?):(\d+)(?:-(\d+))?")


def evidence_locations(evidence: str):
    """Yield whole quoted citations and ordinary citations outside backticks."""
    start = 0
    for quoted in QUOTED_TEXT.finditer(evidence):
        yield from LOCATION.finditer(evidence[start:quoted.start()])
        location = QUOTED_LOCATION.fullmatch(quoted.group(1))
        if location:
            yield location
        else:
            # Preserve validation of existing inline prose/multiple citations.
            yield from LOCATION.finditer(quoted.group(1))
        start = quoted.end()
    yield from LOCATION.finditer(evidence[start:])


def tier(score: float) -> str:
    for limit, name in TIERS:
        if score < limit:
            return name
    return TIERS[-1][1]


def clamp_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(min(10.0, max(0.0, score)), 1)


def lookup_fact(facts: dict, dotted: str) -> bool:
    node = facts
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return True


class Bailiff:
    """Checks evidence against the repo on disk and the facts file."""

    def __init__(self, repo: Path | None, facts: dict | None):
        self.repo = repo.resolve() if repo else None
        self.facts = facts
        self._line_counts: dict[str, int | None] = {}

    def _count_lines(self, rel: str) -> int | None:
        if rel not in self._line_counts:
            path = (self.repo / rel)
            try:
                resolved = path.resolve()
                resolved.relative_to(self.repo)
                with open(resolved, "rb") as fh:
                    data = fh.read()
                self._line_counts[rel] = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
            except (OSError, ValueError):
                self._line_counts[rel] = None
        return self._line_counts[rel]

    def check(self, evidence: str) -> str | None:
        """Return None if the evidence is admissible, else the reason it is not."""
        ev = (evidence or "").strip()
        if not ev:
            return "no evidence"
        if ev.startswith("$ "):
            return None if len(ev) > 4 else "command evidence has no command"
        if ev.startswith("facts."):
            key = ev.split()[0].split("=")[0].rstrip(":,")[len("facts."):]
            if self.facts is None:
                return None
            return None if lookup_fact(self.facts, key) else f"facts key '{key}' does not exist"
        matches = list(evidence_locations(ev))
        if not matches:
            return "evidence must be path:line, '$ command -> output' or 'facts.<key>'"
        if self.repo is None:
            return None
        for m in matches:
            rel = m.group(1).replace("\\", "/")
            while rel.startswith("./"):
                rel = rel[2:]
            line = int(m.group(2))
            end = int(m.group(3)) if m.group(3) else line
            total = self._count_lines(rel)
            if total is None:
                return f"file '{rel}' not found in the repo"
            if line < 1 or end > total or end < line:
                return f"'{rel}' has {total} lines; line {line if end == line else f'{line}-{end}'} is out of range"
        return None


def normalise_judge(raw: dict, bailiff: Bailiff) -> dict:
    key = str(raw.get("judge", "")).strip().lower().replace("judge-", "") or "unknown"
    persona = str(raw.get("persona") or key.title()).strip()
    rubric = raw.get("rubric") or {}
    notes = []
    score = clamp_score(raw.get("score"))
    if isinstance(rubric, dict) and rubric:
        if len(rubric) != RUBRIC_CRITERIA:
            notes.append(f"rubric has {len(rubric)} criteria, expected {RUBRIC_CRITERIA}")
        try:
            values = [float(v) for v in rubric.values()]
            if any(v < 0 or v > RUBRIC_MAX for v in values):
                notes.append(f"rubric values outside 0-{RUBRIC_MAX:g} were clamped")
            rubric = {k: min(max(float(v), 0.0), RUBRIC_MAX) for k, v in rubric.items()}
            rubric_total = round(sum(rubric.values()), 1)
        except (TypeError, ValueError):
            rubric_total = None
            notes.append("rubric has non-numeric values; score kept as given")
        if rubric_total is not None and abs(rubric_total - score) > 0.05:
            notes.append(f"score {score} did not match rubric total {rubric_total}; rubric total used")
            score = clamp_score(rubric_total)
    else:
        notes.append("no rubric supplied; score is unjustified")
    admitted, rejected = [], []
    for roast in raw.get("roasts") or []:
        if not isinstance(roast, dict):
            continue
        line = str(roast.get("line", "")).strip()
        fix = str(roast.get("fix", "")).strip()
        evidence = str(roast.get("evidence", "")).strip()
        reason = None
        if not line:
            reason = "no roast line"
        elif not fix:
            reason = "no fix (every jab needs a fix)"
        else:
            reason = bailiff.check(evidence)
        impact = str(roast.get("impact", "medium")).lower()
        effort = str(roast.get("effort", "M")).upper()[:1]
        entry = {"line": line, "evidence": evidence, "fix": fix,
                 "impact": impact if impact in IMPACT else "medium",
                 "effort": effort if effort in EFFORT else "M"}
        if reason:
            entry["reason"] = reason
            rejected.append(entry)
        else:
            admitted.append(entry)
    return {
        "judge": key, "persona": persona, "score": score, "tier": tier(score),
        "rubric": rubric, "verdict": str(raw.get("verdict", "")).strip(),
        "praise": str(raw.get("praise", "")).strip(), "roasts": admitted,
        "rejected": rejected, "notes": notes,
    }


def order_key(judge: dict):
    key = judge["judge"]
    return (JUDGE_ORDER.index(key) if key in JUDGE_ORDER else len(JUDGE_ORDER), key)


def rank_fixes(judges: list[dict], limit: int = 10) -> list[dict]:
    pool = []
    for j_index, judge in enumerate(judges):
        for r_index, roast in enumerate(judge["roasts"]):
            impact, effort = IMPACT[roast["impact"]], EFFORT[roast["effort"]]
            pool.append(((-(impact / effort), -impact, effort, j_index, r_index),
                         {**roast, "judge": judge["judge"], "persona": judge["persona"]}))
    pool.sort(key=lambda item: item[0])
    ranked = [entry for _, entry in pool[:limit]]
    for i, entry in enumerate(ranked, 1):
        entry["rank"] = i
    return ranked


CREDENTIAL_WORDS = re.compile(r"(?i)\b(secret|credential|api[ _-]?key|access[ _-]?key|token|password|private key)s?\b")


def pick_fix_today(top: list[dict]) -> dict | None:
    # A leaked credential always goes first: rotating it can't wait for the effort ranking.
    for entry in top:
        if (entry.get("judge") == "security" and entry.get("impact") == "high"
                and CREDENTIAL_WORDS.search(entry.get("line", "") + " " + entry.get("fix", ""))):
            return entry
    for entry in top:
        if entry["effort"] == "S":
            return entry
    return top[0] if top else None


def load_judges(paths: list[Path]) -> list[dict]:
    raws: list[dict] = []
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"verdict.py: {f} is not valid JSON: {exc}") from None
        if isinstance(data, list):
            raws.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict) and isinstance(data.get("judges"), list):
            raws.extend(d for d in data["judges"] if isinstance(d, dict))
        elif isinstance(data, dict):
            raws.append(data)
    return raws


def build(raws: list[dict], *, repo_name: str, mode: str, date: str, headline: str | None,
          bailiff: Bailiff) -> dict:
    judges = sorted((normalise_judge(r, bailiff) for r in raws), key=order_key)
    overall = round(sum(j["score"] for j in judges) / len(judges), 1) if judges else 0.0
    top = rank_fixes(judges)
    return {
        "schema": 1, "repo": repo_name, "date": date, "mode": mode,
        "overall": overall, "tier": tier(overall),
        "headline": headline or DEFAULT_HEADLINES[tier(overall)],
        "judges": judges, "top_fixes": top, "fix_today": pick_fix_today(top),
        "admitted": sum(len(j["roasts"]) for j in judges),
        "thrown_out": sum(len(j["rejected"]) for j in judges),
    }


def card_payload(roast: dict) -> dict:
    return {
        "repo": roast["repo"], "overall": roast["overall"], "headline": roast["headline"],
        "date": roast["date"], "mode": roast["mode"],
        "judges": [{"name": j["persona"], "role": j["judge"].title(), "score": j["score"],
                    "quote": j["verdict"]} for j in roast["judges"]],
    }


def md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(roast: dict) -> str:
    o = []
    o.append(f"# The Roast of {roast['repo']}")
    o.append("")
    o.append(f"> {roast['headline']}")
    o.append("")
    o.append(f"**Overall: {roast['overall']:.1f}/10 ({roast['tier']})** · mode: {roast['mode']} · {roast['date']}")
    o.append("")
    o.append("![Roast card](card.svg)")
    o.append("")
    o.append("| Judge | Score | Verdict |")
    o.append("|---|---|---|")
    for j in roast["judges"]:
        o.append(f"| {md_cell(j['persona'])} ({j['judge']}) | {j['score']:.1f} | {md_cell(j['verdict'])} |")
    o.append("")
    o.append("## The one thing to fix today")
    o.append("")
    ft = roast["fix_today"]
    if ft:
        o.append(f"**{ft['fix']}**")
        o.append("")
        o.append(f"Why: {ft['line']} ({ft['persona']}, evidence: `{ft['evidence']}`)")
    else:
        o.append("Nothing. The panel left empty-handed.")
    o.append("")
    o.append("## Top fixes, ordered by impact for the effort")
    o.append("")
    if roast["top_fixes"]:
        o.append("| # | Fix | Evidence | Impact | Effort | Judge |")
        o.append("|---|---|---|---|---|---|")
        for f in roast["top_fixes"]:
            o.append(f"| {f['rank']} | {md_cell(f['fix'])} | `{md_cell(f['evidence'])}` | {f['impact']} | "
                     f"{f['effort']} | {md_cell(f['persona'])} |")
    else:
        o.append("No admissible fixes.")
    o.append("")
    o.append("## The panel")
    for j in roast["judges"]:
        o.append("")
        o.append(f"### {j['persona']} ({j['judge']}): {j['score']:.1f}/10")
        o.append("")
        if j["verdict"]:
            o.append(f"> {j['verdict']}")
            o.append("")
        for r in j["roasts"]:
            o.append(f"- **{r['line']}**")
            o.append(f"  - Evidence: `{r['evidence']}`" + (" (command output as reported, not re-run)" if r['evidence'].startswith('$ ') else ""))
            o.append(f"  - Fix: {r['fix']} _(impact {r['impact']}, effort {r['effort']})_")
        if not j["roasts"]:
            o.append("- No admissible roasts.")
        if j["praise"]:
            o.append("")
            o.append(f"Grudging praise: {j['praise']}")
        if isinstance(j["rubric"], dict) and j["rubric"]:
            parts = ", ".join(f"{k} {v}" for k, v in j["rubric"].items())
            o.append("")
            o.append(f"Scorecard: {parts}")
        for note in j["notes"]:
            o.append(f"Note: {note}")
    thrown = [(j, r) for j in roast["judges"] for r in j["rejected"]]
    if thrown:
        o.append("")
        o.append("## Thrown out by the bailiff")
        o.append("")
        o.append("These jabs had no admissible evidence, so they do not count:")
        o.append("")
        for j, r in thrown:
            o.append(f"- {j['persona']}: \"{r['line'] or '(empty)'}\" ({r['reason']})")
    o.append("")
    o.append("## How this was scored")
    o.append("")
    o.append("Each judge scores five criteria from 0 to 2 (total 0-10). The overall score is the plain "
             "average of the judges. A roast only counts if it cites a real `file:line`, a command and its "
             "output, or a fact from `facts.json`, and comes with a fix. Fixes are ranked by impact "
             "divided by effort. Tone mode changes the jokes, never the evidence bar.")
    o.append("")
    o.append(f"Admitted roasts: {roast['admitted']} · thrown out: {roast['thrown_out']}")
    o.append("")
    return "\n".join(o)


def archive_previous(out_dir: Path) -> Path | None:
    current = out_dir / "roast.json"
    if not current.exists():
        return None
    try:
        date = json.loads(current.read_text(encoding="utf-8")).get("date") or "undated"
    except ValueError:
        date = "undated"
    history = out_dir / "history"
    history.mkdir(parents=True, exist_ok=True)
    target = history / f"roast-{date}.json"
    n = 2
    while target.exists():
        target = history / f"roast-{date}-{n}.json"
        n += 1
    shutil.copyfile(current, target)
    return target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Assemble judge verdicts into roast.json, card.json and ROAST.md.")
    p.add_argument("--judges", nargs="+", required=True, help="judge JSON files or directories of them")
    p.add_argument("--repo", help="repo root; when given, every path:line is checked on disk")
    p.add_argument("--facts", help="facts.json from facts.py; enables checking facts.<key> evidence")
    p.add_argument("--out-dir", default="roast", help="output directory (default: roast)")
    p.add_argument("--repo-name", help="display name (default: repo folder name)")
    p.add_argument("--mode", choices=sorted(MODES), default="default")
    p.add_argument("--headline", help="one-line overall verdict (default: picked by score tier)")
    p.add_argument("--date", help="ISO date to stamp (default: today)")
    p.add_argument("--no-archive", action="store_true", help="do not copy an existing roast.json to history/")
    args = p.parse_args(argv)

    facts = json.loads(Path(args.facts).read_text(encoding="utf-8")) if args.facts else None
    repo = Path(args.repo) if args.repo else None
    raws = load_judges([Path(x) for x in args.judges])
    if not raws:
        print("error: no judge verdicts found", file=sys.stderr)
        return 2
    name = args.repo_name or (repo.resolve().name if repo else (facts or {}).get("repo", "this repo"))
    date = args.date or _dt.date.today().isoformat()
    roast = build(raws, repo_name=name, mode=args.mode, date=date, headline=args.headline,
                  bailiff=Bailiff(repo, facts))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    archived = None if args.no_archive else archive_previous(out)
    (out / "roast.json").write_text(json.dumps(roast, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "card.json").write_text(json.dumps(card_payload(roast), indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    (out / "ROAST.md").write_text(render_markdown(roast), encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"overall {roast['overall']:.1f}/10 ({roast['tier']}); admitted {roast['admitted']}, "
          f"thrown out {roast['thrown_out']}")
    if archived:
        print(f"previous roast archived to {archived.as_posix()}")
    print(f"wrote {(out / 'ROAST.md').as_posix()}, {(out / 'roast.json').as_posix()}, {(out / 'card.json').as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
