"""
Heuristic AI-usage signal scanner for student HTML/CSS.

This is a lead worth a look, not a verdict -- false positives happen, and a
genuinely strong student can write clean, well-commented code. Built and
tuned during a live review of one WDD130 section; two things were
deliberately left OUT after testing against real submissions:

- Semantic tags (header/nav/main/section/aside/footer): the course requires
  and teaches these from day one, so their presence is baseline, not a
  signal. Scoring them flagged nearly everyone.
- Indentation-vs-nesting-depth consistency: VS Code auto-indents as you
  type, so this fired for ~90% of students regardless of skill -- it was
  measuring the editor, not the student.
- CSS custom properties (:root { --var }) and CSS resets: both are taught
  and expected in this course, so their presence is baseline too. Custom
  properties used to score as a signal and was removed for the same reason
  as semantic tags; resets were never scored and should stay that way if
  someone's tempted to add a check for them later.

Comment weights are intentionally light, not zero: short organizational
comments like `/* Class Selectors */` are also taught and expected, so a
comment or two shouldn't push a score into "notable" on its own. What still
matters is *pervasive, uniformly descriptive* commenting (see
"sectioned ... comments" below), which is a different pattern than a
student leaving themselves a few section markers.

If you adapt this for a different course, revisit these exclusions: they're
specific to what WDD130 teaches and what tooling its students use, not a
universal rule.
"""
import re

SECTION_WORDS = [
    "header", "nav", "footer", "section", "hero", "about", "gallery",
    "contact", "main", "banner", "menu", "sidebar", "card", "form",
]


def scan_html(html):
    if not html:
        return None
    signals = []
    score = 0

    comments = re.findall(r"<!--(.*?)-->", html, re.S)
    descriptive = [c for c in comments if any(w in c.lower() for w in SECTION_WORDS)]
    if len(descriptive) >= 2:
        signals.append("sectioned HTML comments"); score += 15
    elif len(comments) >= 1:
        signals.append("HTML comments present"); score += 6

    if re.search(r"\baria-[a-z]+\s*=", html, re.I) or re.search(r"\brole\s*=", html, re.I):
        signals.append("ARIA/role attributes"); score += 30

    if re.search(r'\bstyle\s*=\s*"', html):
        signals.append("inline style= attributes"); score += 10

    if re.search(r"<style[\s>]", html, re.I):
        signals.append("embedded <style> block"); score += 20

    if re.search(r"<script[\s>]", html, re.I):
        signals.append("JavaScript present"); score += 50

    if re.search(r"og:[a-z]+|twitter:card", html, re.I):
        signals.append("Open Graph/Twitter meta tags"); score += 8

    if "fonts.googleapis" in html and html.count("family=") >= 1 and ";" in html[html.find("family="):html.find("family=") + 60]:
        signals.append("Google Fonts with multiple weights"); score += 5

    return {"score": min(score, 100), "signals": signals}


def scan_css(css):
    if not css:
        return None
    signals = []
    score = 0

    comments = re.findall(r"/\*(.*?)\*/", css, re.S)
    descriptive = [c for c in comments if any(w in c.lower() for w in SECTION_WORDS)]
    if len(descriptive) >= 2:
        signals.append("sectioned CSS comments"); score += 6
    elif len(comments) >= 1:
        signals.append("CSS comments present"); score += 2

    advanced = ["grid-template-areas", "clamp(", "minmax(", "aspect-ratio", "backdrop-filter"]
    hits = [a for a in advanced if a in css]
    if hits:
        signals.append("advanced layout CSS (" + ", ".join(h.rstrip("(") for h in hits) + ")"); score += 15

    if "@media" in css:
        signals.append("responsive @media queries"); score += 10

    if "@keyframes" in css or "transition" in css or "cubic-bezier" in css:
        signals.append("animations/transitions"); score += 10

    if "linear-gradient" in css:
        signals.append("layered shadows/gradients"); score += 8

    rule_count = css.count("{")
    if rule_count >= 100:
        signals.append(f"very high rule count ({rule_count} rules)"); score += 30
    elif rule_count >= 60:
        signals.append(f"high rule count ({rule_count} rules)"); score += 10
    elif rule_count >= 30:
        signals.append(f"moderate rule count ({rule_count} rules)"); score += 4

    return {"score": min(score, 100), "signals": signals}


def combined_score(html, css=None):
    """Merges an HTML scan with an optional CSS scan into one {score, level, signals}."""
    h = scan_html(html)
    if h is None:
        return {"checked": False, "score": None, "level": None, "signals": []}
    score = h["score"]
    signals = list(h["signals"])
    if css:
        c = scan_css(css)
        if c:
            score += c["score"]
            signals += c["signals"]
    score = min(score, 100)
    return {"checked": True, "score": score, "level": level(score), "signals": signals}


def level(score):
    if score >= 70:
        return "high"
    if score >= 40:
        return "notable"
    if score >= 15:
        return "some"
    return "low"
