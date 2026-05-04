"""Pull the raw context a clinician needs from MongoDB, shape it into a
compact dict the LLM can digest, and compute a stable content hash so
the smart cache can detect new information.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from core.tenancy import tenant_db


# How far back to look when building context. Picked to balance token
# cost against clinical utility — ~3 months of a twice-weekly cadence.
MAX_ENCOUNTERS = 5
MAX_OUTCOMES_PER_MEASURE = 6
MAX_QUESTIONNAIRES = 5


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _stable_hash(data: dict | list) -> str:
    canonical = json.dumps(data, sort_keys=True, default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


async def load_patient_context(
    *, tenant_id: str, patient_id: str,
    exclude_note_id: str | None = None,
) -> tuple[dict, str]:
    """Return (context_dict, content_hash). The hash is deterministic
    across the stable set of inputs — if it changes, the cached AI
    output is stale and should be regenerated.
    """
    db = tenant_db(tenant_id)

    # Patient demographics + active problems.
    patient = await db.patients.find_one(
        {"tenant_id": tenant_id, "id": patient_id},
        {"_id": 0, "first_name": 1, "last_name": 1, "date_of_birth": 1,
         "gender": 1, "chief_complaint": 1, "allergies": 1, "medications": 1},
    ) or {}

    # Last N signed clinical notes (SOAP). Draft notes are omitted on
    # purpose — they'd leak unreviewed content into a cached brief.
    notes_q = {
        "tenant_id": tenant_id, "patient_id": patient_id,
        "status": {"$in": ["signed", "locked"]},
    }
    if exclude_note_id:
        notes_q["id"] = {"$ne": exclude_note_id}
    notes_cur = (
        db.clinical_follow_up_notes.find(notes_q, {"_id": 0})
        .sort("date_of_service", -1)
        .limit(MAX_ENCOUNTERS)
    )
    notes: list[dict] = [n async for n in notes_cur]

    # Compact the notes — keep only the text sections + date + visit_number
    def _section(n: dict, key: str) -> str:
        section = n.get(key) or {}
        if not isinstance(section, dict):
            return str(section)[:800]
        chunks: list[str] = []
        for k, v in section.items():
            if v in (None, "", [], {}):
                continue
            chunks.append(f"{k}: {v}")
        return " | ".join(chunks)[:800]

    compact_notes = [
        {
            "id": n.get("id"),
            "dos": n.get("date_of_service"),
            "visit": n.get("visit_number"),
            "s": _section(n, "subjective"),
            "o": _section(n, "objective"),
            "a": _section(n, "assessment"),
            "p": _section(n, "plan"),
            "reassessment": (n.get("reassessment_summary") or "")[:600],
        }
        for n in notes
    ]

    # Outcome-measure trends (last 6 per measure).
    outcomes_cur = db.outcome_entries.find(
        {"tenant_id": tenant_id, "patient_id": patient_id},
        {"_id": 0, "measure_type": 1, "score": 1, "collected_at": 1,
         "label": 1, "max_score": 1, "interpretation": 1},
    ).sort("collected_at", -1).limit(MAX_OUTCOMES_PER_MEASURE * 4)
    outcomes = [o async for o in outcomes_cur]

    # Recent patient-submitted questionnaires.
    q_cur = db.questionnaire_assignments.find(
        {"tenant_id": tenant_id, "patient_id": patient_id,
         "status": "completed"},
        {"_id": 0, "template_id": 1, "score": 1, "interpretation": 1,
         "completed_at": 1},
    ).sort("completed_at", -1).limit(MAX_QUESTIONNAIRES)
    questionnaires = [q async for q in q_cur]

    # Active diagnoses.
    dx_cur = db.clinical_diagnoses.find(
        {"tenant_id": tenant_id, "patient_id": patient_id,
         "status": {"$ne": "resolved"}},
        {"_id": 0, "code": 1, "description": 1, "onset_date": 1},
    ).limit(20)
    diagnoses = [d async for d in dx_cur]

    age = None
    if patient.get("date_of_birth"):
        try:
            dob = datetime.fromisoformat(str(patient["date_of_birth"])).date()
            today = datetime.now(timezone.utc).date()
            age = today.year - dob.year - (
                (today.month, today.day) < (dob.month, dob.day)
            )
        except ValueError:
            pass

    context = {
        "patient": {
            "name": " ".join(
                filter(None, [patient.get("first_name"),
                              patient.get("last_name")])
            ) or "patient",
            "age": age,
            "gender": patient.get("gender"),
            "chief_complaint": patient.get("chief_complaint"),
            "allergies": patient.get("allergies"),
            "medications": patient.get("medications"),
        },
        "diagnoses": diagnoses,
        "notes": compact_notes,
        "outcomes": outcomes,
        "questionnaires": questionnaires,
    }

    # Cache key is the hash of the *inputs only* that the LLM reads.
    # Patient demographics rarely change, but when they do we want a
    # new brief, so we include them.
    hash_input = {
        "patient": context["patient"],
        "diagnoses": [d.get("code") for d in diagnoses],
        "note_ids": [n["id"] for n in notes],
        "outcome_ids": [o.get("label", "") + str(o.get("collected_at", ""))
                        for o in outcomes],
        "questionnaire_ids": [q.get("template_id", "") + str(q.get("completed_at", ""))
                              for q in questionnaires],
    }
    return context, _stable_hash(hash_input)


def format_context_for_prompt(context: dict) -> str:
    """Turn the context dict into a compact Markdown block suitable
    for feeding to the LLM. We keep it terse: fewer tokens = cheaper,
    faster, and more reliable."""
    p = context["patient"]
    lines = [f"# Patient\n{p['name']}"]
    if p.get("age") is not None:
        lines.append(f"- Age: {p['age']}")
    if p.get("gender"):
        lines.append(f"- Gender: {p['gender']}")
    if p.get("chief_complaint"):
        lines.append(f"- Chief complaint: {p['chief_complaint']}")
    if p.get("allergies"):
        lines.append(f"- Allergies: {p['allergies']}")
    if p.get("medications"):
        lines.append(f"- Medications: {p['medications']}")

    dx = context.get("diagnoses") or []
    if dx:
        lines.append("\n# Active diagnoses")
        for d in dx:
            lines.append(f"- {d.get('code', '?')}: {d.get('description', '')}"
                         + (f" (onset {d.get('onset_date')})"
                            if d.get("onset_date") else ""))

    notes = context.get("notes") or []
    if notes:
        lines.append(f"\n# Prior encounters ({len(notes)} most recent, newest first)")
        for n in notes:
            lines.append(
                f"\n## {n.get('dos', '')[:10]}  visit #{n.get('visit') or '?'}"
            )
            if n.get("s"):
                lines.append(f"**S**: {n['s']}")
            if n.get("o"):
                lines.append(f"**O**: {n['o']}")
            if n.get("a"):
                lines.append(f"**A**: {n['a']}")
            if n.get("p"):
                lines.append(f"**P**: {n['p']}")
            if n.get("reassessment"):
                lines.append(f"*Reassessment*: {n['reassessment']}")

    outcomes = context.get("outcomes") or []
    if outcomes:
        lines.append("\n# Outcome measures (newest first)")
        for o in outcomes[:20]:
            lines.append(
                f"- {o.get('collected_at', '')[:10]} · {o.get('label', '?')} = "
                f"{o.get('score')} / {o.get('max_score', '?')}"
                + (f" ({o.get('interpretation')})"
                   if o.get("interpretation") else "")
            )

    qs = context.get("questionnaires") or []
    if qs:
        lines.append("\n# Patient-submitted questionnaires")
        for q in qs:
            lines.append(
                f"- {q.get('completed_at', '')[:10]} · {q.get('template_id', '?').upper()}"
                f" = {q.get('score')} ({q.get('interpretation', '')})"
            )

    return "\n".join(lines)
