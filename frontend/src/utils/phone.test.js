// Node-test (native runner) unit tests for the US phone utilities.
// Run:  `cd frontend && node --test src/utils/phone.test.js`
import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizePhone,
  isValidPhone,
  formatPhoneDisplay,
  searchNormalize,
  formatAsTyped,
} from "./phone.js";

test("normalizePhone accepts common US shapes", () => {
  assert.equal(normalizePhone("6155551212"), "6155551212");
  assert.equal(normalizePhone("(615) 555-1212"), "6155551212");
  assert.equal(normalizePhone("615-555-1212"), "6155551212");
  assert.equal(normalizePhone("615.555.1212"), "6155551212");
  assert.equal(normalizePhone("+1 (615) 555 1212"), "6155551212");
  assert.equal(normalizePhone("1-615-555-1212"), "6155551212");
  assert.equal(normalizePhone("   6155551212   "), "6155551212");
});

test("normalizePhone returns null for empty, non-US and partial inputs", () => {
  assert.equal(normalizePhone(""), null);
  assert.equal(normalizePhone("   "), null);
  assert.equal(normalizePhone(null), null);
  assert.equal(normalizePhone(undefined), null);
  // 7-digit legacy
  assert.equal(normalizePhone("555-1212"), null);
  // 11-digit with non-1 leading
  assert.equal(normalizePhone("21555512345"), null);
});

test("isValidPhone treats empty as valid (optional field)", () => {
  assert.equal(isValidPhone(""), true);
  assert.equal(isValidPhone("  "), true);
  assert.equal(isValidPhone(null), true);
});

test("isValidPhone enforces 10-digit when non-empty", () => {
  assert.equal(isValidPhone("6155551212"), true);
  assert.equal(isValidPhone("(615) 555-1212"), true);
  assert.equal(isValidPhone("555-1212"), false);
  assert.equal(isValidPhone("61555512345"), false);
});

test("formatPhoneDisplay renders (XXX) XXX-XXXX for 10-digit, echoes legacy", () => {
  assert.equal(formatPhoneDisplay("6155551212"), "(615) 555-1212");
  assert.equal(formatPhoneDisplay("(615) 555-1212"), "(615) 555-1212");
  assert.equal(formatPhoneDisplay("+1-615-555-1212"), "(615) 555-1212");
  // Legacy seed-format with only 7 body digits → echoed unchanged.
  assert.equal(formatPhoneDisplay("+1-555-0102"), "+1-555-0102");
  assert.equal(formatPhoneDisplay("555-1212"), "555-1212");
  assert.equal(formatPhoneDisplay(""), "");
  assert.equal(formatPhoneDisplay(null), "");
});

test("searchNormalize strips non-digits without length enforcement", () => {
  assert.equal(searchNormalize("(615) 555-1212"), "6155551212");
  assert.equal(searchNormalize("615"), "615");
  assert.equal(searchNormalize(""), "");
  assert.equal(searchNormalize(null), "");
});

test("formatAsTyped progressively formats", () => {
  assert.equal(formatAsTyped(""), "");
  assert.equal(formatAsTyped("6"), "(6");
  assert.equal(formatAsTyped("61"), "(61");
  assert.equal(formatAsTyped("615"), "(615");
  assert.equal(formatAsTyped("6155"), "(615) 5");
  assert.equal(formatAsTyped("615555"), "(615) 555");
  assert.equal(formatAsTyped("6155551"), "(615) 555-1");
  assert.equal(formatAsTyped("6155551212"), "(615) 555-1212");
  // Extra digits past 10 are truncated.
  assert.equal(formatAsTyped("61555512129999"), "(615) 555-1212");
  // Non-digit characters are ignored during formatting.
  assert.equal(formatAsTyped("(615) 555-1212"), "(615) 555-1212");
});
