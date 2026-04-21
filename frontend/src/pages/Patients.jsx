import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronRight, Plus, Search, User2 } from "lucide-react";
import { api, formatApiError } from "../api/client";
import { toast } from "sonner";
import { useAuth } from "../contexts/AuthContext";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Skeleton } from "../components/ui/skeleton";
import { PatientWizardDialog } from "../components/patient-wizard/PatientWizardDialog";

const STAFF_ROLES = ["admin", "doctor", "staff"];

// -----------------------------------------------------------------------
// Page
// -----------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Recently-viewed patients — localStorage-backed utility.
// ---------------------------------------------------------------------------
const RECENT_KEY = "ccms.recentPatients";
const RECENT_LIMIT = 6;

function readRecent(userId) {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || "{}");
    const list = Array.isArray(raw?.[userId]) ? raw[userId] : [];
    return list.slice(0, RECENT_LIMIT);
  } catch {
    return [];
  }
}

function pushRecent(userId, entry) {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || "{}");
    const list = Array.isArray(raw?.[userId]) ? raw[userId] : [];
    const dedup = [entry, ...list.filter((x) => x?.id !== entry.id)].slice(0, RECENT_LIMIT);
    localStorage.setItem(RECENT_KEY, JSON.stringify({ ...raw, [userId]: dedup }));
  } catch {
    /* ignore quota errors */
  }
}

// ---------------------------------------------------------------------------
// Typeahead debounce.
// ---------------------------------------------------------------------------
function useDebouncedValue(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

// ---------------------------------------------------------------------------
// Result row highlighter — wraps matched substrings in <mark>.
// Supports the SQL `%` wildcard (treated as `.*`) and escapes the rest.
// ---------------------------------------------------------------------------
function escapeRegExp(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildHighlightRegex(term) {
  if (!term) return null;
  const t = term.trim();
  if (!t) return null;
  const placeholder = "\u0000WILDCARD\u0000";
  const safe = escapeRegExp(t.replace(/%/g, placeholder)).split(placeholder).join(".*?");
  try {
    return new RegExp(`(${safe})`, "gi");
  } catch {
    return null;
  }
}

function Highlight({ value, rx }) {
  if (!value) return <span>—</span>;
  const s = String(value);
  if (!rx) return <>{s}</>;
  const parts = s.split(rx);
  return (
    <>
      {parts.map((chunk, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-sm bg-primary/25 px-0.5 text-foreground">
            {chunk}
          </mark>
        ) : (
          <span key={i}>{chunk}</span>
        )
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Patients — search-first lookup page.
// ---------------------------------------------------------------------------
export default function Patients() {
  const { user } = useAuth();
  const canCreate = STAFF_ROLES.includes(user.role);
  const [mode, setMode] = useState("global"); // 'global' | 'advanced'
  const [q, setQ] = useState("");
  const [fields, setFields] = useState({ name: "", phone: "", address: "", dob: "" });
  const [submittedAt, setSubmittedAt] = useState(0);
  const [results, setResults] = useState(null); // null = idle, [] = empty
  const [meta, setMeta] = useState({ total: 0, truncated: false });
  const [loading, setLoading] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const [open, setOpen] = useState(false);
  const [recent, setRecent] = useState(() => readRecent(user.id));
  const navigate = useNavigate();

  const debouncedQ = useDebouncedValue(q, 250);
  const debouncedFields = useDebouncedValue(fields, 300);

  const buildParams = useCallback(() => {
    if (mode === "global") return q.trim() ? { q: q.trim(), limit: 10 } : null;
    const active = Object.fromEntries(
      Object.entries(fields).filter(([, v]) => v.trim())
    );
    return Object.keys(active).length ? { ...active, limit: 25 } : null;
  }, [mode, q, fields]);

  const runSearch = useCallback(
    async (params, { typeahead = false } = {}) => {
      if (!params) {
        setResults(null);
        setMeta({ total: 0, truncated: false });
        return;
      }
      setLoading(true);
      try {
        const { data } = await api.get("/patients/search", { params });
        setResults(data.results);
        setMeta({ total: data.total, truncated: data.truncated_candidates });
        setActiveIdx(data.results.length ? 0 : -1);
      } catch (err) {
        if (!typeahead) toast.error(formatApiError(err));
        setResults([]);
        setMeta({ total: 0, truncated: false });
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Typeahead (lazy): runs on debounced input for quick global lookups only.
  // Advanced mode waits for an explicit submit to avoid scattershot requests.
  useEffect(() => {
    if (mode !== "global") return;
    const params = buildParams();
    if (!params || params.q.length < 2) {
      setResults(null);
      return;
    }
    runSearch(params, { typeahead: true });
  }, [debouncedQ, mode, buildParams, runSearch]);

  // Advanced submits only via button / Enter.
  useEffect(() => {
    if (mode !== "advanced" || submittedAt === 0) return;
    runSearch(buildParams(), { typeahead: false });
  }, [submittedAt, mode, buildParams, runSearch]);

  function onSubmit(e) {
    e?.preventDefault();
    setSubmittedAt(Date.now());
    if (mode === "global") runSearch(buildParams(), { typeahead: false });
  }

  function openPatient(row) {
    const entry = {
      id: row.id,
      display: row.display_name_masked || `${row.first_name || ""} ${row.last_name || ""}`.trim() || row.id.slice(0, 8),
      dob: row.date_of_birth,
      viewedAt: new Date().toISOString(),
    };
    pushRecent(user.id, entry);
    setRecent(readRecent(user.id));
    navigate(`/patients/${row.id}`);
  }

  function onKeyDown(e) {
    if (!results || !results.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(results.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter" && activeIdx >= 0) {
      e.preventDefault();
      openPatient(results[activeIdx]);
    }
  }

  const highlightRx = useMemo(() => {
    const term = mode === "global" ? q : (fields.name || fields.phone || fields.address || fields.dob);
    return buildHighlightRegex(term);
  }, [mode, q, fields]);

  const hasQuery = mode === "global" ? q.trim().length > 0 : Object.values(fields).some((v) => v.trim());

  return (
    <div data-testid="patients-page" className="space-y-8 animate-in fade-in duration-300">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">Patient lookup</span>
          <h1 className="mt-2 font-display text-4xl font-medium tracking-tight text-foreground">Find a patient</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Search by name, phone, address, or DOB. Use <code className="rounded-sm bg-muted px-1">%</code> as a wildcard (e.g., <code className="rounded-sm bg-muted px-1">Test%</code>). PHI is masked in results.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {canCreate && (
            <Button
              data-testid="patients-new-btn"
              onClick={() => setOpen(true)}
              className="h-11 rounded-sm bg-primary px-5 hover:bg-[var(--primary-hover)]"
            >
              <Plus className="mr-2 h-4 w-4" /> New patient
            </Button>
          )}
        </div>
      </header>

      <form onSubmit={onSubmit} onKeyDown={onKeyDown} className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div className="inline-flex rounded-sm border border-border bg-card p-0.5 text-xs font-semibold">
            <button
              type="button"
              data-testid="search-mode-global"
              onClick={() => setMode("global")}
              className={`px-3 py-1.5 rounded-sm uppercase tracking-wider ${mode === "global" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            >
              Quick lookup
            </button>
            <button
              type="button"
              data-testid="search-mode-advanced"
              onClick={() => setMode("advanced")}
              className={`px-3 py-1.5 rounded-sm uppercase tracking-wider ${mode === "advanced" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            >
              Advanced
            </button>
          </div>
          {meta.truncated && (
            <span data-testid="too-many-candidates" className="text-xs text-warning">
              Too many candidates — refine your search for complete results.
            </span>
          )}
        </div>

        {mode === "global" ? (
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/70" />
            <Input
              data-testid="search-q"
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Test Patient · (555) 123-4567 · Test% · 01/15/1985"
              className="h-14 rounded-sm border-border pl-11 text-base"
            />
            {loading && (
              <div className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-[color:var(--sage-accent)] border-t-transparent" />
              </div>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <AdvancedInput testid="search-name"    label="Name"    placeholder="Test Patient / Test%" value={fields.name}    onChange={(v) => setFields((f) => ({ ...f, name: v }))} />
            <AdvancedInput testid="search-phone"   label="Phone"   placeholder="5551234567"       value={fields.phone}   onChange={(v) => setFields((f) => ({ ...f, phone: v }))} />
            <AdvancedInput testid="search-address" label="Address" placeholder="Meadow / Portland"value={fields.address} onChange={(v) => setFields((f) => ({ ...f, address: v }))} />
            <AdvancedInput testid="search-dob"     label="DOB"     placeholder="01/15/1985"       value={fields.dob}     onChange={(v) => setFields((f) => ({ ...f, dob: v }))} />
            <div className="md:col-span-4 flex justify-end">
              <Button
                type="submit"
                data-testid="search-submit"
                className="h-10 rounded-sm bg-primary px-6 hover:bg-[var(--primary-hover)]"
              >
                <Search className="mr-2 h-4 w-4" /> Search
              </Button>
            </div>
          </div>
        )}
      </form>

      {/* Result list / recently viewed / empty state */}
      {hasQuery ? (
        <SearchResults
          results={results}
          loading={loading}
          activeIdx={activeIdx}
          meta={meta}
          onOpen={openPatient}
          highlightRx={highlightRx}
        />
      ) : (
        <RecentPatients recent={recent} onOpen={(r) => navigate(`/patients/${r.id}`)} canCreate={canCreate} />
      )}

      {canCreate && (
        <PatientWizardDialog
          open={open}
          onClose={() => setOpen(false)}
          onCreated={(p) => {
            pushRecent(user.id, { id: p.id, display: `${p.first_name || ""} ${p.last_name || ""}`.trim(), dob: p.date_of_birth, viewedAt: new Date().toISOString() });
            setRecent(readRecent(user.id));
            toast.success("Patient added to recently-viewed");
          }}
          userId={user.id}
          tenantId={user.tenant_id}
        />
      )}
    </div>
  );
}

function AdvancedInput({ testid, label, placeholder, value, onChange }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
      <span>{label}</span>
      <Input
        data-testid={testid}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-10 rounded-sm border-border text-sm font-normal normal-case tracking-normal text-foreground"
      />
    </label>
  );
}

function SearchResults({ results, loading, activeIdx, meta, onOpen, highlightRx }) {
  if (results === null && loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14 rounded-sm" />)}
      </div>
    );
  }
  if (results === null) return null;
  if (results.length === 0) {
    return (
      <div data-testid="search-empty" className="rounded-sm border border-dashed border-border bg-card p-12 text-center">
        <User2 className="mx-auto h-10 w-10 text-muted-foreground/70" />
        <p className="mt-4 font-display text-lg text-foreground">No matching patients</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Try a different name, phone, address, or DOB. Wildcard <code className="rounded-sm bg-muted px-1">%</code> is supported.
        </p>
      </div>
    );
  }
  return (
    <div data-testid="search-results" className="overflow-hidden rounded-sm border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border bg-background px-4 py-2 text-xs uppercase tracking-wider text-muted-foreground">
        <span>{meta.total} result{meta.total === 1 ? "" : "s"}</span>
        <span className="text-[11px]">Use ↑↓ + Enter</span>
      </div>
      <ul role="listbox">
        {results.map((r, idx) => (
          <li
            key={r.id}
            role="option"
            aria-selected={idx === activeIdx}
            data-testid={`search-result-${r.id}`}
            onClick={() => onOpen(r)}
            onMouseEnter={() => {}}
            className={`cursor-pointer border-b border-border last:border-b-0 px-4 py-3 text-sm transition-colors ${
              idx === activeIdx ? "bg-primary/10" : "hover:bg-muted"
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 text-foreground">
                  <span className="font-medium">
                    <Highlight
                      value={r.display_name_masked || `${r.first_name || ""} ${r.last_name || ""}`.trim() || "—"}
                      rx={highlightRx}
                    />
                  </span>
                  {r.status === "deleted" && (
                    <span className="rounded-sm bg-destructive-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase text-destructive">deleted</span>
                  )}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  <Highlight value={`DOB ${r.date_of_birth || "—"}`} rx={highlightRx} />
                  <span className="mx-2 text-muted-foreground/70">·</span>
                  <Highlight value={r.primary_phone || "—"} rx={highlightRx} />
                  <span className="mx-2 text-muted-foreground/70">·</span>
                  <Highlight value={r.address_summary || "—"} rx={highlightRx} />
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-muted-foreground/70" />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RecentPatients({ recent, onOpen, canCreate }) {
  if (!recent.length) {
    return (
      <div data-testid="patients-empty-hero" className="rounded-sm border border-dashed border-border bg-card p-16 text-center">
        <Search className="mx-auto h-10 w-10 text-muted-foreground/70" />
        <p className="mt-4 font-display text-lg text-foreground">Search to find a patient</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Try <code className="rounded-sm bg-muted px-1">Test Patient</code>, <code className="rounded-sm bg-muted px-1">5551234567</code>, <code className="rounded-sm bg-muted px-1">01/15/1985</code>, or a wildcard like <code className="rounded-sm bg-muted px-1">Test%</code>.
          {canCreate && " New patient? Use the button above."}
        </p>
      </div>
    );
  }
  return (
    <section data-testid="recent-patients" className="space-y-3">
      <h2 className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">Recently viewed</h2>
      <ul className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
        {recent.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              data-testid={`recent-${r.id}`}
              onClick={() => onOpen(r)}
              className="group flex w-full items-center justify-between rounded-sm border border-border bg-card px-4 py-3 text-left transition-colors hover:bg-muted"
            >
              <div>
                <div className="font-medium text-foreground">{r.display || r.id.slice(0, 8)}</div>
                <div className="text-xs text-muted-foreground">DOB {r.dob || "—"}</div>
              </div>
              <ChevronRight className="h-4 w-4 text-muted-foreground/70 group-hover:text-primary" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
