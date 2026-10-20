/**
 * Where an item that needs attention is dealt with, and the one thing to press for it. The inbox
 * gives each item a body that opens that screen and one button that does the first thing there:
 * "the button of the row is the action that fixes it or leads to the screen that fixes it, and a
 * click on the body opens the same screen" (2.7 of the navigation design). Pure, so each rule is
 * tested without a page; the words of each button are the component's (`AttentionActionButton`).
 */

import type { OverviewAttentionItem } from "../../api/queries/overview";

/** A screen to open: the path of the router with its parameters and search. */
export interface AttentionTarget {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string | number | boolean>;
}

/** A button that opens the screen where the item is dealt with. `name` is what it is about, for its accessible name. */
export interface OpenAction {
  mode: "open";
  kind: "viewLog" | "diagnose" | "renew" | "openService" | "openFinding" | "openProbes" | "review" | "openUpdates" | "reviewRecipe";
  name: string;
  target: AttentionTarget;
}

/** A button that takes a firing alert, or hands it back: it stops the reminders and never resolves it. */
export interface AlertAction {
  mode: "alert";
  kind: "acknowledge" | "reopen";
  name: string;
  rule: string;
  subject: string;
}

/** What the button of a row does. */
export type AttentionAction = OpenAction | AlertAction;

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** The screen an item is about, which a click on its body opens. */
export function attentionTarget(item: OverviewAttentionItem): AttentionTarget | null {
  const subject = item.subject;
  const domain = text(subject["domain"]);
  const name = text(subject["name"]);
  const reason = item.reasons[0];
  switch (subject["kind"]) {
    case "app": {
      if (domain === null) return null;
      // A deploy that failed or was rolled back is the likeliest cause of whatever else is wrong with the
      // application, and its log has the system's words: that is where the item leads, ahead of the state it left.
      const deployment = item.reasons.find((candidate) => candidate.kind === "deploy" && candidate.deployment_id !== null && candidate.deployment_id !== undefined)?.deployment_id ?? null;
      if (deployment !== null) return { to: "/apps/$domain/deployments/$id", params: { domain, id: String(deployment) } };
      if (reason?.kind === "certificate") return { to: "/apps/$domain/domains", params: { domain } };
      return { to: "/apps/$domain/diagnose", params: { domain } };
    }
    case "certificate":
      return domain === null ? null : { to: "/domains", search: { q: domain } };
    case "unit":
      return name === null ? { to: "/server" } : { to: "/server/services/$name", params: { name } };
    case "monitor":
      return { to: "/server" };
    case "alert":
      return { to: "/settings/alerts" };
    case "probe":
      return { to: "/settings/probes" };
    case "approval":
      return { to: "/settings/approvals" };
    case "noust":
      return { to: "/settings/about" };
    case "recipe":
      return { to: "/catalog/installed" };
    default:
      return null;
  }
}

/** The one button of an item's row, or none when the console has nothing to offer for it. */
export function attentionAction(item: OverviewAttentionItem): AttentionAction | null {
  const target = attentionTarget(item);
  const reason = item.reasons[0];
  if (target === null || reason === undefined) return null;
  const name = text(item.subject["domain"]) ?? item.title;
  switch (item.subject["kind"]) {
    case "app":
      if (target.to.endsWith("/deployments/$id")) return { mode: "open", kind: "viewLog", name, target };
      return { mode: "open", kind: reason.kind === "certificate" ? "renew" : "diagnose", name, target };
    case "certificate":
      return { mode: "open", kind: "renew", name, target };
    case "unit":
      return { mode: "open", kind: "openService", name, target };
    case "monitor":
      return { mode: "open", kind: "openFinding", name, target };
    case "alert": {
      const rule = text(item.subject["rule"]);
      const subject = typeof item.subject["subject"] === "string" ? item.subject["subject"] : "";
      if (rule === null) return null;
      return { mode: "alert", kind: reason.code === "alert_taken" ? "reopen" : "acknowledge", name: subject === "" ? rule : subject, rule, subject };
    }
    case "probe":
      return { mode: "open", kind: "openProbes", name, target };
    case "approval":
      return { mode: "open", kind: "review", name, target };
    case "noust":
      return { mode: "open", kind: "openUpdates", name, target };
    case "recipe":
      return { mode: "open", kind: "reviewRecipe", name, target };
    default:
      return null;
  }
}
