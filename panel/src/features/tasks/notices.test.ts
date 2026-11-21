import { afterEach, describe, expect, it, vi } from "vitest";

import { toast } from "../../components/ui/toast";
import { taskOfJob } from "./model";
import type { Task } from "./model";
import { sayEnding } from "./notices";
import { job } from "./taskFixtures";

vi.mock("../../components/ui/toast", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const NOW = Date.parse("2026-11-04T12:00:00Z");
const failed = (): Task => ({ ...taskOfJob(job({ actor: "ana" }), null, NOW), state: "failed", error: "npm exited with 1" });
const go = (): void => undefined;

afterEach(() => {
  vi.clearAllMocks();
});

describe("what a toast says of a task that ended", () => {
  it("tells a person who went to another page that they did, and where to see it", () => {
    sayEnding(failed(), go, true);
    expect(toast.error).toHaveBeenCalledTimes(1);
    const [, options] = vi.mocked(toast.error).mock.calls[0] ?? [];
    expect(options).toMatchObject({ description: "You started it and left that page.", action: { label: "See" }, detail: "npm exited with 1" });
  });

  // The failure of a job on the page of its application is told by a toast although the person is looking at that page: it said they
  // had left it (round 2 of the console's users). Said of someone who stayed, the toast keeps the way to see it and the failure's words.
  it("does not tell a person who is still on the page that they left it", () => {
    sayEnding(failed(), go, false);
    expect(toast.error).toHaveBeenCalledTimes(1);
    const [words, options] = vi.mocked(toast.error).mock.calls[0] ?? [];
    expect(words).toContain("failed");
    expect(options).not.toHaveProperty("description");
    expect(options).toMatchObject({ action: { label: "See" }, detail: "npm exited with 1" });
  });
});
