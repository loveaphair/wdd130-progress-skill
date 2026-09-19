"""
GitHub GraphQL helpers for batched repo/file checks.

Generate a token at github.com/settings/tokens (fine-grained, no special
scopes needed -- these are public repos, the token is only here to raise
the rate limit from 60/hour to 5,000/hour). Set as GITHUB_TOKEN.

Why GraphQL instead of REST: a single query can check many students' repos
and many files per repo at once by aliasing each lookup (s0, s1, s2... and
f0, f1, f2... within each). This is the same technique used in
byupathway/wdd130/AppScript_GitHubTracker.ts, extended to batch across
students, not just across repo-name variations for one student. It also
sidesteps branch-name guessing entirely: `HEAD:path` resolves against
whatever the repo's actual default branch is.
"""
import json
import os
import re
import urllib.request
import urllib.error

GRAPHQL_URL = "https://api.github.com/graphql"
VALID_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
# File/folder paths embedded in the query as a GraphQL string literal
# (`HEAD:{path}`). Some of these come from parsing a student's own HTML
# (discovered <link href> values), so they're untrusted -- this allowlist
# keeps them from carrying a quote or escape sequence (e.g. \" or ")
# that could break out of the string and inject query syntax.
VALID_PATH = re.compile(r"^[A-Za-z0-9_.\-/]+$")


class GitHubError(RuntimeError):
    pass


def _filter_valid_paths(paths, kind="path"):
    valid, dropped = [], []
    for p in paths:
        (valid if VALID_PATH.match(p) else dropped).append(p)
    if dropped:
        print(f"  Warning: ignoring {len(dropped)} unsafe {kind}(s): {dropped!r}")
    return valid


def _token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GitHubError(
            "GITHUB_TOKEN is not set. Generate one at github.com/settings/tokens "
            "(no special scopes needed for public repos) and export it as an "
            "environment variable before running this skill."
        )
    return token


def _post(query):
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Authorization": f"bearer {_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise GitHubError(f"GitHub GraphQL error {e.code}: {detail}") from e


def _build_query(batch, repo_name, file_paths, folder_paths):
    parts = []
    for i, s in enumerate(batch):
        uname = s["username"]
        if not VALID_USERNAME.match(uname):
            continue  # skip anything that isn't a plausible GitHub username
        file_parts = "\n".join(
            f'f{j}: object(expression: "HEAD:{path}") {{ ... on Blob {{ text byteSize }} }}'
            for j, path in enumerate(file_paths)
        )
        folder_parts = "\n".join(
            f'd{j}: object(expression: "HEAD:{path}") {{ ... on Tree {{ entries {{ name }} }} }}'
            for j, path in enumerate(folder_paths)
        )
        parts.append(f'''
        s{i}: repository(owner: "{uname}", name: "{repo_name}") {{
          name
          url
          defaultBranchRef {{ name }}
          {file_parts}
          {folder_parts}
        }}''')
    return "query {\n" + "\n".join(parts) + "\n}"


def check_students_files(students, repo_name, file_paths, folder_paths=None, batch_size=15):
    """
    students: [{"sid": ..., "username": ...}, ...]
    file_paths: ["index.html", "week01/favorite-city.html", ...] -- blob
        (file) checks, identical for every student in one request.
    folder_paths: ["week01", "week02", ...] -- tree (folder) existence
        checks, same batching.

    Returns {sid: None | {"exists": True, "url": ..., "branch": ...,
             "files": {path: text_or_None}, "folders": {path: bool}}}
    """
    file_paths = _filter_valid_paths(file_paths, "file path")
    folder_paths = _filter_valid_paths(folder_paths or [], "folder path")
    results = {}
    usable = [s for s in students if s.get("username")]
    for start in range(0, len(usable), batch_size):
        batch = usable[start:start + batch_size]
        alias_map = {f"s{i}": s for i, s in enumerate(batch) if VALID_USERNAME.match(s["username"])}
        if not alias_map:
            continue
        query = _build_query(batch, repo_name, file_paths, folder_paths)
        payload = _post(query)
        data = payload.get("data") or {}
        for alias, s in alias_map.items():
            repo = data.get(alias)
            if not repo:
                results[s["sid"]] = None
                continue
            files = {path: (repo.get(f"f{j}") or {}).get("text") for j, path in enumerate(file_paths)}
            folders = {path: repo.get(f"d{j}") is not None for j, path in enumerate(folder_paths)}
            results[s["sid"]] = {
                "exists": True,
                "url": repo["url"],
                "branch": (repo.get("defaultBranchRef") or {}).get("name", "main"),
                "files": files,
                "folders": folders,
            }
    return results


def check_custom_paths(items, batch_size=15):
    """
    For a second pass where each student needs a DIFFERENT set of paths
    checked (e.g. a CSS file discovered by parsing their index.html, whose
    path varies per student) -- unlike check_students_files, which checks
    the same fixed path list for everyone in a batch.

    items: [{"sid": ..., "username": ..., "repo": ..., "paths": [...]}, ...]
    Returns {sid: {path: text_or_None}}
    """
    results = {}
    # These paths are discovered by scraping a student's own HTML (e.g. a
    # <link href> value pulled out of their index.html), so they're
    # untrusted -- filter each item's list before it's used anywhere,
    # not just at the point it's interpolated into the query string.
    for it in items:
        it["paths"] = _filter_valid_paths(it.get("paths") or [], f"CSS path for {it.get('username')}")
    usable = [it for it in items if it.get("username") and it.get("paths")]
    for start in range(0, len(usable), batch_size):
        batch = usable[start:start + batch_size]
        parts = []
        alias_map = {}
        for i, it in enumerate(batch):
            uname = it["username"]
            if not VALID_USERNAME.match(uname):
                continue
            alias_map[f"s{i}"] = it
            file_parts = "\n".join(
                f'f{j}: object(expression: "HEAD:{path}") {{ ... on Blob {{ text }} }}'
                for j, path in enumerate(it["paths"])
            )
            parts.append(f'''
            s{i}: repository(owner: "{uname}", name: "{it["repo"]}") {{
              {file_parts}
            }}''')
        if not alias_map:
            continue
        query = "query {\n" + "\n".join(parts) + "\n}"
        payload = _post(query)
        data = payload.get("data") or {}
        for alias, it in alias_map.items():
            repo = data.get(alias) or {}
            files = {}
            for j, path in enumerate(it["paths"]):
                blob = repo.get(f"f{j}")
                files[path] = blob["text"] if blob else None
            results[it["sid"]] = files
    return results


def check_repo_variations(students_missing, repo_variations, file_paths, folder_paths=None, batch_size=15):
    """
    For students whose primary repo name didn't resolve, try each variation
    in turn (e.g. 'wdd-130', 'WDD130'). Returns the same shape as
    check_students_files, plus which variation matched under 'repo_name_used'.
    """
    remaining = list(students_missing)
    found = {}
    for variation in repo_variations:
        if not remaining:
            break
        batch_result = check_students_files(remaining, variation, file_paths, folder_paths, batch_size)
        still_missing = []
        for s in remaining:
            r = batch_result.get(s["sid"])
            if r:
                r["repo_name_used"] = variation
                found[s["sid"]] = r
            else:
                still_missing.append(s)
        remaining = still_missing
    return found, remaining
