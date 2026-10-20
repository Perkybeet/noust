/**
 * Ratchet `concept-doors` (docs/DESIGN.md NV-8): a concept has one canonical page, and anywhere
 * else it is a link, the same component filtered to that scope, or a read-only summary that says
 * where it is managed. The concepts that can still be changed from more than one place are listed
 * in concept-doors.json with the places and the reason, and the list may only get shorter:
 *
 * - a concept added fails: give it one home, or it is not a new door;
 * - a concept removed fails until `baseline` goes down with it, so the progress cannot be lost.
 */

import { describe, expect, it } from "vitest";

import doors from "./concept-doors.json";

interface Concept {
  id: string;
  doors: string[];
  reason: string;
}

const BASELINE_DOORS = doors as { baseline: number; concepts: Concept[] };

describe("the concepts with more than one editable home", () => {
  it("are no more than the baseline allows", () => {
    expect(BASELINE_DOORS.concepts.length, "A concept gained a second editable home: give it one page and link to it (NV-8)").toBeLessThanOrEqual(BASELINE_DOORS.baseline);
  });

  it("lock in every one that was given a single home: lower `baseline` in styles/concept-doors.json", () => {
    expect(BASELINE_DOORS.concepts.length).toBe(BASELINE_DOORS.baseline);
  });

  it("name at least two doors each, and say why it is still so", () => {
    for (const concept of BASELINE_DOORS.concepts) {
      expect(concept.doors.length, `${concept.id} lists fewer than two places`).toBeGreaterThanOrEqual(2);
      expect(new Set(concept.doors).size, `${concept.id} lists a place twice`).toBe(concept.doors.length);
      expect(concept.reason.trim().length, `${concept.id} has no reason`).toBeGreaterThan(30);
    }
  });

  it("do not repeat a concept", () => {
    const ids = BASELINE_DOORS.concepts.map((concept) => concept.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z]+(?:-[a-z]+)*$/);
  });
});
