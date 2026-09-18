#!/usr/bin/env python3
"""
Builds the WDD130 Weekly Progress report using Canvas + GitHub API tokens
instead of browser scraping.

Requires environment variables:
    CANVAS_API_TOKEN  -- Canvas personal access token
    GITHUB_TOKEN      -- GitHub personal access token (no special scopes needed)

Usage:
    python3 build_report.py [path/to/config.json]

Defaults to config.json in the same directory as config.example.json if no
path is given. Writes the final report to ./wdd130_progress_report.html in
the current directory -- publish that file as a Claude Artifact.
"""
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import canvas_api  # noqa: E402
import github_graphql  # noqa: E402
import ai_signals  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)


def load_config(path=None):
    path = path or os.path.join(SKILL_DIR, "config.json")
    if not os.path.exists(path):
        example = os.path.join(SKILL_DIR, "config.example.json")
        raise SystemExit(
            f"No config.json found at {path}.\n"
            f"Copy {example} to config.json and fill in your course_id "
            f"(and any assignment/quiz names that differ from the WDD130 default)."
        )
    with open(path) as f:
        return json.load(f)


def extract_username(raw):
    """Same heuristic used in the original manual review: pulls a plausible
    GitHub username out of free-text quiz answers, flagging the ones that
    clearly aren't one (blank, an email, a full name, "yes", etc.)."""
    if not raw:
        return None, "blank/unanswered"
    raw = raw.strip()
    if not raw:
        return None, "blank/unanswered"

    if re.match(r"^[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+$", raw):
        return None, "looks like an email, not a username"

    # an explicit "my username is X" statement is more authoritative than a
    # URL elsewhere in the answer, whose casing may not match what they typed
    first_line = raw.split("\n")[0].strip()
    m2 = re.match(r"^(?:my github username(?: is)?|usuario)\s*:?\s*(.+)$", first_line, re.I)
    if m2:
        candidate = m2.group(1).strip().rstrip(".").lstrip("@")
        if " " not in candidate:
            return candidate.split("/")[0], None

    m = re.search(r"github\.com/([A-Za-z0-9_.-]+)", raw)
    if m:
        return m.group(1).split("/")[0], None
    m = re.search(r"https?://([A-Za-z0-9-]+)\.github\.io", raw)
    if m:
        return m.group(1), None

    candidate = first_line.lstrip("@").strip()
    if " " in candidate:
        return None, f'looks like a name/phrase, not a username: "{candidate}"'
    if candidate.lower() in ("yes", "no", "okay", "ok", "true", "false", "done"):
        return None, f'not a username: "{candidate}"'
    candidate = candidate.split("/")[0]
    return (candidate or None), (None if candidate else "could not parse")


def username_from_url(url):
    if not url:
        return None
    m = re.search(r"https?://([A-Za-z0-9-]+)\.github\.io", url)
    if m:
        return m.group(1)
    m = re.search(r"github\.com/([A-Za-z0-9_.-]+)", url)
    if m:
        return m.group(1)
    return None


def find_css_links(html, base_path):
    if not html:
        return []
    hrefs = re.findall(r'<link[^>]+rel=["\']?stylesheet["\']?[^>]*href=["\']([^"\']+)["\']', html, re.I)
    hrefs += re.findall(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\']?stylesheet["\']?', html, re.I)
    out = []
    for h in hrefs:
        if h.startswith("http"):
            continue
        out.append(urllib.parse.urljoin(base_path, h))
    return list(dict.fromkeys(out))


def has_css_link(html):
    if not html:
        return False
    return bool(re.search(r"<link[^>]+\.css", html, re.I)) or bool(
        re.search(r"<link[^>]+rel=[\"']?stylesheet", html, re.I)
    )


def has_input(html):
    return bool(html) and bool(re.search(r"<input\b", html, re.I))


def has_class(html, tag, class_name):
    """True if html has a <tag ...> whose class attribute includes class_name
    (among possibly several classes)."""
    if not html:
        return False
    for m in re.finditer(rf'<{tag}\b[^>]*\bclass=["\']([^"\']*)["\']', html, re.I):
        if class_name in m.group(1).split():
            return True
    return False


def _css_rules(css_text):
    """Yields (selectors_lowercased_list, body) for each rule in css_text."""
    if not css_text:
        return
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css_text):
        selectors = [s.strip().lower() for s in m.group(1).split(",") if s.strip()]
        yield selectors, m.group(2)


def css_selectors_grouped(css_text, *names):
    """True if some rule's comma-separated selector list includes all of
    `names` together (order and whitespace don't matter)."""
    wanted = {n.lower() for n in names}
    return any(wanted.issubset(set(selectors)) for selectors, _ in _css_rules(css_text))


def css_selector_has_property(css_text, selector, prop, value):
    """True if a rule whose selectors include `selector` declares
    prop: value (whitespace around the colon doesn't matter)."""
    pattern = re.compile(rf"{re.escape(prop)}\s*:\s*{re.escape(value)}", re.I)
    for selectors, body in _css_rules(css_text):
        if selector.lower() in selectors and pattern.search(body):
            return True
    return False


def css_has_property_anywhere(css_text, prop, value):
    if not css_text:
        return False
    return bool(re.search(rf"{re.escape(prop)}\s*:\s*{re.escape(value)}", css_text, re.I))


def parse_args(argv):
    """Returns (config_path, current_week, recheck_all, update_query).
    --week N caps which weeks get fetched/checked; --recheck-all forces a
    full fresh check of everyone, ignoring the cache; --update "name,name"
    refreshes just the named student(s) from a comma-separated free-text
    list (GitHub usernames or name substrings), reusing the cache for
    everyone else. config_path is whatever positional arg is left."""
    config_path = None
    current_week = 5
    recheck_all = False
    update_query = None
    i = 0
    while i < len(argv):
        if argv[i] == "--week" and i + 1 < len(argv):
            current_week = int(argv[i + 1])
            i += 2
        elif argv[i] == "--recheck-all":
            recheck_all = True
            i += 1
        elif argv[i] == "--update" and i + 1 < len(argv):
            update_query = argv[i + 1]
            i += 2
        else:
            config_path = argv[i]
            i += 1
    if not 1 <= current_week <= 5:
        raise SystemExit(f"--week must be 1-5, got {current_week}")
    if recheck_all and update_query:
        raise SystemExit("--recheck-all and --update are mutually exclusive.")
    return config_path, current_week, recheck_all, update_query


def _week_num(folder_name):
    m = re.search(r"(\d+)$", folder_name)
    return int(m.group(1)) if m else 0


def cache_path_for(config_path):
    """Cache lives next to the config it belongs to, so multiple
    sections/terms with their own config.json get their own cache instead
    of clobbering each other."""
    base = config_path or os.path.join(SKILL_DIR, "config.json")
    root, _ = os.path.splitext(base)
    return root + ".cache.json"


def load_cache(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(path, cache):
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def resolve_update_targets(query, roster, cache):
    """Resolves a comma-separated free-text query to a set of student ids
    -- matched against cached GitHub usernames (exact) and current roster
    names (substring). Raises SystemExit with a clear message when a term
    matches zero or multiple students, so the caller can ask for
    clarification instead of silently guessing."""
    cached_students = (cache or {}).get("students", {})
    sids = set()
    for term in [t.strip() for t in query.split(",") if t.strip()]:
        term_l = term.lower()
        uname_matches = [
            sid for sid, d in cached_students.items()
            if d.get("username") and d["username"].lower() == term_l
        ]
        name_matches = [u["id"] for u in roster if term_l in u["name"].lower()]
        combined = list(dict.fromkeys(uname_matches + name_matches))
        if len(combined) == 1:
            sids.add(combined[0])
        elif len(combined) == 0:
            raise SystemExit(
                f'--update: no student matches "{term}" '
                f"(checked cached GitHub usernames and current roster names)."
            )
        else:
            names = [next((u["name"] for u in roster if u["id"] == sid), sid) for sid in combined]
            raise SystemExit(f'--update: "{term}" matches multiple students: {", ".join(names)}. Be more specific.')
    return sids


def main():
    config_path, current_week, recheck_all, update_query = parse_args(sys.argv[1:])
    cfg = load_config(config_path)
    canvas = cfg["canvas"]
    gh = cfg["github"]
    checks = cfg["checks"]

    domain = canvas["domain"]
    course_id = canvas["course_id"]

    cache_path = cache_path_for(config_path)
    cache = load_cache(cache_path)
    cached_students = (cache or {}).get("students", {})
    cached_week = (cache or {}).get("current_week", 0)

    print("Fetching active roster...")
    roster = canvas_api.get_active_roster(domain, course_id)
    print(f"  {len(roster)} active students")
    roster_by_sid = {u["id"]: u for u in roster}
    active_sids = set(roster_by_sid)

    # ---- decide what actually needs a fresh Canvas/GitHub check ----
    # Default: reuse the cache and just re-render (no API calls beyond the
    # roster fetch above). --recheck-all forces everyone fresh. Asking for a
    # later week than what's cached also forces everyone fresh, since a full
    # check is already cheap thanks to GraphQL batching. --update (or a
    # newly-active student the cache has never seen) triggers a scoped
    # re-fetch of just those students, reusing cache for everyone else.
    if cache is None or recheck_all:
        effective_week = current_week
        full_refresh = True
        fetch_sids = set(active_sids)
        if cache is None:
            print("No cache found -- running a full check.")
        else:
            print("Rechecking every student (--recheck-all)...")
    else:
        effective_week = max(current_week, cached_week)
        full_refresh = effective_week > cached_week
        if full_refresh:
            fetch_sids = set(active_sids)
            print(f"Requested week {current_week} > cached week {cached_week} -- running a full recheck.")
        else:
            fetch_sids = {sid for sid in active_sids if sid not in cached_students}
            if update_query:
                targeted = resolve_update_targets(update_query, roster, cache)
                new_joins = len(fetch_sids)
                fetch_sids |= targeted
                extra = f" plus {new_joins} newly-active student(s) not yet cached" if new_joins else ""
                print(f"Updating {len(targeted)} requested student(s){extra}; reusing cache for everyone else.")
            elif fetch_sids:
                print(f"{len(fetch_sids)} newly-active student(s) not yet cached -- "
                      f"fetching just them; reusing cache for everyone else.")
            else:
                print("Nothing new to check -- regenerating the report straight from cache (no GitHub/Canvas calls).")

    fresh_entries = {}
    canonical = (cache or {}).get("guardrail_canonical", {}) or {}

    if fetch_sids:
        print("Resolving quiz + question...")
        quiz_id = canvas_api.resolve_quiz_id(domain, course_id, canvas["quiz_name"])
        question_id = canvas_api.resolve_quiz_question_id(
            domain, course_id, quiz_id, canvas["quiz_question_hint"]
        )
        quiz_answers = canvas_api.get_quiz_answers(domain, course_id, quiz_id, question_id)

        print("Resolving Home Page assignment (fallback for missing usernames)...")
        home_assignment_id = canvas_api.resolve_assignment_id(
            domain, course_id, canvas["home_page_assignment_name"]
        )
        home_urls = canvas_api.get_assignment_submission_urls(domain, course_id, home_assignment_id)

        print("Resolving weekly code-along self-report assignments...")
        selfreport_names = canvas["codealong_selfreport_assignments"]
        selfreport_ids = canvas_api.resolve_assignment_ids(domain, course_id, list(selfreport_names.values()))
        selfreport_ids = {wk: selfreport_ids[name] for wk, name in selfreport_names.items()}
        selfreport_scores = {
            wk: canvas_api.get_assignment_scores(domain, course_id, aid)
            for wk, aid in selfreport_ids.items()
        }
        selfreport_min_score = canvas.get("codealong_selfreport_min_score", 5)

        def self_reported(sid, wk):
            score = selfreport_scores.get(wk, {}).get(sid)
            return score is not None and score >= selfreport_min_score

        def selfreport_link(sid, wk):
            aid = selfreport_ids.get(wk)
            return f"{domain}/courses/{course_id}/assignments/{aid}/submissions/{sid}" if aid else None

        students = []
        for sid in fetch_sids:
            u = roster_by_sid[sid]
            raw = quiz_answers.get(sid)
            username, flag = extract_username(raw)
            source = "quiz"
            if not username:
                fallback_url = home_urls.get(sid)
                fb_username = username_from_url(fallback_url)
                if fb_username:
                    username = fb_username
                    flag = None
                    source = "home_page_fallback"
            students.append({
                "sid": sid, "name": u["name"], "username": username,
                "flag": flag, "source": source,
            })

        print(f"  {sum(1 for s in students if s['username'])} usable usernames, "
              f"{sum(1 for s in students if not s['username'])} unresolved")

        # ---- GitHub: single batched pass for fixed-path files + folders ----
        # Only fetch what `effective_week` actually calls for -- early in the
        # term there's no point checking (or showing) weeks the class hasn't
        # reached.
        file_paths = [checks["ai_scan_root_file"]]
        file_paths += checks["week01"]["candidate_files"]
        if effective_week >= 2:
            file_paths.append(checks["week02"]["file"])
        if effective_week >= 3:
            file_paths.append(checks["week03"]["file"])
        if effective_week >= 5:
            file_paths.append(checks["week05"]["codealong_file"])
            file_paths.append(checks["week05"]["main_assignment_file"])
        file_paths += cfg["guardrail_files"]
        folder_paths = [wk for wk in cfg["week_folders_to_check"] if _week_num(wk) <= effective_week]

        print(f"Querying GitHub for {len(file_paths)} files x "
              f"{sum(1 for s in students if s['username'])} students (batched)...")
        gh_results = github_graphql.check_students_files(
            students, gh["repo_name"], file_paths, folder_paths
        )

        missing = [s for s in students if s["username"] and gh_results.get(s["sid"]) is None]
        if missing and gh.get("repo_name_variations"):
            print(f"  {len(missing)} repos not found under '{gh['repo_name']}', trying variations...")
            found, still_missing = github_graphql.check_repo_variations(
                missing, gh["repo_name_variations"], file_paths, folder_paths
            )
            gh_results.update(found)
            print(f"  found {len(found)} more, {len(still_missing)} still not found")

        # ---- second pass: CSS files, whose paths vary per student ----
        css_lookup_items = []
        for s in students:
            r = gh_results.get(s["sid"])
            if not r:
                continue
            repo_used = r.get("repo_name_used", gh["repo_name"])
            index_html = r["files"].get(checks["ai_scan_root_file"])
            temple_html = r["files"].get(checks["week02"]["file"])
            about_html = r["files"].get(checks["week03"]["file"])
            paths = set()
            paths.update(find_css_links(index_html, checks["ai_scan_root_file"]))
            paths.update(find_css_links(temple_html, checks["week02"]["file"]))
            paths.update(find_css_links(about_html, checks["week03"]["file"]))
            if paths:
                css_lookup_items.append({
                    "sid": s["sid"], "username": s["username"], "repo": repo_used,
                    "paths": sorted(paths),
                })
        print(f"Fetching {sum(len(i['paths']) for i in css_lookup_items)} discovered CSS files...")
        css_results = github_graphql.check_custom_paths(css_lookup_items)

        # ---- assemble per-student dataset (only for the students we fetched) ----
        guardrail_raw = {f: {} for f in cfg["guardrail_files"]}

        for s in students:
            sid = s["sid"]
            username = s["username"]
            r = gh_results.get(sid)
            repo_used = (r or {}).get("repo_name_used", gh["repo_name"])
            branch = (r or {}).get("branch", "main")
            root = r["url"] if r else None

            week_links = {}
            for wk in folder_paths:
                present = bool(r and r["folders"].get(wk))
                week_links[wk] = f"{root}/tree/{branch}/{wk}" if (present and root) else None

            index_html = (r or {}).get("files", {}).get(checks["ai_scan_root_file"])
            index_link = f"{root}/blob/{branch}/{checks['ai_scan_root_file']}" if root else None
            css_text_all = "\n".join(v for v in css_results.get(sid, {}).values() if v)
            wwr_prefix = checks["week03"]["file"].rsplit("/", 1)[0]
            wwr_css_text = "\n".join(
                v for k, v in css_results.get(sid, {}).items() if v and wwr_prefix in k
            ) or None

            # week01 -- the file/link is always looked up so the report can
            # link to a student's code even when they haven't self-reported;
            # only the pass/fail *status* is gated on self-report (per
            # instructor's explicit call: technical result doesn't matter
            # for someone who never claimed credit for the work).
            w01_status = "no_data"
            w01_file, w01_link = None, None
            w01_sr_link = selfreport_link(sid, "week01")
            if r:
                file_status = "no_template" if not week_links.get("week01") else "no_file"
                for cand in checks["week01"]["candidate_files"]:
                    content = r["files"].get(cand)
                    if content is not None:
                        w01_file = cand.split("/")[-1]
                        w01_link = f"{root}/blob/{branch}/{cand}"
                        file_status = "pass" if content.strip() else "no_file"
                        break
                w01_status = file_status if self_reported(sid, "week01") else "not_self_reported"

            # week02 -- same self-report gate as week01
            w02_status = "no_data" if effective_week >= 2 else "not_checked"
            w02_link = None
            w02_sr_link = selfreport_link(sid, "week02")
            if effective_week >= 2 and r:
                temple = r["files"].get(checks["week02"]["file"])
                if temple is not None:
                    w02_link = f"{root}/blob/{branch}/{checks['week02']['file']}"
                    file_status = "pass" if has_css_link(temple) else "fail_no_link"
                else:
                    file_status = "no_template" if not week_links.get("week02") else "fail_no_link"
                w02_status = file_status if self_reported(sid, "week02") else "not_self_reported"

            # week03 main assignment (cursory AI signal only)
            about_html = (r or {}).get("files", {}).get(checks["week03"]["file"]) if effective_week >= 3 else None
            w03_ai = None
            if about_html is not None:
                w03_ai = ai_signals.combined_score(about_html, wwr_css_text)
            else:
                w03_ai = {"checked": False, "score": None, "level": None, "signals": []}

            # week03 code-along: root index.html has <ul class="box"> and an
            # <aside> also carrying class="box" -- self-report gated
            w03_ca_status = "no_data" if effective_week >= 3 else "not_checked"
            w03_ca_detail = {}
            w03_ca_sr_link = selfreport_link(sid, "week03")
            if effective_week >= 3 and r:
                if not self_reported(sid, "week03"):
                    w03_ca_status = "not_self_reported"
                elif not index_html:
                    w03_ca_status = "no_file"
                else:
                    w03_ca_detail = {
                        "ul": has_class(index_html, "ul", checks["week03"]["codealong_ul_class"]),
                        "aside": has_class(index_html, "aside", checks["week03"]["codealong_aside_class"]),
                    }
                    w03_ca_status = "pass" if all(w03_ca_detail.values()) else "fail"

            # week04 code-along: root index.html's linked CSS has a combined
            # header+footer selector, a nav rule with display:flex, and a
            # display:grid declaration somewhere -- self-report gated
            w04_ca_status = "no_data" if effective_week >= 4 else "not_checked"
            w04_ca_detail = {}
            w04_ca_sr_link = selfreport_link(sid, "week04")
            if effective_week >= 4 and r:
                if not self_reported(sid, "week04"):
                    w04_ca_status = "not_self_reported"
                elif not css_text_all:
                    w04_ca_status = "no_css"
                else:
                    sel_a, sel_b = checks["week04"]["css_combined_selector"]
                    w04_ca_detail = {
                        "pair": css_selectors_grouped(css_text_all, sel_a, sel_b),
                        "navFlex": css_selector_has_property(
                            css_text_all, "nav", "display", checks["week04"]["css_nav_display_property"]
                        ),
                        "grid": css_has_property_anywhere(
                            css_text_all, "display", checks["week04"]["css_required_display_property"]
                        ),
                    }
                    w04_ca_status = "pass" if all(w04_ca_detail.values()) else "fail"

            # week04 main assignment: wwr stylesheet (e.g. rafting.css) should
            # show a grid or flex display rule by now
            w04_main_status = "no_data" if effective_week >= 4 else "not_checked"
            if effective_week >= 4 and r:
                if not wwr_css_text:
                    w04_main_status = "no_css"
                else:
                    found = any(
                        css_has_property_anywhere(wwr_css_text, "display", p)
                        for p in checks["week04"]["main_assignment_display_properties"]
                    )
                    w04_main_status = "pass" if found else "fail"

            # week05 code-along: week05/quiz.html exists, links a stylesheet,
            # and has at least one <input> -- file/link always looked up,
            # only the pass/fail status is self-report gated
            quiz_html = (r or {}).get("files", {}).get(checks["week05"]["codealong_file"]) if effective_week >= 5 else None
            w05_ca_status = "no_data" if effective_week >= 5 else "not_checked"
            w05_ca_missing = []
            w05_ca_link = None
            w05_ca_sr_link = selfreport_link(sid, "week05")
            if effective_week >= 5 and r:
                if quiz_html is None:
                    file_status = "no_file" if week_links.get("week05") else "no_folder"
                else:
                    w05_ca_link = f"{root}/blob/{branch}/{checks['week05']['codealong_file']}"
                    if checks["week05"]["codealong_requires_css_link"] and not has_css_link(quiz_html):
                        w05_ca_missing.append("css_link")
                    if checks["week05"]["codealong_requires_input"] and not has_input(quiz_html):
                        w05_ca_missing.append("input")
                    file_status = "pass" if not w05_ca_missing else "fail"
                w05_ca_status = file_status if self_reported(sid, "week05") else "not_self_reported"

            # week05 main assignment: wwr/contact.html has real content
            contact_html = (r or {}).get("files", {}).get(checks["week05"]["main_assignment_file"]) if effective_week >= 5 else None
            w05_main_status = "no_data" if effective_week >= 5 else "not_checked"
            w05_main_link = None
            if effective_week >= 5 and r:
                if contact_html is None:
                    w05_main_status = "no_file"
                else:
                    w05_main_link = f"{root}/blob/{branch}/{checks['week05']['main_assignment_file']}"
                    w05_main_status = "pass" if contact_html.strip() else "empty"

            # AI scores for each week's main assignment: week1/2 (index.html,
            # progressively +its css), week3/4 (wwr/about.html -- same file,
            # updated across both weeks), week5 (wwr/contact.html)
            w1_ai = ai_signals.combined_score(index_html, None)
            w2_ai = ai_signals.combined_score(index_html, css_text_all or None)
            w05_ai = ai_signals.combined_score(contact_html, wwr_css_text)

            # guardrail raw content, collected for cross-student consensus below
            for gfile in cfg["guardrail_files"]:
                guardrail_raw[gfile][sid] = (r or {}).get("files", {}).get(gfile)

            fresh_entries[sid] = {
                "sid": sid, "name": s["name"], "username": username, "repo": repo_used,
                "root": root, "weeks": week_links,
                "week01": {"status": w01_status, "file": w01_file, "fileLink": w01_link, "selfReportLink": w01_sr_link},
                "week02": {"status": w02_status, "fileLink": w02_link, "selfReportLink": w02_sr_link},
                "week03": {
                    "codealong": {
                        "status": w03_ca_status, "detail": w03_ca_detail,
                        "fileLink": index_link, "selfReportLink": w03_ca_sr_link,
                    },
                },
                "week04": {
                    "codealong": {
                        "status": w04_ca_status, "detail": w04_ca_detail,
                        "fileLink": index_link, "selfReportLink": w04_ca_sr_link,
                    },
                    "mainAssignment": {
                        "status": w04_main_status,
                        "fileLink": f"{root}/blob/{branch}/{checks['week03']['file']}" if root else None,
                    },
                },
                "week05": {
                    "codealong": {
                        "status": w05_ca_status, "missing": w05_ca_missing,
                        "fileLink": w05_ca_link, "selfReportLink": w05_ca_sr_link,
                    },
                    "mainAssignment": {"status": w05_main_status, "fileLink": w05_main_link},
                },
                "ai": {"week1": w1_ai, "week2": w2_ai, "week3": w03_ai, "week4": w03_ai, "week5": w05_ai},
                "note": s["flag"],
            }

        # ---- guardrail consensus: flag anyone whose file differs from the
        # most common (presumed untouched-template) version. Recomputed fresh
        # on a full refresh; otherwise the cached canonical version (from a
        # prior full run) is reused so a partial update stays consistent
        # with everyone else's already-computed status. ----
        if full_refresh:
            from collections import Counter
            canonical = {}
            for gfile in cfg["guardrail_files"]:
                present = [c for c in guardrail_raw[gfile].values() if c is not None]
                canonical[gfile] = Counter(present).most_common(1)[0][0] if present else None

        for sid, d in fresh_entries.items():
            if not d["username"]:
                d["guardrail"] = {"status": "not_checked", "note": None}
                continue
            contents = {gfile: guardrail_raw[gfile][sid] for gfile in cfg["guardrail_files"]}
            all_missing = all(v is None for v in contents.values())
            all_present = all(v is not None for v in contents.values())
            if all_missing:
                if d["week02"]["status"] in ("no_template",):
                    d["guardrail"] = {"status": "no_template", "note": None}
                else:
                    d["guardrail"] = {
                        "status": "removed_targeted",
                        "note": "Guardrail files are missing, but the rest of the repo is present. "
                                "A targeted removal, not a case of never using the template.",
                    }
            elif all_present:
                if all(contents[gfile] == canonical.get(gfile) for gfile in cfg["guardrail_files"]):
                    d["guardrail"] = {"status": "unmodified", "note": None}
                else:
                    d["guardrail"] = {
                        "status": "modified",
                        "note": "Content differs from the template original -- review manually.",
                    }
            else:
                d["guardrail"] = {
                    "status": "partial",
                    "note": "Only some guardrail files are present -- worth a manual look.",
                }
    else:
        quiz_id = cache["quiz_id"]
        home_assignment_id = cache["home_assignment_id"]

    # ---- merge fresh + cached results, active roster only ----
    # Iterates the roster list (not the active_sids set) so student order in
    # the report stays stable across runs -- set iteration order isn't.
    # dataset and merged_students are built from the same entry objects so a
    # roster name change (rare, but possible) lands in the saved cache too,
    # not just this run's report.
    dataset = []
    merged_students = {}
    for u in roster:
        sid = u["id"]
        if sid in fresh_entries:
            entry = fresh_entries[sid]
        elif sid in cached_students:
            entry = dict(cached_students[sid])
            entry["name"] = u["name"]
        else:
            continue
        dataset.append(entry)
        merged_students[sid] = entry

    save_cache(cache_path, {
        "current_week": effective_week,
        "quiz_id": quiz_id,
        "home_assignment_id": home_assignment_id,
        "guardrail_canonical": canonical,
        "students": merged_students,
    })
    print(f"Cache updated ({cache_path}): {len(fresh_entries)} freshly checked, "
          f"{len(merged_students) - len(fresh_entries)} reused from cache.")

    write_report(cfg, dataset, quiz_id, home_assignment_id, effective_week)


def write_report(cfg, dataset, quiz_id, home_assignment_id, current_week):
    canvas = cfg["canvas"]
    template_path = os.path.join(SKILL_DIR, "assets", "report_template.html")
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    replacements = {
        "__DATA_JSON__": json.dumps(dataset, separators=(",", ":")),
        "__SECTION_LABEL__": canvas.get("section_label", "WDD130"),
        "__QUIZ_NAME__": canvas["quiz_name"],
        "__QUIZ_ID__": str(quiz_id),
        "__QUIZ_QUESTION_HINT__": canvas["quiz_question_hint"],
        "__HOME_PAGE_ASSIGNMENT_NAME__": canvas["home_page_assignment_name"],
        "__HOME_PAGE_ASSIGNMENT_ID__": str(home_assignment_id),
        "__SELFREPORT_MIN_SCORE__": str(canvas.get("codealong_selfreport_min_score", 5)),
        "__COURSE_ID__": str(canvas["course_id"]),
        "__CURRENT_WEEK__": str(current_week),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)

    out_path = os.path.join(os.getcwd(), "wdd130_progress_report.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(template)
    print(f"\nReport written to {out_path}")
    print("Publish it with the Artifact tool to view it.")


if __name__ == "__main__":
    main()
