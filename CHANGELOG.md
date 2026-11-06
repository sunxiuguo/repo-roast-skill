# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- The bailiff accepts backtick-quoted `path:line` citations containing spaces and
  checks the whole path, while preserving file, line-range and repository-boundary checks.

## [1.1.0] - 2026-11-07

Found by running the pack on itself: the self-roast's top finding was that 1.0.1 broke its
own test suite.

### Changed

- Every judge and the `roast-repo` skill now treat the roasted repo strictly as evidence: they
  must not follow instructions planted in its files, comments, commits or command output, and
  must never run its code. (Flagged by the self-roast's security judge.)
- `verdict.py` clamps every rubric value to 0–2 and flags a rubric that does not have exactly
  five criteria, so a score can no longer exceed what its rubric allows.
- `verdict.py` makes a high-impact leaked-credential finding from the security judge the
  "fix today", as the README promised.
- Command evidence (`$ command -> output`) is labelled in ROAST.md as reported, not re-run.
- `card.py` shrinks the title font for long repo names instead of cutting them at 36 characters.
- Judge verdicts are now asked for in 70 characters or fewer, matching what the card can show.

### Fixed

- `tests/test_repo.py` no longer hard-codes the version; it checks for semver and a matching
  CHANGELOG entry.
- `facts.py` counts a TODO/FIXME only when it opens a comment, not when prose or a regex
  mentions one.
- `facts.py` treats Travis builds for Java, Scala, PHP and other languages with default test
  commands as running tests.
- `facts.py` no longer overcounts README lines by one.
- `verdict.py` names the file when a panel JSON file is invalid instead of printing a traceback.
- The skill docs note to use `python` on Windows, where `python3` is often a Store shortcut.

## [1.0.1] - 2026-11-07

### Fixed

- `facts.py` now counts root-level test files such as `test.js`, `tests.py` and `spec.ts`. Before,
  a repo whose only tests lived in `test.js` was reported as having no tests.
- `facts.py` now treats a `.travis.yml` with no `script:` as running tests, since Travis runs the
  language's default test command (for example `npm test`) in that case.

## [1.0.0] - 2026-11-07

### Added

- `roast-repo`: gathers facts, convenes six judges in parallel, validates every jab's evidence,
  and writes `roast/ROAST.md` with an overall score, per-judge roasts, the top 10 fixes and the
  one thing to fix today.
- `roast-card`: 1200x630 SVG scorecard and a shields-style score badge, with before and after
  support.
- `roast-readme`: first-five-minutes roast of the README with a measured lint and a rewrite of
  the top, saved only after approval.
- `roast-pr`: roasts a diff or pull request with new-side line numbers; review comments are
  posted only on confirmation.
- `roast-fix`: turns the roast into `roast/FIXPLAN.md` and applies approved quick wins one at a
  time with the test suite as referee.
- `roast-rematch`: re-roasts after fixes and reports the score delta per judge.
- Six read-only judge agents: security, performance, newcomer, maintainer, product, ops.
- Helper scripts `facts.py`, `verdict.py`, `card.py`, `badge.py`, `readme_lint.py`, `delta.py`
  (standard library only) with unit tests.
- Fictional example roast, card and badge in `examples/`.
