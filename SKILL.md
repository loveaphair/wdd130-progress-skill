---
name: wdd130-progress
description: Builds the WDD130 Weekly Progress dashboard (per-week code-along completion, AI-usage signal scoring, and AGENTS.md guardrail tampering check) for a WDD130 section, using Canvas and GitHub API tokens. Use when the instructor asks to check/track/audit their WDD130 students' GitHub progress, run the weekly report, or refresh the progress dashboard.
---

# WDD130 Weekly Progress

Generates the tabbed HTML progress report (Dashboard + Week 01/02/03 tabs)
for a WDD130 section: which students have completed each week's code-along,
a heuristic AI-usage score per student per week, and a check for whether
anyone has modified or removed the course's `AGENTS.md` / `.github/copilot-
instructions.md` guardrail files (which instruct AI agents to tutor, not
write solutions).

This does the same thing an earlier session did by browser-scraping Canvas
and GitHub, but uses real API tokens instead -- much faster, and avoids
GitHub's 60/hour unauthenticated rate limit entirely (raised to 5,000/hour
with a token, and the GraphQL batching below uses only a handful of
requests for an entire class regardless of size).

## Before running

Two API tokens are required, both read-only. Claude must never write a
token into any file, and should not ask the instructor to paste one into
chat -- but pasting long tokens through the chat `!` bridge as an inline
`export ... && python3 ...` command has repeatedly gotten corrupted in
practice (the bridge can mangle line breaks/whitespace in long or
multi-line pasted commands). Prefer this instead:

1. Check whether `run.sh` exists next to this file. If not, tell the
   instructor to copy `run.sh.example` to `run.sh` **themselves, in a text
   editor** (not by pasting into chat) and fill in their two tokens there:
   - **Canvas**: generated at Canvas -> Account -> Settings -> "New Access
     Token".
   - **GitHub**: a fine-grained token at github.com/settings/tokens. No
     special scopes needed -- student repos are public; it only exists to
     raise the API rate limit.
2. From then on, running the report is just `bash run.sh` -- a short
   command that isn't vulnerable to the long-line paste corruption. This
   also means the instructor doesn't need to re-paste tokens every run.

`run.sh` is gitignored. If the instructor would rather not keep a
tokens-bearing file around at all, the fallback is the old one-shot
approach -- export both tokens and run the script in a single command,
typed directly into a real terminal window (not the chat `!` bridge, which
is what actually broke) -- see the `python3 scripts/build_report.py`
invocation below.

## Configuration

Check whether `config.json` exists next to this file. If not, copy
`config.example.json` to `config.json` and ask the instructor for the one
value that's always section-specific:

- `canvas.course_id` -- the numeric Canvas course ID for their section.

Everything else in `config.example.json` already matches the standard
WDD130 course template (same quiz name, same assignment names, same
`week01`-`week05`/`wwr` folder structure, same guardrail file paths) shared
across sections. Only ask about the other fields if the instructor mentions
their section's names or structure differ from the defaults -- don't
interrogate them about fields that are almost certainly already correct.

If `canvas.domain` isn't `https://byupw.instructure.com` (a different
institution reusing this course), ask for the correct Canvas domain too.

## Running it

**Before running, ask the instructor which week of the course to check
through (1-5).** There's no point checking (or showing) weeks the class
hasn't reached yet -- early in the term this also cuts down on wasted
GitHub API calls for files that don't exist. Pass their answer as
`--week N`; if they want the full history regardless of pacing, omit the
flag (defaults to checking all 5 weeks).

Preferred: `bash run.sh --week N` (see "Before running" above).

Fallback, in a real terminal window (not the chat `!` bridge):
```
export CANVAS_API_TOKEN="..." GITHUB_TOKEN="..." && python3 scripts/build_report.py --week N
```

(append `path/to/other-config.json` as an extra argument to either form for
a non-default config location -- useful if the instructor keeps configs
for multiple sections/terms side by side). Weeks beyond the one requested
show a "not checked this run" placeholder in the report rather than being
computed.

### Caching

Every run writes a cache file next to the config it used (`config.cache.json`
-- gitignored, one per config so multiple sections don't clobber each
other). **By default, a run reuses that cache and just re-renders the
report -- no GitHub or Canvas API calls beyond a fresh roster fetch.** Three
things force a real check instead:

- **A student not yet in the cache** (new roster addition) is always
  fetched fresh automatically, no flag needed.
- **Asking for a later week than what's cached** (e.g. cached through
  week3, now asked for week5) triggers a full recheck of everyone, since a
  full GraphQL-batched check is already fast. It never checks fewer weeks
  than the cache already has, even if a lower `--week N` is given.
- **The instructor says specific students resubmitted or just submitted**
  ("Bob and Alice just turned in week03") -- pass `--update "Bob,Alice"`
  (comma-separated names or GitHub usernames; matched against cached
  usernames and current roster names, erroring out with the candidate list
  on an ambiguous or missing match). Only those students get a fresh
  GitHub/Canvas check; everyone else comes straight from cache.

To ignore the cache entirely and recheck every single active student,
pass `--recheck-all`. Use this when the instructor explicitly asks to
re-verify everyone (e.g. "just double check the whole class"), not as a
routine default -- that defeats the point of caching.

This prints progress as it works through: roster fetch, quiz/assignment
resolution, the batched GitHub GraphQL passes, then writes
`wdd130_progress_report.html` to the current directory.

**Publish the result with the Artifact tool** so the instructor gets a
shareable link, same as any other artifact. Read the generated HTML file's
size is fine to skip -- just call the Artifact tool with the file path.

## First run on a new section

The very first run against a course Claude hasn't touched before is the
one likely to surface an API-shape surprise (Canvas's quiz-submission-
questions endpoint has some version-dependent quirks). If `build_report.py`
raises a `CanvasError` or `GitHubError`, read the message -- it names the
exact endpoint and HTTP status. Common issues:

- **"No quiz/assignment named X found"**: the section's copy of the course
  uses a different name for that quiz/assignment. Ask the instructor, or
  list what Canvas actually has (`canvas_api.py`'s `resolve_quiz_id` /
  `resolve_assignment_id` print the available names in the error).
- **Quiz answers all come back empty**: the token's user may lack
  grading/TA permission on the course, or the quiz submission questions
  endpoint needs an additional parameter for this Canvas version -- check
  `canvas_api.get_quiz_answers` and adjust as needed. (`_paginated` already
  handles Canvas wrapping a paginated array in a `{"resource_name": [...]}`
  object instead of returning it bare -- that bit the quiz-submissions
  endpoint specifically and is fixed, so look elsewhere first.)
- **`resolve_quiz_question_id` can't find the hint text**: the course's
  rich-text quiz question often has HTML-entity-injected double spaces
  (e.g. `&nbsp;`) splitting words apart after tag-stripping, so a
  multi-word hint like `"GitHub username"` can fail to match text that
  reads identically on screen. The error lists every question's cleaned
  text -- pick a single distinctive word instead of a phrase.
- **GitHub GraphQL "NOT_FOUND" for everyone**: double check
  `github.repo_name` in config matches what the template actually produces.

Fix forward in the scripts as needed -- they're plain, short, readable
Python specifically so this is easy to patch per-section without needing to
understand the whole pipeline.

## What each script does

- `scripts/canvas_api.py` -- token-authenticated Canvas REST calls: active
  roster, quiz question resolution, per-student quiz answer text, and
  assignment submission URLs. Replaces the original session's browser
  navigation + `get_page_text` scraping of quiz history pages and
  SpeedGrader's cross-origin iframe.
- `scripts/github_graphql.py` -- batches many students' file/folder checks
  into single GraphQL queries (same technique as
  `byupathway/wdd130/AppScript_GitHubTracker.ts`, extended to batch across
  students, not just repo-name variations for one student). `HEAD:path`
  expressions resolve against each repo's actual default branch, so there's
  no main-vs-master guessing.
- `scripts/ai_signals.py` -- the heuristic AI-usage scanner. Read its
  module docstring before changing the weights -- several signals that
  seemed obvious in testing (semantic-tag presence, indentation
  cleanliness, CSS custom properties, heavy CSS resets) were deliberately
  removed or down-weighted because they're taught/expected in this course
  and didn't discriminate in practice. Scored against each week's *main
  assignment* (index.html for weeks 1-2, wwr/about.html for weeks 3-4,
  wwr/contact.html for week5) -- not the lower-stakes code-along files.
- `scripts/build_report.py` -- orchestrates everything above, gates which
  weeks get fetched/checked based on `--week N` (see "Running it"), and
  injects the result into `assets/report_template.html`. Each week's
  `checks.weekNN` config entry can define both a `codealong` check (the
  guided in-class exercise) and a `mainAssignment`/cursory check (the
  graded deliverable) -- they're tracked and displayed separately since a
  student can be caught up on one and behind on the other.
  **Code-along checks are self-report-gated**: a student's technical
  result (pass/fail/etc.) only counts once they've scored at least
  `canvas.codealong_selfreport_min_score` on that week's
  `canvas.codealong_selfreport_assignments[weekNN]` Canvas assignment.
  Below that, the status is `not_self_reported` regardless of what their
  code actually shows -- per the instructor, a student who never claimed
  credit for the work isn't worth flagging against the technical bar. Main
  assignments (about.html, contact.html, the Home Page) are **not** gated
  this way -- they're separately graded, not self-reported.
- `assets/report_template.html` -- the report page itself (design, tabs,
  clickable signal-detail badges, a Course Home link + Page Audit Tool
  dropdown above the tabs that opens the matching
  `byui-cse.github.io/wdd-audits/...` page per week in a new tab). Only
  touch this for a visual/behavior
  change, not for anything section-specific -- section-specific values are
  template placeholders (`__COURSE_ID__`, `__QUIZ_NAME__`, etc.) filled in
  by `build_report.py`, not hardcoded.

## Sharing with another WDD130 instructor

Hand them this whole folder. Their setup is: generate their own two
tokens, copy `config.example.json` to `config.json`, fill in their
`course_id`, copy `run.sh.example` to `run.sh` and fill in their tokens
there, then `bash run.sh`. No other WDD130-specific knowledge should be
required since the defaults already match the shared course template.
