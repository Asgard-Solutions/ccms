/**
 * Frontend feature-flag registry.
 *
 * Resolution order (highest wins):
 *   1. localStorage override  →  ccms.flags.<key>  ("on" | "off")
 *   2. Runtime env override   →  process.env.REACT_APP_<UPPER_KEY>
 *   3. Hard-coded fallback in FLAG_DEFAULTS
 *
 * Env overrides let ops flip a flag for staging / production / rollback
 * without shipping a code change. localStorage is a per-user opt-out
 * exposed through Settings so a clinician can go back to the legacy
 * layout if the new one blocks them.
 *
 * Keep the surface tiny — no React context, no async, no publisher.
 * Consumers just call `useFeatureFlag(key)` inside a component.
 */
import { useCallback, useEffect, useState } from "react";

const STORAGE_PREFIX = "ccms.flags.";

// Fallback used when neither env nor storage has an opinion.
const FLAG_DEFAULTS = {
  clinicalRedesign: "on",
};

// Explicit env-var mapping so grep/CI can find the strings.
const ENV_VAR_MAP = {
  clinicalRedesign: "REACT_APP_CLINICAL_REDESIGN",
};

function normalise(raw) {
  if (raw == null) return null;
  const v = String(raw).trim().toLowerCase();
  if (["on", "true", "1", "enabled"].includes(v)) return "on";
  if (["off", "false", "0", "disabled"].includes(v)) return "off";
  return null;
}

function readStorage(key) {
  if (typeof window === "undefined") return null;
  try {
    return normalise(window.localStorage.getItem(STORAGE_PREFIX + key));
  } catch {
    return null;
  }
}

function readEnv(key) {
  const envKey = ENV_VAR_MAP[key];
  if (!envKey) return null;
  return normalise(process.env[envKey]);
}

export function getFlag(key) {
  const storage = readStorage(key);
  if (storage) return storage;
  const env = readEnv(key);
  if (env) return env;
  return FLAG_DEFAULTS[key] || "off";
}

export function isFlagOn(key) {
  return getFlag(key) === "on";
}

export function setFlagOverride(key, value) {
  if (typeof window === "undefined") return;
  try {
    if (value == null) {
      window.localStorage.removeItem(STORAGE_PREFIX + key);
    } else {
      window.localStorage.setItem(STORAGE_PREFIX + key, value);
    }
    window.dispatchEvent(new CustomEvent("ccms-flag-change", { detail: { key, value } }));
  } catch {
    /* ignore */
  }
}

export function useFeatureFlag(key) {
  const [value, setValue] = useState(() => getFlag(key));

  useEffect(() => {
    const onChange = (e) => {
      if (!e.detail || e.detail.key === key) setValue(getFlag(key));
    };
    const onStorage = (e) => {
      if (e.key === STORAGE_PREFIX + key) setValue(getFlag(key));
    };
    window.addEventListener("ccms-flag-change", onChange);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("ccms-flag-change", onChange);
      window.removeEventListener("storage", onStorage);
    };
  }, [key]);

  const set = useCallback((next) => setFlagOverride(key, next), [key]);
  return [value === "on", value, set];
}
