/**
 * ImagingCard — Phase 3 Slice 4 (patient-chart scoped).
 *
 * Filterable, permission-aware imaging list built on top of the
 * existing `clinical_media` records surfaced through the grouped
 * timeline. Imaging *filters* here are transient (session-scoped
 * via `useClinicalReturnState`) and never leak into a durable
 * preset (see Slice 2 sanitizer).
 *
 * Metadata surfaces:
 *   - `modality` (allow-listed: xray / mri / ct / ultrasound / other)
 *   - `region` (allow-listed: cervical / thoracic / lumbar / other)
 *   - `report_status` (pending / final / amended)
 *   - `provider_name` (from media.uploaded_by_name, presentational)
 *
 * The card never *writes* — the "Open" action delegates to the
 * existing imaging viewer / edit surface.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Image as ImageIcon } from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDate } from "../../utils/time";
import { useClinicalReturnState } from "./useClinicalReturnState";

const MODALITIES = ["xray", "mri", "ct", "ultrasound", "other"];
const MODALITY_LABEL = {
  xray: "X-ray",
  mri: "MRI",
  ct: "CT",
  ultrasound: "Ultrasound",
  other: "Other",
};

function toggle(list, v) {
  const set = new Set(list || []);
  if (set.has(v)) set.delete(v);
  else set.add(v);
  return Array.from(set);
}

function inferModality(row) {
  const raw = (row.imaging_modality || row.kind || "").toString().toLowerCase();
  if (raw.includes("x-ray") || raw.includes("xray") || raw === "x-ray") return "xray";
  if (raw.includes("mri")) return "mri";
  if (raw.includes("ct")) return "ct";
  if (raw.includes("ultra")) return "ultrasound";
  if (!raw) return null;
  return "other";
}

export default function ImagingCard({
  patientId,
  canWrite,
  routeInstanceToken,
  onOpenImaging,
}) {
  const { state, saveState } = useClinicalReturnState({
    section: "imaging",
    routeInstanceToken,
  });

  const filters = useMemo(
    () => ({
      modalities: state?.modalities || [],
    }),
    [state],
  );

  const [rows, setRows] = useState(null); // null = loading
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const { data } = await api.get(
        `/patients/${patientId}/clinical/media`,
      );
      setRows(Array.isArray(data) ? data : []);
    } catch (e) {
      const status = e?.response?.status;
      if (status === 403) {
        setRows([]);
        setErr("permission");
      } else {
        setRows([]);
        setErr(formatApiError(e));
      }
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const decorated = useMemo(() => {
    if (!rows) return [];
    return rows.map((r) => ({ ...r, _modality: inferModality(r) }));
  }, [rows]);

  const filtered = useMemo(() => {
    if (!filters.modalities?.length) return decorated;
    const set = new Set(filters.modalities);
    return decorated.filter((r) => set.has(r._modality));
  }, [decorated, filters.modalities]);

  if (rows === null) {
    return (
      <Skeleton data-testid="imaging-card-loading" className="h-24 rounded-lg" />
    );
  }

  if (err === "permission") {
    return (
      <section
        data-testid="imaging-card-permission-denied"
        className="rounded-xl border border-border bg-card/60 p-4"
      >
        <h3 className="font-display text-lg font-semibold">Imaging</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Your role does not include imaging access on this patient.
        </p>
      </section>
    );
  }

  if (err) {
    return (
      <section
        data-testid="imaging-card-error"
        className="rounded-xl border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive"
      >
        <h3 className="font-display text-lg font-semibold">Imaging</h3>
        <p className="mt-1">{err}</p>
      </section>
    );
  }

  return (
    <section
      data-testid="imaging-card"
      aria-labelledby="imaging-title"
      className="rounded-xl border border-border bg-card/60 p-4"
    >
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3
            id="imaging-title"
            className="font-display text-lg font-semibold text-foreground"
          >
            Imaging
          </h3>
          <p className="text-sm text-muted-foreground">
            Patient-chart imaging list. Filters apply to this chart only and are cleared on logout.
          </p>
        </div>
        <div
          role="tablist"
          aria-label="Imaging modality filters"
          className="flex flex-wrap gap-1"
        >
          {MODALITIES.map((m) => {
            const on = filters.modalities.includes(m);
            return (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => saveState({ modalities: toggle(filters.modalities, m) })}
                data-testid={`imaging-filter-${m}`}
                className={[
                  "rounded-full px-2.5 py-1 text-[11px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                  on
                    ? "bg-primary text-primary-foreground font-medium"
                    : "border border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
                ].join(" ")}
              >
                {MODALITY_LABEL[m]}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mt-3">
        {filtered.length === 0 ? (
          <div
            data-testid="imaging-card-empty"
            className="rounded-lg border border-dashed border-border bg-card/40 px-4 py-3 text-sm text-muted-foreground"
          >
            {rows.length === 0
              ? "No imaging records on this chart yet."
              : "No imaging records match these filters."}
          </div>
        ) : (
          <ul
            data-testid="imaging-list"
            aria-label="Imaging records"
            className="space-y-2"
          >
            {filtered.map((r) => (
              <li
                key={r.id}
                data-testid={`imaging-row-${r.id}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-card px-3 py-2"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <ImageIcon
                    className="h-4 w-4 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-foreground">
                      {r.title || r.kind || "Imaging"}
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      {r._modality ? (
                        <span
                          className="rounded-full border border-border bg-card px-1.5 py-0.5 uppercase text-[9px] tracking-wider text-muted-foreground"
                          data-testid={`imaging-row-${r.id}-modality`}
                        >
                          {MODALITY_LABEL[r._modality]}
                        </span>
                      ) : (
                        <span
                          className="rounded-full border border-warning/40 bg-warning-soft px-1.5 py-0.5 text-warning uppercase text-[9px] tracking-wider"
                          data-testid={`imaging-row-${r.id}-missing-modality`}
                        >
                          Missing modality
                        </span>
                      )}
                      <span className="ml-1">
                        {r.captured_at
                          ? formatDate(r.captured_at)
                          : r.created_at
                          ? formatDate(r.created_at)
                          : ""}
                      </span>
                      {r.uploaded_by_name && (
                        <span className="ml-1">· {r.uploaded_by_name}</span>
                      )}
                    </div>
                  </div>
                </div>
                {onOpenImaging && (
                  <button
                    type="button"
                    onClick={() => onOpenImaging(r)}
                    data-testid={`imaging-row-${r.id}-open`}
                    className="rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-foreground hover:bg-muted"
                  >
                    Open
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
