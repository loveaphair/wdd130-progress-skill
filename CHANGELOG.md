# Changelog

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
