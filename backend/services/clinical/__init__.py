"""Clinical module — Phase 1 scaffold.

The chiropractic clinical record is owned by the **patient profile**.
Appointments are operational launch points for encounter documentation, but
every clinical artifact — episode/case, note, diagnosis, treatment plan,
outcome, clinical media — is persisted under the patient and reachable from
Patient Profile > Clinical regardless of whether it was created inline from
an appointment or directly from the patient chart.

Phase 1 deliberately ships only the **episode/case** CRUD plus schema models
for downstream artifacts so future phases can attach without rework.
"""
