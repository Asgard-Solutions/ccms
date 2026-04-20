import { useEffect, useState } from "react";
import { api } from "../../api/client";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

const ALL_VALUE = "__all__";

/**
 * Small provider filter dropdown for Scheduling. Lazy-fetches
 * `/auth/providers` the first time it mounts, shows "All providers" +
 * one item per provider, and emits a callback with either the provider id
 * or `null` for "all".
 *
 * For the `doctor` role the endpoint returns every provider, but the
 * scheduling backend already auto-filters to the caller unless an explicit
 * `provider_id` is passed — so letting a doctor pick a peer simply shows
 * that peer's schedule, which is the desired behaviour when a doctor
 * covers for someone else.
 */
export default function ProviderFilter({ value, onChange }) {
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.get("/auth/providers");
        setProviders(r.data || []);
      } catch {
        setProviders([]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (!loading && providers.length === 0) return null;

  return (
    <Select
      value={value || ALL_VALUE}
      onValueChange={(v) => onChange?.(v === ALL_VALUE ? null : v)}
    >
      <SelectTrigger
        data-testid="scheduling-provider-filter"
        className="h-10 w-48 rounded-sm"
      >
        <SelectValue placeholder="All providers" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_VALUE} data-testid="scheduling-provider-filter-all">
          All providers
        </SelectItem>
        {providers.map((p) => (
          <SelectItem
            key={p.id}
            value={p.id}
            data-testid={`scheduling-provider-filter-${p.id}`}
          >
            {p.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
