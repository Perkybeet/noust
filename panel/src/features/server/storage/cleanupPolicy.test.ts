import { describe, expect, it } from "vitest";

import { EMPTY_DRAFT, draftOf, policyBody, policyProblems, runsDocker, scheduleOfPolicy } from "./cleanupPolicy";

const DOCKER = ["docker-build-cache", "docker-dangling-images"];

describe("the cleanup policy form", () => {
  it("reads a stored policy back as the time it means, never as an expression to decode", () => {
    expect(scheduleOfPolicy("daily")).toMatchObject({ frequency: "daily", time: "02:00" });
    expect(scheduleOfPolicy("*-*-* 04:30:00")).toMatchObject({ frequency: "daily", time: "04:30" });
    // systemd's own words for midnight on Monday and on the first of the month.
    expect(scheduleOfPolicy("weekly")).toMatchObject({ frequency: "weekly", weekday: "Mon", time: "00:00" });
    expect(scheduleOfPolicy("monthly")).toMatchObject({ frequency: "monthly", day: "1", time: "00:00" });
    expect(scheduleOfPolicy("Mon..Fri *-*-* 03:30:00")).toMatchObject({ frequency: "custom", custom: "Mon..Fri *-*-* 03:30:00" });
  });

  it("round-trips a policy through the form", () => {
    const policy = { enabled: true, schedule: "*-*-* 05:15:00", threshold_percent: 70, actions: ["journal", "releases"], journal_mb: 300, allow_docker: false };
    expect(policyBody(draftOf(policy), DOCKER)).toEqual(policy);
  });

  it("sends the journal size only while the journal is cleaned, and Docker consent only while Docker is", () => {
    const draft = { ...EMPTY_DRAFT, actions: ["pkg-cache"], journal: "300", allowDocker: true };
    expect(policyBody(draft, DOCKER)).toMatchObject({ journal_mb: null, allow_docker: false });
    const docker = { ...draft, actions: ["pkg-cache", "docker-build-cache"] };
    expect(runsDocker(docker, DOCKER)).toBe(true);
    expect(policyBody(docker, DOCKER)).toMatchObject({ allow_docker: true });
  });

  it("names what is wrong, field by field", () => {
    expect(policyProblems(EMPTY_DRAFT)).toEqual({});
    expect(policyProblems({ ...EMPTY_DRAFT, threshold: "100" })).toEqual({ threshold: true });
    expect(policyProblems({ ...EMPTY_DRAFT, threshold: "8o" })).toEqual({ threshold: true });
    expect(policyProblems({ ...EMPTY_DRAFT, threshold: "0" })).toEqual({});
    expect(policyProblems({ ...EMPTY_DRAFT, actions: [] })).toEqual({ actions: true });
    expect(policyProblems({ ...EMPTY_DRAFT, journal: "4" })).toEqual({ journal: true });
    expect(policyProblems({ ...EMPTY_DRAFT, journal: "512" })).toEqual({});
    expect(policyProblems({ ...EMPTY_DRAFT, schedule: { ...EMPTY_DRAFT.schedule, time: "25:00" } })).toEqual({ time: true });
    expect(policyProblems({ ...EMPTY_DRAFT, schedule: { ...EMPTY_DRAFT.schedule, frequency: "custom", custom: " " } })).toEqual({ schedule: true });
  });
});
