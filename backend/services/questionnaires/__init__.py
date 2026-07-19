"""Outcome-measure questionnaires — templates + scoring.

Four standard chiropractic PRO instruments ship as code:

  * **NPRS**  — Numeric Pain Rating Scale (0–10).
  * **ODI**   — Oswestry Disability Index (10 items × 0–5, scored 0–100).
  * **NDI**   — Neck Disability Index (10 items × 0–5, scored 0–100).
  * **PSFS**  — Patient-Specific Functional Scale (up to 5 items × 0–10,
    averaged to a single 0–10 figure).

Each template specifies sections and questions in a structure the portal
renders generically, plus a ``score(answers)`` callable that returns a
dict ``{score, interpretation}``. The submit endpoint persists the
answers + computed score and writes a row into ``outcome_entries`` so
the existing trends/charts pick it up automatically.
"""
