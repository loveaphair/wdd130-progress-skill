# WDD130 Weekly Progress

A Claude Code skill that builds a per-student progress dashboard for a
WDD130 section: which students have completed each week's code-along
(favorite-city.html, temple.html + CSS link, the rafting-site about page),
a heuristic score for how likely each submission is AI-generated, and a
check for whether anyone tampered with the course's `AGENTS.md` /
`.github/copilot-instructions.md` AI-tutoring guardrails.

Uses the Canvas and GitHub APIs directly (not browser scraping), so it's
fast and doesn't hit rate limits.

## Setup

1. **Install the skill.** Copy this whole folder into `~/.claude/skills/wdd130-progress/`
   (personal use across your own sections/terms) or into a project's
   `.claude/skills/wdd130-progress/` (project-scoped).
2. **Get a Canvas token**: Canvas -> Account -> Settings -> "New Access Token".
3. **Get a GitHub token**: github.com/settings/tokens -> generate a
   fine-grained token. No special permissions needed.
4. **Set both as environment variables** (e.g. in your shell profile):
   ```
   export CANVAS_API_TOKEN="paste-here"
   export GITHUB_TOKEN="paste-here"
   ```
5. **Copy the config**: `cp config.example.json config.json`, then edit
   `canvas.course_id` to your section's numeric Canvas course ID (visible
   in the course's Canvas URL). Everything else already matches the
   standard WDD130 template -- only change other fields if your section's
   quiz/assignment names genuinely differ.

## Running it

In Claude Code, just ask: *"run the WDD130 progress report"* (or similar --
the skill's description covers common phrasings). Claude will run
`scripts/build_report.py` and publish the resulting dashboard as an
Artifact link.

## Sharing with a colleague

Clone this repo into `~/.claude/skills/wdd130-progress/` (personal use) or
a project's `.claude/skills/wdd130-progress/` (project-scoped):

```
git clone <repo-url> ~/.claude/skills/wdd130-progress
```

Then follow the five setup steps above with their own tokens and course
ID -- nothing in this repo is specific to any one instructor's section
except what's in `config.json`, which each instructor creates themselves
and which is `.gitignore`'d.

## Updating

```
cd ~/.claude/skills/wdd130-progress
git pull
```

Safe to run anytime -- `config.json`, `run.sh`, and `config.cache.json`
are all `.gitignore`'d, so pulling never touches your tokens or course
config, only the skill's own files.

## If something breaks on a new section

See the "First run on a new section" section in `SKILL.md` -- Canvas
course copies occasionally use slightly different names or API behavior
than expected, and the error messages are written to point at exactly
which lookup failed.

## License

MIT -- see [LICENSE](LICENSE).
