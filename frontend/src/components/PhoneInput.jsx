import React from "react";
import { Input } from "./ui/input";
import { formatAsTyped } from "../utils/phone";

/**
 * Controlled US-phone input. Users type digits; we live-format the
 * value into `(XXX) XXX-XXXX`. The consumer receives the RAW input
 * value (formatted string) via `onChange` — it should then pass it
 * through `normalizePhone` at submit time so storage stays 10-digit.
 *
 * Kept minimal so it can be dropped into existing forms without
 * rewiring. Preserves any passed `data-testid` / className.
 */
export const PhoneInput = React.forwardRef(function PhoneInput(
  { value, onChange, onBlur, inputMode = "tel", autoComplete = "tel-national", ...rest },
  ref,
) {
  const handleChange = (e) => {
    const pretty = formatAsTyped(e.target.value);
    if (onChange) {
      // Emit a synthetic event so existing `(e) => update(...)` handlers
      // keep working unchanged.
      onChange({ ...e, target: { ...e.target, value: pretty } });
    }
  };
  return (
    <Input
      ref={ref}
      type="tel"
      inputMode={inputMode}
      autoComplete={autoComplete}
      value={value || ""}
      onChange={handleChange}
      onBlur={onBlur}
      placeholder="(555) 123-4567"
      maxLength={14}
      {...rest}
    />
  );
});
