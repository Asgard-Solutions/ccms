/**
 * MediaCard — Clinical Media (x-rays, MRI/CT reports, US, photos, outside
 * records, PDFs). Grid of thumbnails/icons with upload + preview + delete.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Download, Image as ImageIcon, FileText, PlusCircle, Trash2, Loader2,
} from "lucide-react";
import { api, formatApiError } from "../../api/client";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../../components/ui/select";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";
import { Skeleton } from "../../components/ui/skeleton";
import ConfirmDialog from "../../components/ConfirmDialog";
import { formatDateTime } from "../../utils/time";

const CATEGORIES = [
  { value: "xray", label: "X-ray" },
  { value: "mri_ct_report", label: "MRI / CT" },
  { value: "ultrasound", label: "Ultrasound" },
  { value: "clinical_photo", label: "Clinical photo" },
  { value: "outside_record", label: "Outside record" },
  { value: "other_pdf", label: "Other PDF" },
];
const SOURCES = [
  { value: "in_clinic", label: "In-clinic" },
  { value: "outside_imaging_center", label: "Outside imaging center" },
  { value: "patient_provided", label: "Patient-provided" },
  { value: "records_request", label: "Records request" },
];

export default function MediaCard({ patientId, canWrite, onReauthNeeded }) {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("all");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [blobUrl, setBlobUrl] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/patients/${patientId}/clinical/media`);
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e));
      setRows([]);
    }
  }, [patientId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleReauthAware = (err) => {
    if (err?.response?.status === 401 && /re-auth/i.test(err.response?.data?.detail || "")) {
      onReauthNeeded?.();
      return true;
    }
    return false;
  };

  const filtered = rows === null ? null : filter === "all"
    ? rows : rows.filter((r) => r.category === filter);

  const openDetail = async (m) => {
    setDetail(m);
    setBlobUrl(null);
    try {
      const resp = await api.get(
        `/patients/${patientId}/clinical/media/${m.id}/download`,
        { responseType: "blob" },
      );
      setBlobUrl(URL.createObjectURL(resp.data));
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const closeDetail = () => {
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    setBlobUrl(null);
    setDetail(null);
  };

  const doDelete = async (m) => {
    try {
      await api.delete(`/patients/${patientId}/clinical/media/${m.id}`);
      toast.success("Media removed");
      closeDetail();
      load();
    } catch (e) {
      if (!handleReauthAware(e)) toast.error(formatApiError(e));
      throw e;
    }
  };

  return (
    <section data-testid="clinical-media-card" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-lg font-semibold text-foreground">
            Imaging &amp; Clinical Media
          </h3>
          <p className="text-sm text-muted-foreground">
            X-rays, MRI/CT reports, ultrasound, clinical photos, outside
            records. Files are immutable after upload; metadata is editable.
          </p>
        </div>
        {canWrite && (
          <Button
            size="sm"
            onClick={() => setUploadOpen(true)}
            data-testid="media-upload-btn"
            className="rounded-sm"
          >
            <PlusCircle className="mr-1.5 h-3.5 w-3.5" />
            Upload
          </Button>
        )}
      </div>

      <div data-testid="media-filter-chips" className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => setFilter("all")}
          data-testid="media-filter-all"
          className={`rounded-sm border px-2.5 py-1 text-xs transition-colors ${
            filter === "all" ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted/40"
          }`}
        >
          All
        </button>
        {CATEGORIES.map((c) => (
          <button
            key={c.value}
            type="button"
            onClick={() => setFilter(c.value)}
            data-testid={`media-filter-${c.value}`}
            className={`rounded-sm border px-2.5 py-1 text-xs transition-colors ${
              filter === c.value ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted/40"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {filtered === null ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Skeleton className="h-32 rounded-lg" />
          <Skeleton className="h-32 rounded-lg" />
        </div>
      ) : filtered.length === 0 ? (
        <div
          data-testid="media-empty"
          className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-dashed border-border bg-card/60 px-5 py-4"
        >
          <div className="flex items-center gap-3">
            <ImageIcon className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">
                No imaging uploaded
              </p>
              <p className="text-xs text-muted-foreground">
                Upload x-rays, MRI or CT reports, ultrasound, clinical photos, or outside records.
              </p>
            </div>
          </div>
          {canWrite && (
            <Button
              size="sm"
              onClick={() => setUploadOpen(true)}
              data-testid="media-empty-upload"
              className="rounded-full"
            >
              <PlusCircle className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Upload
            </Button>
          )}
        </div>
      ) : (
        <div data-testid="media-grid" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {filtered.map((m) => {
            const isImage = (m.mime_type || "").startsWith("image/");
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => openDetail(m)}
                data-testid={`media-tile-${m.id}`}
                className="group block overflow-hidden rounded-lg border border-border bg-card text-left transition-colors hover:bg-muted/40"
              >
                <div className="flex h-28 items-center justify-center bg-muted">
                  {isImage ? (
                    <ImageIcon className="h-10 w-10 text-muted-foreground" />
                  ) : (
                    <FileText className="h-10 w-10 text-muted-foreground" />
                  )}
                </div>
                <div className="p-2">
                  <div className="truncate text-xs font-semibold text-foreground">
                    {CATEGORIES.find((c) => c.value === m.category)?.label || m.category}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {m.body_region || "—"} · {formatDateTime(m.study_date || m.uploaded_at)}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        patientId={patientId}
        onUploaded={() => {
          load();
          setUploadOpen(false);
        }}
        onReauthNeeded={onReauthNeeded}
      />

      <DetailDialog
        detail={detail}
        blobUrl={blobUrl}
        canWrite={canWrite}
        onClose={closeDetail}
        onDelete={(m) => setConfirmDelete(m)}
      />

      <ConfirmDialog
        open={!!confirmDelete}
        onOpenChange={(v) => !v && setConfirmDelete(null)}
        title="Remove media from chart?"
        description={
          confirmDelete
            ? `"${confirmDelete.original_filename}" will be hidden from the chart. The underlying file is retained for compliance.`
            : undefined
        }
        confirmLabel="Remove"
        destructive
        onConfirm={async () => {
          if (confirmDelete) await doDelete(confirmDelete);
        }}
        testId="media-delete-confirm"
      />
    </section>
  );
}

function UploadDialog({ open, onOpenChange, patientId, onUploaded, onReauthNeeded }) {
  const fileRef = useRef(null);
  const [form, setForm] = useState({
    category: "xray", source: "in_clinic", body_region: "",
    study_date: "", impression_findings: "",
  });
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!open) {
      setForm({ category: "xray", source: "in_clinic", body_region: "", study_date: "", impression_findings: "" });
    }
  }, [open]);

  const submit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.error("Choose a file first");
      return;
    }
    setUploading(true);
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(form).forEach(([k, v]) => {
      if (v) fd.append(k, v);
    });
    try {
      await api.post(
        `/patients/${patientId}/clinical/media`,
        fd,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      toast.success("Uploaded");
      onUploaded();
    } catch (e) {
      if (e?.response?.status === 401 && /re-auth/i.test(e.response?.data?.detail || "")) {
        onReauthNeeded?.();
      } else {
        toast.error(formatApiError(e));
      }
    } finally {
      setUploading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="media-upload-dialog" className="max-w-lg rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">Upload clinical media</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              File (PNG / JPEG / WebP / HEIC / PDF, up to 25 MB)
            </Label>
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/heic,image/heif,application/pdf"
              data-testid="media-upload-file"
              className="mt-1 block w-full rounded-sm border border-border bg-card p-2 text-sm"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">Category</Label>
              <Select
                value={form.category}
                onValueChange={(v) => setForm((f) => ({ ...f, category: v }))}
              >
                <SelectTrigger data-testid="media-upload-category" className="rounded-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">Source</Label>
              <Select
                value={form.source}
                onValueChange={(v) => setForm((f) => ({ ...f, source: v }))}
              >
                <SelectTrigger data-testid="media-upload-source" className="rounded-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SOURCES.map((c) => (
                    <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">Body region</Label>
              <Input
                value={form.body_region}
                onChange={(e) => setForm((f) => ({ ...f, body_region: e.target.value }))}
                data-testid="media-upload-body-region"
                className="rounded-sm"
              />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">Study date</Label>
              <Input
                type="date"
                value={form.study_date}
                onChange={(e) => setForm((f) => ({ ...f, study_date: e.target.value }))}
                data-testid="media-upload-study-date"
                className="rounded-sm"
              />
            </div>
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-muted-foreground">
              Impression / findings
            </Label>
            <Textarea
              rows={3}
              value={form.impression_findings}
              onChange={(e) => setForm((f) => ({ ...f, impression_findings: e.target.value }))}
              data-testid="media-upload-impression"
              className="rounded-sm"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-sm">Cancel</Button>
          <Button
            onClick={submit}
            disabled={uploading}
            data-testid="media-upload-submit-btn"
            className="rounded-sm"
          >
            {uploading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <PlusCircle className="mr-1.5 h-3.5 w-3.5" />}
            Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DetailDialog({ detail, blobUrl, canWrite, onClose, onDelete }) {
  if (!detail) return null;
  const isImage = (detail.mime_type || "").startsWith("image/");
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="media-detail-dialog" className="max-w-3xl rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-display">{detail.original_filename}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <Badge variant="outline" className="text-[10px]">
            {CATEGORIES.find((c) => c.value === detail.category)?.label || detail.category}
          </Badge>
          {detail.body_region && <span>Region · {detail.body_region}</span>}
          {detail.source && <span>Source · {detail.source.replace("_", " ")}</span>}
          {detail.study_date && <span>Study · {formatDateTime(detail.study_date)}</span>}
        </div>
        {detail.impression_findings && (
          <div className="rounded-sm border border-border bg-muted/30 p-3 text-xs">
            <div className="font-semibold text-foreground">Impression / findings</div>
            <p className="mt-1 whitespace-pre-wrap text-muted-foreground">
              {detail.impression_findings}
            </p>
          </div>
        )}
        <div className="max-h-[55vh] overflow-auto rounded-sm border border-border bg-muted/30">
          {!blobUrl ? (
            <div className="flex h-40 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : isImage ? (
            <img
              src={blobUrl}
              alt={detail.original_filename}
              data-testid="media-detail-image"
              className="mx-auto block max-h-[55vh]"
            />
          ) : (
            <iframe
              src={blobUrl}
              title={detail.original_filename}
              data-testid="media-detail-iframe"
              className="h-[55vh] w-full"
            />
          )}
        </div>
        <DialogFooter>
          {blobUrl && (
            <a
              href={blobUrl}
              download={detail.original_filename}
              data-testid="media-detail-download"
              className="inline-flex items-center gap-1.5 rounded-sm border border-border px-3 py-1.5 text-sm"
            >
              <Download className="h-3.5 w-3.5" /> Download
            </a>
          )}
          {canWrite && (
            <Button
              variant="outline"
              onClick={() => onDelete(detail)}
              data-testid="media-detail-delete"
              className="rounded-sm"
            >
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
              Delete
            </Button>
          )}
          <Button onClick={onClose} className="rounded-sm">Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
