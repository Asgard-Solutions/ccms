import { useEffect, useState } from "react";
import { ShieldCheck, KeyRound, Server, AlertTriangle, CircleDot, Lock } from "lucide-react";
import { api } from "../api/client";
import { formatDateTime } from "../utils/time";
import { Skeleton } from "../components/ui/skeleton";

function Flag({ ok, label, testid, warn = false }) {
  const cls = ok
    ? "bg-primary/10 text-primary"
    : warn
    ? "bg-warning-soft text-warning"
    : "bg-destructive-soft text-destructive";
  const dot = ok
    ? "bg-primary"
    : warn
    ? "bg-warning"
    : "bg-destructive";
  return (
    <div
      data-testid={testid}
      className="flex items-center justify-between gap-3 rounded-sm border border-border bg-card px-4 py-3 text-sm"
    >
      <span className="text-foreground">{label}</span>
      <span
        className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${cls}`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
        {ok ? "OK" : warn ? "Review" : "Missing"}
      </span>
    </div>
  );
}

function Row({ label, value, testid, mono = false }) {
  return (
    <div
      data-testid={testid}
      className="flex items-center justify-between gap-3 rounded-sm border border-border bg-card px-4 py-3 text-sm"
    >
      <span className="text-muted-foreground">{label}</span>
      <span className={`${mono ? "font-mono text-xs" : ""} text-foreground`}>{value}</span>
    </div>
  );
}

export default function SecurityConfig() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/compliance/security-config");
        setData(data);
      } catch (e) {
        setError(e?.response?.data?.detail || "Failed to load security configuration");
      }
    })();
  }, []);

  if (error) {
    return (
      <div
        data-testid="security-config-error"
        className="rounded-sm border border-destructive-soft bg-destructive-soft p-4 text-sm text-destructive"
      >
        {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div data-testid="security-config-loading" className="space-y-4">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  return (
    <div data-testid="security-config-page" className="space-y-10 animate-in fade-in duration-300">
      <header>
        <span className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Compliance
        </span>
        <h1 className="mt-2 font-display text-4xl font-medium tracking-tight">
          Security configuration
        </h1>
        <p className="mt-3 max-w-3xl text-sm text-muted-foreground">{data.disclaimer}</p>
        <div
          data-testid="security-config-env"
          className="mt-4 flex items-center gap-4 text-xs"
        >
          <span className="inline-flex items-center gap-1.5 rounded-sm bg-muted px-2 py-1 font-mono uppercase tracking-wider text-foreground">
            <CircleDot className="h-3 w-3" /> APP_ENV = {data.app_env}
          </span>
          <span
            className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-1 font-semibold uppercase tracking-wider ${
              data.production_ready
                ? "bg-primary/10 text-primary"
                : "bg-warning-soft text-warning"
            }`}
          >
            {data.production_ready ? "production-ready" : "pre-production"}
          </span>
          <span className="text-muted-foreground">
            Generated {formatDateTime(data.generated_at)}
          </span>
        </div>
      </header>

      {data.production_gaps.length > 0 && (
        <section
          data-testid="security-config-gaps"
          className="rounded-sm border border-border bg-warning-soft p-4 text-sm text-warning"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4" />
            <span className="font-display text-base font-medium">
              Gaps for production go-live
            </span>
          </div>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs">
            {data.production_gaps.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-4">
        <h2 className="flex items-center gap-2 font-display text-lg font-medium">
          <Server className="h-4 w-4" /> Required configuration
        </h2>
        <div className="grid gap-3 md:grid-cols-2">
          {Object.entries(data.required_config).map(([k, v]) => (
            <Flag key={k} ok={v} label={k} testid={`req-${k.toLowerCase()}`} />
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="flex items-center gap-2 font-display text-lg font-medium">
          <Server className="h-4 w-4" /> Recommended configuration
        </h2>
        <div className="grid gap-3 md:grid-cols-2">
          {Object.entries(data.recommended_config).map(([k, v]) => (
            <Flag
              key={k}
              ok={v}
              warn
              label={k}
              testid={`rec-${k.toLowerCase()}`}
            />
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="flex items-center gap-2 font-display text-lg font-medium">
          <KeyRound className="h-4 w-4" /> Secret strength
        </h2>
        <div className="grid gap-3 md:grid-cols-2">
          <Row
            label="JWT_SECRET length"
            value={data.secret_strength.jwt_secret_length}
            testid="secret-jwt-length"
          />
          <Row
            label="JWT_SECRET (masked)"
            value={data.secret_strength.jwt_secret_masked || "—"}
            testid="secret-jwt-masked"
            mono
          />
          <Row
            label="DATA_ENCRYPTION_KEY length"
            value={data.secret_strength.data_encryption_key_length}
            testid="secret-dek-length"
          />
          <Row
            label="DATA_ENCRYPTION_KEY (masked)"
            value={data.secret_strength.data_encryption_key_masked || "—"}
            testid="secret-dek-masked"
            mono
          />
        </div>
        {data.weak_secrets.length > 0 && (
          <div className="rounded-sm border border-destructive-soft bg-destructive-soft p-3 text-xs text-destructive">
            Weak secrets detected: {data.weak_secrets.join(", ")} (minimum 32 chars)
          </div>
        )}
      </section>

      <section className="space-y-4">
        <h2 className="flex items-center gap-2 font-display text-lg font-medium">
          <Lock className="h-4 w-4" /> Field-level encryption
        </h2>
        <div className="grid gap-3 md:grid-cols-2">
          <Row label="Provider" value={data.encryption.provider} testid="enc-provider" mono />
          <Flag
            ok={data.encryption.enabled}
            label="Encryption enabled"
            testid="enc-enabled"
          />
          <Row
            label="Active key version"
            value={data.encryption.active_version}
            testid="enc-active-version"
            mono
          />
          <Row
            label="Extra key versions (for rotation)"
            value={
              data.encryption.extra_versions.length
                ? data.encryption.extra_versions.join(", ")
                : "none"
            }
            testid="enc-extra-versions"
            mono
          />
        </div>
        <div className="rounded-sm border border-border bg-background p-3 text-xs text-muted-foreground">
          <div className="font-semibold uppercase tracking-[0.15em] text-foreground">
            Encrypted at rest
          </div>
          <div className="mt-1">
            <span className="font-semibold">patients:</span>{" "}
            {data.encryption.patient_encrypted_fields.join(", ")}
          </div>
          <div>
            <span className="font-semibold">medical_records:</span>{" "}
            {data.encryption.medical_record_encrypted_fields.join(", ")}
          </div>
          <div className="mt-2 italic">
            password_hash is bcrypt; reset-token is SHA-256 hashed; MFA secret
            is stored as plaintext today (KMS-wrap flagged in the backlog).
          </div>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="flex items-center gap-2 font-display text-lg font-medium">
          <ShieldCheck className="h-4 w-4" /> Security features
        </h2>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {Object.entries(data.features).map(([k, v]) => (
            <Flag key={k} ok={v} warn={!v} label={k} testid={`feature-${k}`} />
          ))}
        </div>
      </section>

      <section className="space-y-3 text-xs text-muted-foreground">
        <h2 className="font-display text-lg font-medium text-foreground">
          Reference
        </h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <code>/app/memory/DATA_PROTECTION_AND_KEYS.md</code> — full data-protection inventory and KMS migration plan
          </li>
          <li>
            <code>/app/memory/ACCESS_CONTROL_AND_AUDIT.md</code> — identity, session, audit evidence
          </li>
          <li>
            <code>/app/memory/COMPLIANCE_BASELINE.md</code> — SOC 2 / CCPA / ISO 27001 control mapping
          </li>
        </ul>
      </section>
    </div>
  );
}
