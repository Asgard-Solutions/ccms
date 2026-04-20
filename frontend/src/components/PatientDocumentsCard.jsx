import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { CreditCard, Download, FileText, ImagePlus, Loader2, Trash2, Upload } from "lucide-react";
import { api, formatApiError } from "../api/client";
import { Button } from "./ui/button";
import ReauthDialog from "./ReauthDialog";

const CATEGORIES = [
  { value: "insurance_card_front", label: "Insurance card — front", icon: CreditCard },
  { value: "insurance_card_back", label: "Insurance card — back", icon: CreditCard },
  { value: "drivers_license", label: "Driver's license / ID", icon: FileText },
  { value: "referral_letter", label: "Referral letter", icon: FileText },
  { value: "imaging_report", label: "Imaging report", icon: FileText },
  { value: "intake_form", label: "Signed intake form", icon: FileText },
  { value: "consent_receipt", label: "Consent receipt", icon: FileText },
  { value: "other", label: "Other document", icon: FileText },
];

function needsReauth(err) {
  if (err?.response?.status !== 401) return false;
  const detail = err.response?.data?.detail || "";
  return typeof detail === "string" && /re-auth/i.test(detail);
}

/**
 * PatientDocumentsCard — insurance-card-first upload + inline list + delete.
 * Files are PHI — links below trigger an authenticated download via the
 * backend, which streams signed bytes + audits the access. Upload + delete
 * are reauth-gated; a 401 automatically pops the ReauthDialog and retries.
 */
export default function PatientDocumentsCard({ patientId, canEdit }) {
  const [documents, setDocuments] = useState(null);
  const [uploading, setUploading] = useState(null); // `category` while uploading
  const [pendingDelete, setPendingDelete] = useState(null);
  const [reauthOpen, setReauthOpen] = useState(false);
  const pendingAction = useRef(null); // () => Promise — rerun after reauth
  const inputsRef = useRef({});

  const load = useCallback(async () => {
    setDocuments(null);
    try {
      const { data } = await api.get(`/patients/${patientId}/documents`);
      setDocuments(data || []);
    } catch {
      setDocuments([]);
    }
  }, [patientId]);

  useEffect(() => { load(); }, [load]);

  async function runWithReauth(fn) {
    try {
      await fn();
    } catch (err) {
      if (needsReauth(err)) {
        pendingAction.current = fn;
        setReauthOpen(true);
        return;
      }
      toast.error(formatApiError(err));
    }
  }

  async function upload(category, file) {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      toast.error("File exceeds 10 MB cap");
      return;
    }
    setUploading(category);
    await runWithReauth(async () => {
      const body = new FormData();
      body.append("file", file);
      body.append("category", category);
      await api.post(`/patients/${patientId}/documents`, body, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success("Document uploaded");
      load();
    });
    setUploading(null);
    if (inputsRef.current[category]) inputsRef.current[category].value = "";
  }

  async function download(doc) {
    try {
      const resp = await api.get(
        `/patients/${patientId}/documents/${doc.id}/download`,
        { responseType: "blob" }
      );
      const url = URL.createObjectURL(resp.data);
      window.open(url, "_blank", "noopener,noreferrer");
      // Revoke later — browsers typically keep the blob alive until reload.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      toast.error(formatApiError(err));
    }
  }

  async function remove(doc) {
    await runWithReauth(async () => {
      await api.delete(`/patients/${patientId}/documents/${doc.id}`);
      toast.success("Document removed");
      setPendingDelete(null);
      load();
    });
  }

  function onReauthConfirmed() {
    setReauthOpen(false);
    const fn = pendingAction.current;
    pendingAction.current = null;
    if (fn) fn().catch((err) => toast.error(formatApiError(err)));
  }

  const docsByCategory = (documents || []).reduce((acc, d) => {
    (acc[d.category] = acc[d.category] || []).push(d);
    return acc;
  }, {});

  return (
    <section
      data-testid="patient-documents-card"
      className="rounded-sm border border-subtle bg-card p-6"
    >
      <div className="mb-4 border-b border-subtle pb-2">
        <h3 className="font-['Outfit'] text-lg font-medium text-strong">
          Documents &amp; attachments
        </h3>
        <p className="mt-0.5 text-xs text-muted-strong">
          Insurance cards, IDs, referral letters &amp; imaging reports. All uploads are
          encrypted at rest and every access is audited.
        </p>
      </div>

      {documents === null ? (
        <div className="py-4 text-sm text-muted-strong">Loading…</div>
      ) : (
        <div className="space-y-5">
          {CATEGORIES.map(({ value, label, icon: Icon }) => {
            const items = docsByCategory[value] || [];
            const isInsurance = value.startsWith("insurance_card");
            if (!canEdit && items.length === 0) return null;
            return (
              <div key={value} data-testid={`docs-category-${value}`} className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium text-strong">
                    <Icon className="h-4 w-4 text-sage" />
                    <span>{label}</span>
                    <span className="text-xs text-soft">({items.length})</span>
                  </div>
                  {canEdit && (
                    <>
                      <input
                        ref={(el) => { inputsRef.current[value] = el; }}
                        type="file"
                        accept={isInsurance ? "image/*" : "image/*,application/pdf"}
                        className="hidden"
                        data-testid={`docs-upload-${value}`}
                        onChange={(e) => upload(value, e.target.files?.[0])}
                      />
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={uploading === value}
                        onClick={() => inputsRef.current[value]?.click()}
                        data-testid={`docs-upload-btn-${value}`}
                        className="rounded-sm"
                      >
                        {uploading === value ? (
                          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        ) : isInsurance ? (
                          <ImagePlus className="mr-2 h-3.5 w-3.5" />
                        ) : (
                          <Upload className="mr-2 h-3.5 w-3.5" />
                        )}
                        {items.length ? "Add another" : "Upload"}
                      </Button>
                    </>
                  )}
                </div>
                {items.length > 0 && (
                  <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {items.map((doc) => (
                      <li
                        key={doc.id}
                        data-testid={`docs-item-${doc.id}`}
                        className="flex items-center justify-between gap-3 rounded-sm border border-subtle surface-app px-3 py-2 text-sm"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-strong">{doc.filename}</div>
                          <div className="text-xs text-muted-strong">
                            {doc.content_type} · {Math.round((doc.size || 0) / 1024)} KB ·{" "}
                            {new Date(doc.uploaded_at).toLocaleString()}
                          </div>
                        </div>
                        <div className="flex items-center gap-1">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => download(doc)}
                            data-testid={`docs-download-${doc.id}`}
                            className="h-7 px-2 text-sage-deep hover:surface-sage"
                          >
                            <Download className="h-3.5 w-3.5" />
                          </Button>
                          {canEdit && (
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              onClick={() => setPendingDelete(doc)}
                              data-testid={`docs-delete-${doc.id}`}
                              className="h-7 px-2 text-danger hover:surface-danger-soft"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      {pendingDelete && (
        <div
          data-testid="docs-delete-confirm"
          className="mt-5 flex items-start justify-between gap-4 rounded-sm border border-[#E7C4B9] surface-danger-soft px-4 py-3 text-sm text-danger-strong"
        >
          <span>
            Remove <strong>{pendingDelete.filename}</strong>? This soft-deletes the
            record; the raw file is retained for audit.
          </span>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setPendingDelete(null)}
              className="h-7 text-danger-strong hover:bg-[#F5DFD7]"
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => remove(pendingDelete)}
              data-testid="docs-delete-confirm-btn"
              className="h-7 rounded-sm bg-danger hover:bg-[#A85540]"
            >
              Remove
            </Button>
          </div>
        </div>
      )}

      <ReauthDialog
        open={reauthOpen}
        title="Confirm it's you"
        description="HIPAA policy requires step-up re-authentication before uploading or removing patient documents."
        onClose={() => {
          setReauthOpen(false);
          pendingAction.current = null;
        }}
        onConfirmed={onReauthConfirmed}
      />
    </section>
  );
}
