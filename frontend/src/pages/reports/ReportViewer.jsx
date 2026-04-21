import { Link, useParams } from "react-router-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Columns,
  Download,
  Filter as FilterIcon,
  Loader2,
  Lock,
  RotateCcw,
  Save,
  Share2,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Skeleton } from "../../components/ui/skeleton";
import { Badge } from "../../components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Checkbox } from "../../components/ui/checkbox";
import { toast } from "sonner";
import { useAuth } from "../../contexts/AuthContext";
import {
  createView,
  deleteView,
  exportDownloadUrl,
  fetchReportMeta,
  formatApiError,
  listViews,
  pollExport,
  requestExport,
  runReport,
  updateView,
} from "./reportsApi";
import { formatCell } from "./formatters";

const PAGE_SIZES = [25, 50, 100, 200];

function defaultColumnSet(meta) {
  return new Set(meta?.default_columns || []);
}

export default function ReportViewer() {
  const { name } = useParams();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin" || user?.role === "super_admin";

  const [meta, setMeta] = useState(null);
  const [loadingMeta, setLoadingMeta] = useState(true);

  const [filters, setFilters] = useState({});
  const [sort, setSort] = useState(null);
  const [sortDir, setSortDir] = useState("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selectedCols, setSelectedCols] = useState(null);

  const [result, setResult] = useState(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState(null);

  const [views, setViews] = useState([]);
  const [activeViewId, setActiveViewId] = useState(null);

  const [saveOpen, setSaveOpen] = useState(false);
  const [exportDialog, setExportDialog] = useState(null); // {format, state}
  const [phiConsent, setPhiConsent] = useState(null); // {format, reason}

  // Load meta + saved views
  useEffect(() => {
    let cancelled = false;
    setLoadingMeta(true);
    setResult(null);
    (async () => {
      try {
        const m = await fetchReportMeta(name);
        if (cancelled) return;
        setMeta(m);
        setSort(m.default_sort);
        setSortDir(m.default_sort_dir);
        setSelectedCols(m.default_columns);
        const vs = await listViews(name);
        if (cancelled) return;
        setViews(vs);
        const def = vs.find((v) => v.is_default);
        if (def) applyView(def, m);
      } catch (e) {
        if (!cancelled) setRunError(formatApiError(e));
      } finally {
        if (!cancelled) setLoadingMeta(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const applyView = useCallback((v, metaOverride) => {
    const m = metaOverride || meta;
    if (!m) return;
    setActiveViewId(v.id);
    setFilters(v.filters || {});
    setSort(v.sort || m.default_sort);
    setSortDir(v.sort_dir || m.default_sort_dir);
    setSelectedCols(v.columns?.length ? v.columns : m.default_columns);
    setPage(1);
  }, [meta]);

  // Run when state changes
  const runRef = useRef(0);
  useEffect(() => {
    if (!meta) return;
    const token = ++runRef.current;
    setRunLoading(true);
    setRunError(null);
    runReport(name, {
      filters, sort, sort_dir: sortDir, page, page_size: pageSize,
      columns: selectedCols || meta.default_columns,
    })
      .then((r) => {
        if (runRef.current !== token) return;
        setResult(r);
      })
      .catch((e) => {
        if (runRef.current !== token) return;
        setRunError(formatApiError(e));
        setResult(null);
      })
      .finally(() => {
        if (runRef.current !== token) return;
        setRunLoading(false);
      });
  }, [name, meta, filters, sort, sortDir, page, pageSize, selectedCols]);

  const totalPages = result?.total ? Math.max(1, Math.ceil(result.total / pageSize)) : 1;

  function handleFilterChange(key, value) {
    setFilters((f) => {
      const next = { ...f };
      if (value === "" || value === null || value === undefined) delete next[key];
      else next[key] = value;
      return next;
    });
    setPage(1);
  }

  function clearFilters() {
    setFilters({});
    setPage(1);
  }

  function toggleColumn(key) {
    setSelectedCols((cs) => {
      const cur = new Set(cs || meta.default_columns);
      if (cur.has(key)) cur.delete(key);
      else cur.add(key);
      // Preserve original column order from the meta
      return meta.columns.map((c) => c.key).filter((k) => cur.has(k));
    });
  }

  function moveColumn(key, direction) {
    setSelectedCols((cs) => {
      const current = [...(cs || meta.default_columns)];
      const idx = current.indexOf(key);
      if (idx < 0) return current;
      const target = direction === "up" ? idx - 1 : idx + 1;
      if (target < 0 || target >= current.length) return current;
      [current[idx], current[target]] = [current[target], current[idx]];
      return current;
    });
  }

  function resetView() {
    if (!meta) return;
    setFilters({});
    setSort(meta.default_sort);
    setSortDir(meta.default_sort_dir);
    setSelectedCols(meta.default_columns);
    setActiveViewId(null);
    setPage(1);
  }

  function toggleSort(colKey) {
    if (!colKey) return;
    const sortable = meta.sort_options.some((s) => s.key === colKey);
    if (!sortable) return;
    if (sort === colKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSort(colKey); setSortDir("desc"); }
  }

  async function handleExport(format, reason = null) {
    setExportDialog({ format, state: "submitting", error: null });
    try {
      const resp = await requestExport(name, {
        format,
        filters,
        sort, sort_dir: sortDir,
        columns: selectedCols || meta.default_columns,
        reason: reason || undefined,
      });
      setExportDialog({
        format, state: "polling", exportId: resp.export_id,
        passwordProtected: resp.password_protected,
      });
      // Poll every 1.5s up to 60s
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const s = await pollExport(resp.export_id);
        if (s.status === "ready") {
          setExportDialog({
            format, state: "ready", exportId: resp.export_id,
            downloadToken: s.download_token,
            passwordProtected: s.password_protected,
            protectionKind: s.protection_kind,
            oneTimePassword: s.one_time_password,
            filename: s.filename,
            rows: s.rows,
          });
          return;
        }
        if (s.status === "failed") {
          setExportDialog({ format, state: "failed", error: s.error || "Export failed" });
          return;
        }
      }
      setExportDialog({ format, state: "failed", error: "Export timed out." });
    } catch (e) {
      setExportDialog({ format, state: "failed", error: formatApiError(e) });
    }
  }

  function requestExportFormat(format) {
    if (meta?.contains_phi) {
      setPhiConsent({ format, reason: "" });
    } else {
      handleExport(format);
    }
  }

  async function handleDeleteView(viewId) {
    try {
      await deleteView(viewId);
      setViews((vs) => vs.filter((v) => v.id !== viewId));
      if (activeViewId === viewId) setActiveViewId(null);
      toast.success("Saved view deleted.");
    } catch (e) {
      toast.error(formatApiError(e));
    }
  }

  if (loadingMeta) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-80" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!meta) {
    return (
      <div data-testid="report-error" className="rounded-sm border border-border bg-card p-10 text-center text-muted-foreground">
        Report not found. <Link to="/reports" className="underline">Back to Reports</Link>
      </div>
    );
  }

  const visibleColumns = meta.columns.filter((c) =>
    (selectedCols || meta.default_columns).includes(c.key),
  );

  return (
    <div data-testid="report-viewer" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link
            to="/reports"
            className="mb-2 inline-flex items-center gap-1 text-xs uppercase tracking-[0.15em] text-muted-foreground hover:text-foreground"
            data-testid="report-back-btn"
          >
            <ArrowLeft className="h-3 w-3" /> All reports
          </Link>
          <h1 className="font-display text-3xl font-medium tracking-tight">
            {meta.title}
            {meta.contains_phi && (
              <Badge className="ml-3 align-middle" variant="outline">
                <Lock className="mr-1 h-3 w-3" /> PHI
              </Badge>
            )}
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{meta.description}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Saved views dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" className="rounded-sm" data-testid="report-views-btn">
                <Save className="mr-2 h-4 w-4" />
                {activeViewId
                  ? views.find((v) => v.id === activeViewId)?.name || "Saved views"
                  : "Views"}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-72">
              <DropdownMenuLabel>Saved views</DropdownMenuLabel>
              {views.length === 0 && (
                <DropdownMenuItem disabled>No saved views yet</DropdownMenuItem>
              )}
              {views.map((v) => (
                <DropdownMenuItem
                  key={v.id}
                  onClick={() => applyView(v)}
                  data-testid={`report-view-${v.id}`}
                  className="flex items-center justify-between gap-2"
                >
                  <span className="flex items-center gap-1 truncate">
                    {v.is_shared && <Share2 className="h-3 w-3 text-primary" />}
                    {v.is_default && <Badge variant="outline" className="text-[9px]">default</Badge>}
                    {v.name}
                  </span>
                  {(v.owner_user_id === user?.id || isAdmin) && (
                    <Trash2
                      className="h-3.5 w-3.5 opacity-60 hover:opacity-100"
                      onClick={(e) => { e.stopPropagation(); handleDeleteView(v.id); }}
                    />
                  )}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setSaveOpen(true)} data-testid="report-save-view-btn">
                <Save className="mr-2 h-4 w-4" /> Save current view
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          {/* Column picker */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" className="rounded-sm" data-testid="report-columns-btn">
                <Columns className="mr-2 h-4 w-4" /> Columns
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-80">
              <DropdownMenuLabel>Show &amp; reorder columns</DropdownMenuLabel>
              {/* Selected columns first (with reorder arrows), then hidden */}
              {(selectedCols || meta.default_columns).map((key, idx, arr) => {
                const c = meta.columns.find((x) => x.key === key);
                if (!c) return null;
                return (
                  <div
                    key={key}
                    className="flex items-center justify-between gap-2 px-2 py-1.5 hover:bg-muted/40"
                    data-testid={`report-col-row-${key}`}
                  >
                    <div className="flex items-center gap-2">
                      <Checkbox
                        checked={true}
                        onCheckedChange={() => toggleColumn(key)}
                        data-testid={`report-col-toggle-${key}`}
                      />
                      <span className="text-sm">
                        {c.label}
                        {c.phi && <Lock className="ml-2 inline h-3 w-3 text-warning" />}
                      </span>
                    </div>
                    <div className="flex items-center gap-0.5 opacity-70">
                      <button
                        type="button"
                        disabled={idx === 0}
                        onClick={() => moveColumn(key, "up")}
                        className="rounded-sm p-0.5 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-30"
                        data-testid={`report-col-up-${key}`}
                        aria-label={`Move ${c.label} up`}
                      ><ChevronUp className="h-3 w-3" /></button>
                      <button
                        type="button"
                        disabled={idx === arr.length - 1}
                        onClick={() => moveColumn(key, "down")}
                        className="rounded-sm p-0.5 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-30"
                        data-testid={`report-col-down-${key}`}
                        aria-label={`Move ${c.label} down`}
                      ><ChevronDown className="h-3 w-3" /></button>
                    </div>
                  </div>
                );
              })}
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                Hidden
              </DropdownMenuLabel>
              {meta.columns.filter((c) => !(selectedCols || meta.default_columns).includes(c.key)).map((c) => (
                <DropdownMenuCheckboxItem
                  key={c.key}
                  checked={false}
                  onCheckedChange={() => toggleColumn(c.key)}
                  data-testid={`report-col-toggle-${c.key}`}
                >
                  {c.label}
                  {c.phi && <Lock className="ml-2 h-3 w-3 text-warning" />}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          {/* Reset view */}
          <Button
            variant="ghost"
            className="rounded-sm"
            onClick={resetView}
            data-testid="report-reset-btn"
            title="Reset to default filters, columns, and sort"
          >
            <RotateCcw className="mr-2 h-4 w-4" /> Reset
          </Button>

          {/* Export menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button className="rounded-sm" data-testid="report-export-btn">
                <Download className="mr-2 h-4 w-4" /> Export
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {(meta.export_formats || []).map((fmt) => (
                <DropdownMenuItem
                  key={fmt}
                  onClick={() => requestExportFormat(fmt)}
                  data-testid={`report-export-${fmt}`}
                >
                  {fmt.toUpperCase()}
                  {meta.contains_phi && (
                    <span className="ml-2 text-[10px] text-muted-foreground">(password-protected)</span>
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Filters */}
      {meta.filters?.length > 0 && (
        <section
          data-testid="report-filter-panel"
          className="flex flex-wrap items-end gap-3 rounded-sm border border-border bg-card p-4"
        >
          <FilterIcon className="h-4 w-4 text-muted-foreground" />
          {meta.filters.map((f) => (
            <FilterControl
              key={f.key}
              filter={f}
              value={filters[f.key]}
              onChange={(v) => handleFilterChange(f.key, v)}
            />
          ))}
          {Object.keys(filters).length > 0 && (
            <Button
              variant="ghost"
              className="rounded-sm"
              onClick={clearFilters}
              data-testid="report-filters-clear-btn"
            >
              <X className="mr-1 h-3 w-3" /> Clear
            </Button>
          )}
        </section>
      )}

      {/* Aggregates (optional, from result) */}
      {result?.aggregates && Object.keys(result.aggregates).length > 0 && (
        <AggregatesBar aggregates={result.aggregates} columns={meta.columns} />
      )}

      {/* Results table */}
      <section className="rounded-sm border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <div className="text-xs text-muted-foreground" data-testid="report-total">
            {runLoading ? "Loading…" : `${result?.total || 0} rows`}
          </div>
          <div className="flex items-center gap-2 text-xs">
            <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPage(1); }}>
              <SelectTrigger className="h-8 w-28 rounded-sm" data-testid="report-page-size">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZES.map((n) => (<SelectItem key={n} value={String(n)}>{n}/page</SelectItem>))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="report-table">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
              <tr>
                {visibleColumns.map((c) => (
                  <th
                    key={c.key}
                    onClick={() => toggleSort(c.key)}
                    className={`px-3 py-2 ${c.align === "right" ? "text-right" : ""} ${
                      meta.sort_options.some((s) => s.key === c.key)
                        ? "cursor-pointer select-none hover:text-foreground"
                        : ""
                    }`}
                    data-testid={`report-col-${c.key}`}
                  >
                    <span className="inline-flex items-center gap-1">
                      {c.label}
                      {c.phi && <Lock className="h-3 w-3 text-warning" />}
                      {sort === c.key && (
                        sortDir === "asc"
                          ? <ArrowUp className="h-3 w-3" />
                          : <ArrowDown className="h-3 w-3" />
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runLoading && (
                <tr><td colSpan={visibleColumns.length} className="p-8 text-center text-muted-foreground">
                  <Loader2 className="mx-auto h-6 w-6 animate-spin" />
                </td></tr>
              )}
              {!runLoading && runError && (
                <tr><td colSpan={visibleColumns.length} className="p-8 text-center text-destructive" data-testid="report-table-error">
                  {runError}
                </td></tr>
              )}
              {!runLoading && !runError && (result?.rows || []).length === 0 && (
                <tr><td colSpan={visibleColumns.length} className="p-12 text-center text-muted-foreground" data-testid="report-table-empty">
                  No results match your filters.
                </td></tr>
              )}
              {!runLoading && (result?.rows || []).map((row, i) => (
                <tr key={i} className="border-t border-border hover:bg-muted/30" data-testid={`report-row-${i}`}>
                  {visibleColumns.map((c) => (
                    <td
                      key={c.key}
                      className={`px-3 py-2 ${c.align === "right" ? "text-right tabular-nums" : ""}`}
                    >
                      {formatCell(row[c.key], c.type)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs">
          <div className="text-muted-foreground">
            Page {page} of {totalPages}
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="outline" size="sm" className="rounded-sm"
              disabled={page <= 1 || runLoading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              data-testid="report-prev-btn"
            ><ChevronLeft className="h-3 w-3" /></Button>
            <Button
              variant="outline" size="sm" className="rounded-sm"
              disabled={page >= totalPages || runLoading}
              onClick={() => setPage((p) => p + 1)}
              data-testid="report-next-btn"
            ><ChevronRight className="h-3 w-3" /></Button>
          </div>
        </div>
      </section>

      {/* Save-view dialog */}
      <SaveViewDialog
        open={saveOpen}
        onClose={() => setSaveOpen(false)}
        meta={meta}
        isAdmin={isAdmin}
        state={{
          filters, sort, sortDir,
          columns: selectedCols || meta.default_columns,
        }}
        onSaved={(v) => {
          setViews((vs) => [...vs.filter((x) => x.id !== v.id), v]);
          setActiveViewId(v.id);
          setSaveOpen(false);
          toast.success("View saved.");
        }}
      />

      {/* Export progress / download dialog */}
      <ExportResultDialog
        state={exportDialog}
        onClose={() => setExportDialog(null)}
      />

      {/* Pre-export PHI consent dialog */}
      <PhiConsentDialog
        state={phiConsent}
        format={phiConsent?.format}
        meta={meta}
        onClose={() => setPhiConsent(null)}
        onConfirm={(reason) => {
          const fmt = phiConsent.format;
          setPhiConsent(null);
          handleExport(fmt, reason);
        }}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// PHI consent / purpose-of-export dialog (pre-export)
// ---------------------------------------------------------------------------

function PhiConsentDialog({ state, format, meta, onClose, onConfirm }) {
  const [reason, setReason] = useState("");

  useEffect(() => { setReason(""); }, [state?.format]);

  const open = !!state;
  const fmt = (format || "").toLowerCase();
  const protectionCopy =
    fmt === "pdf"
      ? "The PDF will be natively encrypted. Any PDF reader will prompt for the password on open."
      : "The file will be packaged in an AES-256 password-protected ZIP. Unzip with the password to extract the file.";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="rounded-sm" data-testid="phi-consent-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-warning" />
            Export contains PHI
          </DialogTitle>
          <DialogDescription>
            “{meta?.title}” may include protected health information.
            Under the HIPAA minimum-necessary rule you should only export
            PHI for a specific, documented purpose.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="rounded-sm border border-warning bg-warning-soft p-3 text-xs leading-relaxed text-foreground">
            <p className="font-semibold text-warning">
              Secure export — {fmt.toUpperCase()}
            </p>
            <p className="mt-1 text-muted-foreground">{protectionCopy}</p>
            <p className="mt-1 text-muted-foreground">
              A one-time password will be generated and shown to you{" "}
              <strong>only once</strong>. It is never emailed, logged, or
              stored in plaintext — you will not be able to retrieve it
              later.
            </p>
          </div>
          <div>
            <Label htmlFor="export-reason">
              Purpose of export <span className="text-muted-foreground">(optional, recorded in audit)</span>
            </Label>
            <Input
              id="export-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Quarterly compliance review for Dr. Patel"
              className="rounded-sm"
              data-testid="phi-consent-reason"
              maxLength={500}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} data-testid="phi-consent-cancel">
            Cancel
          </Button>
          <Button
            onClick={() => onConfirm(reason.trim() || null)}
            data-testid="phi-consent-confirm"
          >
            <Download className="mr-2 h-4 w-4" />
            Acknowledge &amp; export
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Filter control
// ---------------------------------------------------------------------------

function FilterControl({ filter, value, onChange }) {
  if (filter.type === "enum") {
    return (
      <div className="flex flex-col gap-1">
        <Label className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          {filter.label}
        </Label>
        <Select value={value || "__any__"} onValueChange={(v) => onChange(v === "__any__" ? null : v)}>
          <SelectTrigger className="h-9 w-48 rounded-sm" data-testid={`filter-${filter.key}`}>
            <SelectValue placeholder="Any" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__any__">Any</SelectItem>
            {(filter.options || []).map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  }
  if (filter.type === "date_range") {
    return (
      <div className="flex flex-col gap-1">
        <Label className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          {filter.label}
        </Label>
        <Input
          type="date"
          value={value || ""}
          onChange={(e) => onChange(e.target.value || null)}
          className="h-9 w-40 rounded-sm"
          data-testid={`filter-${filter.key}`}
        />
      </div>
    );
  }
  if (filter.type === "integer") {
    return (
      <div className="flex flex-col gap-1">
        <Label className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          {filter.label}
        </Label>
        <Input
          type="number"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
          className="h-9 w-32 rounded-sm"
          data-testid={`filter-${filter.key}`}
        />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
        {filter.label}
      </Label>
      <Input
        value={value || ""}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder={filter.placeholder || ""}
        className="h-9 w-48 rounded-sm"
        data-testid={`filter-${filter.key}`}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Aggregates bar
// ---------------------------------------------------------------------------

function AggregatesBar({ aggregates, columns }) {
  const cents = (v) => {
    const abs = Math.abs(v);
    return `${v < 0 ? "-" : ""}$${Math.floor(abs / 100).toLocaleString()}.${String(abs % 100).padStart(2, "0")}`;
  };
  const entries = [];
  for (const [k, v] of Object.entries(aggregates)) {
    if (typeof v === "number") {
      const isCents = k.endsWith("_cents");
      entries.push({
        key: k,
        label: k.replace(/_cents$/, "").replace(/_/g, " "),
        value: isCents ? cents(v) : v.toLocaleString(),
      });
    }
  }
  if (!entries.length) return null;
  return (
    <div data-testid="report-aggregates" className="flex flex-wrap gap-3">
      {entries.map((e) => (
        <div
          key={e.key}
          className="rounded-sm border border-border bg-card px-4 py-2"
          data-testid={`aggregate-${e.key}`}
        >
          <div className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
            {e.label}
          </div>
          <div className="font-display text-lg font-medium tabular-nums">{e.value}</div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Save view dialog
// ---------------------------------------------------------------------------

function SaveViewDialog({ open, onClose, meta, state, onSaved, isAdmin }) {
  const [name, setName] = useState("");
  const [isShared, setShared] = useState(false);
  const [isDefault, setDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (open) { setName(""); setShared(false); setDefault(false); setErr(null); }
  }, [open]);

  async function handleSave() {
    if (!name.trim()) { setErr("Name is required."); return; }
    setSaving(true);
    setErr(null);
    try {
      const v = await createView(meta.name, {
        name: name.trim(),
        columns: state.columns,
        filters: state.filters,
        sort: state.sort,
        sort_dir: state.sortDir,
        is_shared: isShared,
        is_default: isDefault,
      });
      onSaved(v);
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="rounded-sm" data-testid="save-view-dialog">
        <DialogHeader>
          <DialogTitle>Save current view</DialogTitle>
          <DialogDescription>
            Your filters, columns, and sort will be stored under a name you choose.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="view-name">Name</Label>
            <Input
              id="view-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. My pending claims"
              className="rounded-sm"
              data-testid="save-view-name"
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={isDefault}
              onCheckedChange={(v) => setDefault(!!v)}
              data-testid="save-view-default"
            />
            Use as my default view for this report
          </label>
          {isAdmin && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={isShared}
                onCheckedChange={(v) => setShared(!!v)}
                data-testid="save-view-shared"
              />
              Share with everyone in my tenant
            </label>
          )}
          {err && <div className="text-xs text-destructive" role="alert">{err}</div>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving} data-testid="save-view-submit">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save view"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Export result dialog
// ---------------------------------------------------------------------------

function ExportResultDialog({ state, onClose }) {
  const open = !!state;
  const [copied, setCopied] = useState(false);

  useEffect(() => { setCopied(false); }, [state?.exportId]);

  const downloadHref = state?.downloadToken
    ? exportDownloadUrl(state.exportId, state.downloadToken)
    : null;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="rounded-sm" data-testid="export-result-dialog">
        <DialogHeader>
          <DialogTitle>
            {state?.state === "ready" ? "Export ready" : "Preparing export…"}
          </DialogTitle>
          <DialogDescription>
            {state?.format?.toUpperCase()} export of your current filters and columns.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {(state?.state === "submitting" || state?.state === "polling") && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {state.state === "submitting" ? "Submitting request…" : "Generating file…"}
            </div>
          )}
          {state?.state === "failed" && (
            <div className="rounded-sm bg-destructive-soft p-3 text-sm text-destructive" data-testid="export-error">
              {state.error || "Export failed."}
            </div>
          )}
          {state?.state === "ready" && (
            <>
              {state.passwordProtected && state.oneTimePassword && (
                <div className="rounded-sm border border-warning bg-warning-soft p-4">
                  <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-warning">
                    <Lock className="h-4 w-4" />
                    {state.protectionKind === "pdf_native"
                      ? "PDF password required"
                      : "ZIP archive password required"}
                  </div>
                  <p className="mb-2 text-xs text-muted-foreground">
                    {state.protectionKind === "pdf_native"
                      ? "This PDF is natively encrypted. Any PDF reader will prompt for the password on open."
                      : "This export is packaged inside a password-protected AES-256 ZIP archive."}
                    {" "}Copy this password now — it is shown{" "}
                    <strong>only once</strong> and cannot be retrieved later.
                  </p>
                  <div className="flex items-center gap-2">
                    <code
                      data-testid="export-password"
                      className="flex-1 rounded-sm border border-border bg-card px-3 py-2 font-mono text-sm select-all"
                    >
                      {state.oneTimePassword}
                    </code>
                    <Button
                      type="button"
                      variant="outline"
                      className="rounded-sm"
                      data-testid="export-copy-password"
                      onClick={() => {
                        navigator.clipboard?.writeText(state.oneTimePassword);
                        setCopied(true);
                      }}
                    >
                      {copied ? "Copied" : "Copy"}
                    </Button>
                  </div>
                </div>
              )}
              {state.passwordProtected && !state.oneTimePassword && (
                <div className="rounded-sm border border-border bg-muted p-3 text-xs text-muted-foreground">
                  The password for this export was already revealed. If you lost it,
                  generate a new export.
                </div>
              )}
              <div className="rounded-sm border border-border bg-card p-3 text-xs text-muted-foreground">
                {state.rows?.toLocaleString()} rows · file: <code>{state.filename}</code>
              </div>
            </>
          )}
        </div>
        <DialogFooter>
          {state?.state === "ready" && downloadHref && (
            <Button asChild className="rounded-sm" data-testid="export-download-btn">
              <a href={downloadHref} download>
                <Download className="mr-2 h-4 w-4" /> Download
              </a>
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
