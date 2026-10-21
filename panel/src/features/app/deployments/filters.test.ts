import { describe, expect, it } from "vitest";

import type { Deployment } from "../../apps/data";
import { branchesOf, filterDeployments, isFiltered, stateOf, validateDeploymentsSearch } from "./filters";

function deploy(id: number, extra: Partial<Deployment> = {}): Deployment {
  return {
    id,
    domain: "shop.example.com",
    status: "success",
    triggered_by: "webhook",
    git_commit: `c0ffee${String(id)}`,
    git_branch: "main",
    commit_message: `Change ${String(id)}`,
    has_log: true,
    ...extra,
  } as Deployment;
}

const HISTORY = [
  deploy(5, { status: "running" }),
  deploy(4, { status: "failed", triggered_by: "panel", commit_message: "Bump deps" }),
  deploy(3, { status: "failed", cancelled: true }),
  deploy(2, { git_branch: "feat/cart", triggered_by: "cli", commit_message: "Add gift cards" }),
  deploy(1, { status: "rolled_back" }),
];

describe("the deployments list's search", () => {
  it("keeps what it understands and drops the rest", () => {
    expect(validateDeploymentsSearch({ q: "  cart ", state: "failed", branch: "main", by: "cli" })).toEqual({ q: "cart", state: "failed", branch: "main", by: "cli" });
    expect(validateDeploymentsSearch({ state: "nonsense", by: "robot", q: 3, branch: "" })).toEqual({});
  });

  it("knows whether anything narrows the list", () => {
    expect(isFiltered({})).toBe(false);
    expect(isFiltered({ branch: "main" })).toBe(true);
  });
});

describe("a deployment's state for the filter", () => {
  it("tells the version that serves from the ones that merely succeeded", () => {
    expect(stateOf(deploy(2), 2)).toBe("live");
    expect(stateOf(deploy(1), 2)).toBe("succeeded");
  });

  it("reads running, failed, rolled back and cancelled by what they are", () => {
    expect(HISTORY.map((deployment) => stateOf(deployment, 2))).toEqual(["running", "failed", "cancelled", "live", "rolled_back"]);
  });
});

describe("filtering the history", () => {
  it("matches the text against the message, the commit, the branch and the number", () => {
    expect(filterDeployments(HISTORY, { q: "gift" }, 2).map((d) => d.id)).toEqual([2]);
    expect(filterDeployments(HISTORY, { q: "c0ffee4" }, 2).map((d) => d.id)).toEqual([4]);
    expect(filterDeployments(HISTORY, { q: "FEAT/" }, 2).map((d) => d.id)).toEqual([2]);
    expect(filterDeployments(HISTORY, { q: "#3" }, 2).map((d) => d.id)).toEqual([3]);
  });

  it("narrows by state, by branch and by who started it, together", () => {
    expect(filterDeployments(HISTORY, { state: "failed" }, 2).map((d) => d.id)).toEqual([4]);
    expect(filterDeployments(HISTORY, { branch: "main" }, 2).map((d) => d.id)).toEqual([5, 4, 3, 1]);
    expect(filterDeployments(HISTORY, { by: "panel", state: "failed" }, 2).map((d) => d.id)).toEqual([4]);
    expect(filterDeployments(HISTORY, { by: "cli", state: "failed" }, 2)).toEqual([]);
  });

  it("offers the branches the history has seen", () => {
    expect(branchesOf(HISTORY)).toEqual(["main", "feat/cart"]);
  });
});
