import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { useProviders } from "../../contexts/ProvidersContext";

const ALL_VALUE = "__all__";

/**
 * Small provider filter dropdown for Scheduling. Reads from
 * `ProvidersContext` (no per-mount fetch) and emits a callback with either
 * the provider id or `null` for "all".
 *
 * For the `doctor` role the endpoint returns every provider, but the
 * scheduling backend already auto-filters to the caller unless an explicit
 * `provider_id` is passed — so letting a doctor pick a peer simply shows
 * that peer's schedule, which is the desired behaviour when a doctor
 * covers for someone else.
 */
export default function ProviderFilter({ value, onChange }) {
  const { providers, loading } = useProviders();

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
