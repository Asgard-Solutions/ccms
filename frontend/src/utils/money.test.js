/**
 * Unit tests for money utilities. Pure logic — runs under the default
 * react-scripts (jest) runner.
 */
import {
  clampCents,
  formatCents,
  parseDollarsToCents,
  sumAmountCents,
} from "./money";

describe("formatCents", () => {
  test("formats zero / positive / large", () => {
    expect(formatCents(0)).toBe("$0.00");
    expect(formatCents(550)).toBe("$5.50");
    expect(formatCents(123456)).toBe("$1,234.56");
  });
  test("returns em-dash for null / NaN", () => {
    expect(formatCents(null)).toBe("—");
    expect(formatCents(undefined)).toBe("—");
    expect(formatCents("not-a-number")).toBe("—");
  });
});

describe("parseDollarsToCents", () => {
  test("parses plain / decimal / prefixed / comma'd values", () => {
    expect(parseDollarsToCents("12")).toBe(1200);
    expect(parseDollarsToCents("12.5")).toBe(1250);
    expect(parseDollarsToCents("12.50")).toBe(1250);
    expect(parseDollarsToCents("$12.50")).toBe(1250);
    expect(parseDollarsToCents("1,200.00")).toBe(120000);
  });
  test("rejects invalid", () => {
    expect(parseDollarsToCents("")).toBeNull();
    expect(parseDollarsToCents("abc")).toBeNull();
    expect(parseDollarsToCents("12.a")).toBeNull();
    expect(parseDollarsToCents("12.12345")).toBeNull(); // >4 dp rejected
  });
});

describe("clampCents / sumAmountCents", () => {
  test("clamps into range", () => {
    expect(clampCents(50, { min: 0, max: 100 })).toBe(50);
    expect(clampCents(-5, { min: 0, max: 100 })).toBe(0);
    expect(clampCents(500, { min: 0, max: 100 })).toBe(100);
    expect(clampCents(null, { min: 10, max: 100 })).toBe(10);
  });
  test("sums allocations", () => {
    expect(sumAmountCents([])).toBe(0);
    expect(sumAmountCents([{ amount_cents: 100 }, { amount_cents: 250 }])).toBe(350);
    expect(sumAmountCents([{ amount_cents: "100" }, { amount_cents: null }])).toBe(100);
  });
});
