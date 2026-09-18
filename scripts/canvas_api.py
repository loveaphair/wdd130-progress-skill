"""
Canvas LMS API helpers, using a Canvas Personal Access Token instead of
browser scraping.

Generate a token: Canvas -> Account -> Settings -> "New Access Token".
Set it as the CANVAS_API_TOKEN environment variable. Never hardcode it here.

This module replaces the browser-automation workflow used in the original
session: navigating to a quiz's "history" page and scraping rendered text,
and opening SpeedGrader to read a submitted URL out of an iframe. Both are
now single authenticated API calls.
"""
import html
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse


class CanvasError(RuntimeError):
    pass


def _token():
    token = os.environ.get("CANVAS_API_TOKEN")
    if not token:
        raise CanvasError(
            "CANVAS_API_TOKEN is not set. Generate one in Canvas under "
            "Account > Settings > New Access Token, then export it as an "
            "environment variable before running this skill."
        )
    return token


def _request(domain, path, params=None):
    url = domain.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            link_header = resp.headers.get("Link", "")
            return json.loads(body), link_header
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise CanvasError(f"Canvas API error {e.code} for {url}: {detail}") from e


def _paginated(domain, path, params=None):
    """Yields items across all pages, following the Link header's rel=next."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    next_url = None
    first = True
    while first or next_url:
        if first:
            data, link_header = _request(domain, path, params)
            first = False
        else:
            req = urllib.request.Request(
                next_url,
                headers={"Authorization": f"Bearer {_token()}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                link_header = resp.headers.get("Link", "")
        if isinstance(data, dict):
            # Some endpoints (e.g. quiz submissions) wrap the array in an
            # object keyed by resource name instead of returning it bare.
            data = next((v for v in data.values() if isinstance(v, list)), [])
        for item in data:
            yield item
        next_url = None
        for part in link_header.split(","):
            m = re.search(r'<([^>]+)>;\s*rel="next"', part)
            if m:
                next_url = m.group(1)


def get_active_roster(domain, course_id):
    """Returns [{id, name}] for active student enrollments."""
    items = _paginated(
        domain,
        f"/api/v1/courses/{course_id}/users",
        {"enrollment_type[]": "student", "enrollment_state[]": "active"},
    )
    return [{"id": str(u["id"]), "name": u["name"]} for u in items]


def resolve_quiz_id(domain, course_id, quiz_name):
    quizzes, _ = _request(domain, f"/api/v1/courses/{course_id}/quizzes", {"per_page": 100})
    for q in quizzes:
        if q["title"].strip().lower() == quiz_name.strip().lower():
            return q["id"]
    raise CanvasError(
        f'No quiz named "{quiz_name}" found in course {course_id}. '
        f"Available: {[q['title'] for q in quizzes]}"
    )


def resolve_quiz_question_id(domain, course_id, quiz_id, question_hint):
    """Finds the quiz question whose text contains question_hint (case-insensitive)."""
    questions, _ = _request(
        domain, f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/questions", {"per_page": 100}
    )
    cleaned = []
    for q in questions:
        text = re.sub("<[^>]+>", " ", q.get("question_text", "")).strip()
        cleaned.append(text)
        if question_hint.lower() in text.lower():
            return q["id"]
    raise CanvasError(
        f'No question containing "{question_hint}" found in quiz {quiz_id}. '
        f"Available question texts: {cleaned}"
    )


def get_quiz_answers(domain, course_id, quiz_id, question_id):
    """
    Returns {user_id: answer_text_or_None} for every submission of this quiz.

    NOTE: this used to hit /api/v1/quiz_submissions/{id}/questions, which
    looked plausible (it returns one entry per question with an "id" and an
    "answer" field) but is actually a stale/blueprint view of the question
    bank for *in-progress* quiz-taking -- for an already-graded submission
    it returns the same question "id" sequence for every student with
    "answer": None, never the student's actual response. That silently
    broke quiz-answer resolution for 100% of students; every previously
    "resolved" username was actually coming from the Home Page assignment
    fallback, not the quiz, which only surfaces once a student's fallback
    is *also* empty (masking the bug rather than fixing it).

    The reliable path is the quiz's own shadow assignment: fetch its
    submissions with submission_history, and read each student's actual
    answer text out of the last history entry's submission_data (matched
    by question_id, which -- unlike the endpoint above -- really does
    identify the question here).
    """
    quiz, _ = _request(domain, f"/api/v1/courses/{course_id}/quizzes/{quiz_id}")
    assignment_id = quiz.get("assignment_id")
    if not assignment_id:
        raise CanvasError(f"Quiz {quiz_id} has no linked assignment_id -- can't read submissions.")

    items = _paginated(
        domain,
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
        {"include[]": "submission_history"},
    )

    answers = {}
    for s in items:
        uid = s.get("user_id")
        if uid is None:
            continue
        history = s.get("submission_history") or []
        answer_text = None
        if history:
            submission_data = history[-1].get("submission_data") or []
            for item in submission_data:
                if item.get("question_id") == question_id:
                    raw = item.get("text")
                    if isinstance(raw, str):
                        # Rich-text quiz answers can carry HTML entities (e.g. a
                        # trailing "&nbsp;" Canvas's editor injects for a trailing
                        # space) that tag-stripping alone leaves as literal text.
                        text = re.sub("<[^>]+>", " ", raw)
                        text = html.unescape(text)
                        answer_text = re.sub(r"\s+", " ", text).strip() or None
                    elif raw is not None:
                        answer_text = str(raw)
                    break
        answers[str(uid)] = answer_text
    return answers


def resolve_assignment_id(domain, course_id, assignment_name):
    assignments, _ = _request(
        domain, f"/api/v1/courses/{course_id}/assignments", {"per_page": 100}
    )
    for a in assignments:
        if a["name"].strip().lower() == assignment_name.strip().lower():
            return a["id"]
    raise CanvasError(
        f'No assignment named "{assignment_name}" found in course {course_id}.'
    )


def resolve_assignment_ids(domain, course_id, names):
    """Resolves several assignment names to ids in a single API call.
    Returns {name: id}; raises CanvasError naming the first one not found
    (with the full available list, same as resolve_assignment_id)."""
    assignments, _ = _request(
        domain, f"/api/v1/courses/{course_id}/assignments", {"per_page": 100}
    )
    by_name = {a["name"].strip().lower(): a["id"] for a in assignments}
    out = {}
    for name in names:
        aid = by_name.get(name.strip().lower())
        if aid is None:
            raise CanvasError(
                f'No assignment named "{name}" found in course {course_id}. '
                f"Available: {[a['name'] for a in assignments]}"
            )
        out[name] = aid
    return out


def get_assignment_submission_urls(domain, course_id, assignment_id):
    """Returns {user_id: submitted_url_or_None} for an online_url assignment."""
    items = _paginated(
        domain, f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
    )
    out = {}
    for s in items:
        uid = str(s.get("user_id"))
        out[uid] = s.get("url")
    return out


def get_assignment_scores(domain, course_id, assignment_id):
    """Returns {user_id: score_or_None} for every submission of this
    assignment -- used for the code-along self-report gate (a student's
    technical check only counts once they've self-reported completion)."""
    items = _paginated(
        domain, f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
    )
    out = {}
    for s in items:
        uid = str(s.get("user_id"))
        out[uid] = s.get("score")
    return out


def get_assignment_submissions(domain, course_id, assignment_id):
    """Returns {user_id: {"score", "workflow_state", "submitted_at"}} for
    every submission of this assignment -- used for a graded main
    assignment, where completed/zero-grade/not-submitted needs more than
    just the score (a Canvas submission record exists for every enrolled
    student even when nothing was turned in, with workflow_state
    "unsubmitted" and no submitted_at)."""
    items = _paginated(
        domain, f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
    )
    out = {}
    for s in items:
        uid = str(s.get("user_id"))
        out[uid] = {
            "score": s.get("score"),
            "workflow_state": s.get("workflow_state"),
            "submitted_at": s.get("submitted_at"),
        }
    return out
