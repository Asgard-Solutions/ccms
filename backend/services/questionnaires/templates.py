"""Static questionnaire templates + pure scoring functions.

All templates are keyed by ``id`` (snake_case, stable — do NOT rename
without a migration because ``questionnaire_assignments.template_id``
references it).
"""
from __future__ import annotations

from typing import Callable

# ---------------------------------------------------------------------------
# NPRS — 0..10 pain scale, single question
# ---------------------------------------------------------------------------
NPRS = {
    "id": "nprs",
    "title": "Numeric Pain Rating Scale",
    "description": (
        "On a scale from 0 (no pain) to 10 (worst imaginable pain), "
        "rate your current pain level."
    ),
    "measure_type": "pain_scale",
    "min_score": 0,
    "max_score": 10,
    "items": [
        {
            "id": "now",
            "prompt": "Pain right now",
            "type": "scale",
            "min": 0,
            "max": 10,
            "step": 1,
        },
    ],
}


def _score_nprs(answers: dict) -> dict:
    value = int(answers.get("now") or 0)
    value = max(0, min(10, value))
    if value == 0:
        note = "No pain"
    elif value <= 3:
        note = "Mild pain"
    elif value <= 6:
        note = "Moderate pain"
    else:
        note = "Severe pain"
    return {"score": float(value), "interpretation": note}


# ---------------------------------------------------------------------------
# Oswestry Disability Index — 10 items × 0..5, %
# ---------------------------------------------------------------------------
def _odi_items() -> list[dict]:
    sections = [
        ("pain_intensity", "Pain intensity",
         ["I have no pain at the moment",
          "The pain is very mild at the moment",
          "The pain is moderate at the moment",
          "The pain is fairly severe at the moment",
          "The pain is very severe at the moment",
          "The pain is the worst imaginable at the moment"]),
        ("personal_care", "Personal care (washing, dressing)",
         ["I can look after myself normally without causing extra pain",
          "I can look after myself normally but it causes extra pain",
          "It is painful to look after myself and I am slow and careful",
          "I need some help but manage most of my personal care",
          "I need help every day in most aspects of self care",
          "I do not get dressed, I wash with difficulty and stay in bed"]),
        ("lifting", "Lifting",
         ["I can lift heavy weights without extra pain",
          "I can lift heavy weights but it gives extra pain",
          "Pain prevents me lifting heavy weights off the floor",
          "Pain prevents me lifting heavy weights off the floor but I can manage if they are conveniently placed",
          "I can lift only very light weights",
          "I cannot lift or carry anything"]),
        ("walking", "Walking",
         ["Pain does not prevent me walking any distance",
          "Pain prevents me walking more than 1 mile",
          "Pain prevents me walking more than 1/2 mile",
          "Pain prevents me walking more than 100 yards",
          "I can only walk using a stick or crutches",
          "I am in bed most of the time"]),
        ("sitting", "Sitting",
         ["I can sit in any chair as long as I like",
          "I can sit in my favorite chair as long as I like",
          "Pain prevents me from sitting for more than 1 hour",
          "Pain prevents me from sitting for more than 30 minutes",
          "Pain prevents me from sitting for more than 10 minutes",
          "Pain prevents me from sitting at all"]),
        ("standing", "Standing",
         ["I can stand as long as I want without extra pain",
          "I can stand as long as I want but it gives me extra pain",
          "Pain prevents me from standing for more than 1 hour",
          "Pain prevents me from standing for more than 30 minutes",
          "Pain prevents me from standing for more than 10 minutes",
          "Pain prevents me from standing at all"]),
        ("sleeping", "Sleeping",
         ["My sleep is never disturbed by pain",
          "My sleep is occasionally disturbed by pain",
          "Because of pain I have less than 6 hours sleep",
          "Because of pain I have less than 4 hours sleep",
          "Because of pain I have less than 2 hours sleep",
          "Pain prevents me from sleeping at all"]),
        ("sex_life", "Sex life",
         ["My sex life is normal and causes no extra pain",
          "My sex life is normal but causes some extra pain",
          "My sex life is nearly normal but is very painful",
          "My sex life is severely restricted by pain",
          "My sex life is nearly absent because of pain",
          "Pain prevents any sex life at all"]),
        ("social_life", "Social life",
         ["My social life is normal and causes me no extra pain",
          "My social life is normal but increases the degree of pain",
          "Pain has no significant effect on my social life apart from limiting my more energetic interests",
          "Pain has restricted my social life and I do not go out as often",
          "Pain has restricted my social life to my home",
          "I have no social life because of pain"]),
        ("travelling", "Travelling",
         ["I can travel anywhere without pain",
          "I can travel anywhere but it gives me extra pain",
          "Pain is bad but I manage journeys over two hours",
          "Pain restricts me to journeys of less than one hour",
          "Pain restricts me to short necessary journeys under 30 minutes",
          "Pain prevents me from travelling except to receive treatment"]),
    ]
    items = []
    for key, label, choices in sections:
        items.append({
            "id": key,
            "prompt": label,
            "type": "choice",
            "choices": [
                {"value": idx, "label": text}
                for idx, text in enumerate(choices)
            ],
        })
    return items


ODI = {
    "id": "odi",
    "title": "Oswestry Disability Index",
    "description": (
        "For low-back–related disability. Pick the one statement in each "
        "section that most closely applies to you today."
    ),
    "measure_type": "oswestry",
    "min_score": 0,
    "max_score": 100,
    "items": _odi_items(),
}


def _score_odi(answers: dict) -> dict:
    keys = [item["id"] for item in ODI["items"]]
    answered = [int(answers[k]) for k in keys if k in answers and answers[k] is not None]
    if not answered:
        return {"score": 0.0, "interpretation": "No answers provided"}
    max_possible = 5 * len(answered)
    raw = sum(answered)
    pct = round((raw / max_possible) * 100, 1) if max_possible else 0.0
    if pct <= 20:
        note = "Minimal disability"
    elif pct <= 40:
        note = "Moderate disability"
    elif pct <= 60:
        note = "Severe disability"
    elif pct <= 80:
        note = "Crippled"
    else:
        note = "Bed-bound / exaggerating"
    return {"score": pct, "interpretation": note}


# ---------------------------------------------------------------------------
# Neck Disability Index — 10 items × 0..5, %
# ---------------------------------------------------------------------------
def _ndi_items() -> list[dict]:
    sections = [
        ("pain_intensity", "Pain intensity",
         ["I have no pain at the moment",
          "The pain is very mild at the moment",
          "The pain is moderate at the moment",
          "The pain is fairly severe at the moment",
          "The pain is very severe at the moment",
          "The pain is the worst imaginable at the moment"]),
        ("personal_care", "Personal care",
         ["I can look after myself normally without extra pain",
          "I can look after myself but it causes extra pain",
          "It is painful to look after myself and I am slow and careful",
          "I need some help but manage most of my personal care",
          "I need help every day in most aspects of self care",
          "I do not get dressed, I wash with difficulty and stay in bed"]),
        ("lifting", "Lifting",
         ["I can lift heavy weights without extra pain",
          "I can lift heavy weights but it gives extra pain",
          "Pain prevents me lifting heavy weights off the floor",
          "Pain prevents me lifting heavy weights but I can manage if they are conveniently placed",
          "I can lift only very light weights",
          "I cannot lift or carry anything"]),
        ("reading", "Reading",
         ["I can read as much as I want with no pain in my neck",
          "I can read as much as I want with slight pain in my neck",
          "I can read as much as I want with moderate pain in my neck",
          "I can't read as much as I want because of moderate pain",
          "I can hardly read at all because of severe pain",
          "I cannot read at all"]),
        ("headaches", "Headaches",
         ["I have no headaches at all",
          "I have slight headaches that come infrequently",
          "I have moderate headaches that come infrequently",
          "I have moderate headaches that come frequently",
          "I have severe headaches that come frequently",
          "I have headaches almost all the time"]),
        ("concentration", "Concentration",
         ["I can concentrate fully when I want with no difficulty",
          "I can concentrate fully with slight difficulty",
          "I have a fair degree of difficulty concentrating when I want",
          "I have a lot of difficulty concentrating when I want",
          "I have a great deal of difficulty concentrating when I want",
          "I cannot concentrate at all"]),
        ("work", "Work",
         ["I can do as much work as I want",
          "I can only do my usual work but no more",
          "I can do most of my usual work but no more",
          "I cannot do my usual work",
          "I can hardly do any work at all",
          "I can't do any work at all"]),
        ("driving", "Driving",
         ["I can drive my car without any neck pain",
          "I can drive my car as long as I want with slight neck pain",
          "I can drive my car as long as I want with moderate neck pain",
          "I can't drive my car as long as I want because of moderate pain",
          "I can hardly drive at all because of severe neck pain",
          "I cannot drive my car at all"]),
        ("sleeping", "Sleeping",
         ["I have no trouble sleeping",
          "My sleep is slightly disturbed (less than 1 hour sleeplessness)",
          "My sleep is mildly disturbed (1–2 hours sleeplessness)",
          "My sleep is moderately disturbed (2–3 hours sleeplessness)",
          "My sleep is greatly disturbed (3–5 hours sleeplessness)",
          "My sleep is completely disturbed (5–7 hours sleeplessness)"]),
        ("recreation", "Recreation",
         ["I am able to engage in all recreation activities with no pain",
          "I am able to engage in all recreation activities with some pain",
          "I am able to engage in most but not all recreation activities because of pain",
          "I am able to engage in few of my usual recreation activities because of pain",
          "I can hardly do any recreation activities because of pain",
          "I can't do any recreation activities at all"]),
    ]
    items = []
    for key, label, choices in sections:
        items.append({
            "id": key,
            "prompt": label,
            "type": "choice",
            "choices": [{"value": i, "label": t} for i, t in enumerate(choices)],
        })
    return items


NDI = {
    "id": "ndi",
    "title": "Neck Disability Index",
    "description": (
        "For neck-related disability. Pick the one statement in each "
        "section that most closely applies to you today."
    ),
    "measure_type": "ndi",
    "min_score": 0,
    "max_score": 100,
    "items": _ndi_items(),
}


def _score_ndi(answers: dict) -> dict:
    keys = [item["id"] for item in NDI["items"]]
    answered = [int(answers[k]) for k in keys if k in answers and answers[k] is not None]
    if not answered:
        return {"score": 0.0, "interpretation": "No answers provided"}
    max_possible = 5 * len(answered)
    raw = sum(answered)
    pct = round((raw / max_possible) * 100, 1) if max_possible else 0.0
    if pct <= 8:
        note = "No disability"
    elif pct <= 28:
        note = "Mild disability"
    elif pct <= 48:
        note = "Moderate disability"
    elif pct <= 68:
        note = "Severe disability"
    else:
        note = "Complete disability"
    return {"score": pct, "interpretation": note}


# ---------------------------------------------------------------------------
# Patient-Specific Functional Scale — up to 5 activities × 0..10
# ---------------------------------------------------------------------------
PSFS = {
    "id": "psfs",
    "title": "Patient-Specific Functional Scale",
    "description": (
        "List up to 5 activities you're unable to do or are having "
        "difficulty with as a result of your problem. Rate each from 0 "
        "(unable to perform) to 10 (able to perform at pre-injury level)."
    ),
    "measure_type": "functional_index",
    "min_score": 0,
    "max_score": 10,
    "items": [
        {"id": "activity_1", "prompt": "Activity 1 (name + rating)",
         "type": "activity", "min": 0, "max": 10},
        {"id": "activity_2", "prompt": "Activity 2 (optional)",
         "type": "activity", "min": 0, "max": 10, "optional": True},
        {"id": "activity_3", "prompt": "Activity 3 (optional)",
         "type": "activity", "min": 0, "max": 10, "optional": True},
        {"id": "activity_4", "prompt": "Activity 4 (optional)",
         "type": "activity", "min": 0, "max": 10, "optional": True},
        {"id": "activity_5", "prompt": "Activity 5 (optional)",
         "type": "activity", "min": 0, "max": 10, "optional": True},
    ],
}


def _score_psfs(answers: dict) -> dict:
    scores: list[int] = []
    for key in ("activity_1", "activity_2", "activity_3", "activity_4", "activity_5"):
        raw = answers.get(key)
        if isinstance(raw, dict):
            rating = raw.get("rating")
        else:
            rating = raw
        if rating is None:
            continue
        try:
            scores.append(max(0, min(10, int(rating))))
        except (TypeError, ValueError):
            continue
    if not scores:
        return {"score": 0.0, "interpretation": "No activities rated"}
    avg = round(sum(scores) / len(scores), 1)
    if avg >= 8:
        note = "Near-normal function"
    elif avg >= 5:
        note = "Moderate functional limitation"
    elif avg >= 2:
        note = "Severe functional limitation"
    else:
        note = "Unable to function"
    return {"score": avg, "interpretation": note}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SCORERS: dict[str, Callable[[dict], dict]] = {
    "nprs": _score_nprs,
    "odi": _score_odi,
    "ndi": _score_ndi,
    "psfs": _score_psfs,
}

TEMPLATES: dict[str, dict] = {
    NPRS["id"]: NPRS,
    ODI["id"]: ODI,
    NDI["id"]: NDI,
    PSFS["id"]: PSFS,
}


def list_templates() -> list[dict]:
    """Lightweight list view — drops the items for bandwidth."""
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "description": t["description"],
            "measure_type": t["measure_type"],
            "min_score": t["min_score"],
            "max_score": t["max_score"],
            "item_count": len(t["items"]),
        }
        for t in TEMPLATES.values()
    ]


def get_template(template_id: str) -> dict | None:
    return TEMPLATES.get(template_id)


def score_answers(template_id: str, answers: dict) -> dict:
    scorer = SCORERS.get(template_id)
    if not scorer:
        return {"score": 0.0, "interpretation": "Unknown template"}
    return scorer(answers or {})
