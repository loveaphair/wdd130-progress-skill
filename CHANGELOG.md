# Changelog

## 2026-09-21

### Changed
- Raised several AI-usage signal weights in `ai_signals.py`: JavaScript
  present (15 -> 50), ARIA/role attributes (10 -> 30), embedded
  `<style>` block (15 -> 20). None of these are taught in weeks 1-5, so
  their presence is a stronger tell than the old weights gave it credit
  for.
- CSS rule-count scoring now has three tiers instead of two: "moderate"
  at 30+ rules (was 20+), "high" at 60+ rules (was 40+), and a new "very
  high" tier at 100+ rules (+30) to separate genuinely bloated
  stylesheets from merely thorough ones.

## 2026-09-20

### Fixed
- A quiz answer with a character GitHub doesn't allow in a username (e.g.
  a `:` typed where a `-` belonged) was accepted as-is instead of being
  rejected -- it silently fell out of the batched GitHub query later,
  reporting every week as "no GitHub username found for this student"
  even though a (garbled) username had, in fact, been found. Username
  resolution now validates against GitHub's actual rules (letters,
  numbers, hyphens only; no leading/trailing/doubled hyphens; max 39
  chars) before trusting any candidate.
- A student who scored 0 on the Home Page assignment because they linked
  their raw GitHub repo instead of their published Pages site was treated
  as no better resolved than a stale quiz answer. Resolution now checks
  a 0-graded (or ungraded) Home Page submission's username for an actual
  non-empty wdd130 repo (or a configured name variation) before falling
  back to the quiz -- and an unsubmitted Home Page assignment is no
  longer used as a username source at all, graded or not.
- A quiz-derived username is now confirmed to exist as a real GitHub
  account (independent of whether it has a matching repo) before being
  trusted, rather than being used purely on format.
- The "no GitHub username found for this student" reason no longer shows
  up for a student whose username *was* resolved but whose repo just
  wasn't found -- that case now says so explicitly (naming the
  username), instead of implying nothing was ever found.

### Added
- Dashboard's Username column now shows 🛠️ for a username resolved from
  the quiz answer, or 📝 for one resolved from a Home Page submission, so
  the instructor can see at a glance where each came from (hover for the
  full explanation). An instructor-entered `username_overrides` entry
  gets no icon, since there's no ambiguity about its source.

## 2026-09-19

### Fixed
- Username resolution trusted a student's W01 setup quiz answer first,
  even when that answer named a real but abandoned/stale GitHub account.
  A student who started over under a different account -- with only
  their *graded* Home Page assignment submission reflecting it -- kept
  getting checked against their old, empty repo and misreported as
  behind. Resolution now prefers the Home Page assignment's submitted
  URL whenever that assignment is graded above 0, and falls back to the
  quiz answer otherwise (the Home Page URL is still tried last if the
  quiz answer doesn't parse at all). This costs no extra API calls -- the
  Home Page assignment's submissions were already being fetched for
  other reasons and are now reused instead of duplicated.
- Rechecking a full section under this fix corrected 4 students' resolved
  usernames to their actual current repos.
- The Dashboard's GitHub Repo column sorted by username instead of the
  actual repo link, so a student with a resolved username but no
  matching repo landed in the middle of the list instead of grouped
  with true blanks -- now sorts by the repo URL itself. Guardrail
  sorted alphabetically instead of by severity -- now sorts Removed >
  Modified > Partial > Unreachable > N/A > OK, with not-yet-checked
  rows grouped with other blanks (first ascending, last descending).

### Added
- `github.username_overrides` in `config.json`: an optional
  `{"Roster Name": "github-username"}` map for the rare case where
  automatic resolution still can't find a student's real account. Empty
  by default.
- Dashboard table is now sortable by any column -- click a header to
  sort A-Z, click again to reverse.

### Fixed (Windows)
- `config.json`/`config.cache.json` were opened without an explicit
  encoding, so on Windows Python read/wrote them using the system's ANSI
  codepage instead of UTF-8. `config.example.json` ships a literal
  non-ASCII character (the middle dot in "WDD130 &middot; Web
  Fundamentals"), which every instructor inherits by copying it to
  `config.json` -- depending on the machine's codepage this silently
  mangled that text or crashed outright with a UnicodeDecodeError. All
  file opens in `build_report.py` now explicitly use `encoding="utf-8"`.
- Also reconfigured stdout/stderr to UTF-8 at startup, since printing a
  student name with an accented character (many rosters have them) could
  otherwise crash on Windows' legacy console codepage.
