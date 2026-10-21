import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { jobKeys } from "../../api/queries/jobs";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { describeError } from "../../lib/errors";
import { heldOf } from "../../lib/held";
import { BULK_ACTIONS, counts, planBulk } from "./bulk";
import type { BulkAction, PlanEntry, SkipReason } from "./bulk";
import type { AppInfo } from "./data";
import { isNothingNew } from "./useAppActions";

/** What became of one application, as the dialog says it. */
type Outcome =
  | { kind: "pending" }
  | { kind: "working" }
  | { kind: "done" }
  /** An update the server refused because the branch has nothing the live build lacks. */
  | { kind: "nothingNew" }
  | { kind: "failed"; detail: string; hint: string | null }
  /** The operator held it (closed "Confirm it's you") or it waits for a second person: nothing failed. */
  | { kind: "held"; detail: string };

/** The one call each verb is, on the application's own endpoint. */
async function perform(action: BulkAction, domain: string): Promise<void> {
  const params = { params: { domain } };
  switch (action) {
    case "restart":
      await request("post", "/api/apps/{domain}/restart", params);
      return;
    case "start":
      await request("post", "/api/apps/{domain}/start", params);
      return;
    case "stop":
      await request("post", "/api/apps/{domain}/stop", params);
      return;
    case "update":
      await request("post", "/api/jobs/update", { body: { domain, force: false } });
      return;
  }
}

function skipWords(t: T, reason: SkipReason): string {
  switch (reason) {
    case "static":
      return t("apps.bulk.skip.static");
    case "stopped":
      return t("apps.bulk.skip.stopped");
    case "running":
      return t("apps.bulk.skip.running");
    case "busy":
      return t("apps.bulk.skip.busy");
  }
}

function outcomeWords(t: T, action: BulkAction, outcome: Outcome): { state: Status; words: string; detail?: string } {
  switch (outcome.kind) {
    case "pending":
      return { state: "queued", words: t(`apps.bulk.will.${action}`) };
    case "working":
      return { state: "deploying", words: t("apps.bulk.working") };
    case "done":
      return { state: "running", words: t(`apps.bulk.did.${action}`) };
    case "nothingNew":
      return { state: "stopped", words: t("apps.bulk.nothingNew") };
    case "failed":
      return { state: "failed", words: t("apps.bulk.failed"), detail: outcome.hint === null ? outcome.detail : `${outcome.detail} ${outcome.hint}` };
    case "held":
      return { state: "queued", words: t("apps.bulk.held"), detail: outcome.detail };
  }
}

export interface BulkAppsDialogProps {
  /** The applications chosen, in the order they were. */
  apps: readonly AppInfo[];
  onClose: () => void;
}

/**
 * The action to take on the chosen applications: its plan first (what is done to each, what is left
 * out and why), then each one in turn, with what the server said of it, and a retry for the ones that
 * failed. Each verb is the call the application's own page makes, so permissions, sudo mode, the
 * audit trail and the queue are the ones every other way of doing it goes through. A held
 * "Confirm it's you" stops the rest rather than asking again for each.
 */
export function BulkAppsDialog({ apps, onClose }: BulkAppsDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [action, setAction] = useState<BulkAction>("restart");
  const [outcomes, setOutcomes] = useState<ReadonlyMap<string, Outcome>>(new Map());
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);

  const plan: PlanEntry[] = planBulk(action, apps);
  const { run, skipped } = counts(plan);
  const outcomeOf = (domain: string): Outcome => outcomes.get(domain) ?? { kind: "pending" };
  const finished = started && !running;
  const done = plan.filter((entry) => entry.skip === null && ["done", "nothingNew"].includes(outcomeOf(entry.domain).kind)).length;
  const failed = plan.filter((entry) => outcomeOf(entry.domain).kind === "failed");
  const unfinished = plan.filter((entry) => entry.skip === null && ["failed", "held", "pending"].includes(outcomeOf(entry.domain).kind));

  const set = (domain: string, outcome: Outcome): void => {
    setOutcomes((previous) => new Map(previous).set(domain, outcome));
  };

  /** Does the action to each of `entries` in turn, and stops at the first one the operator held. */
  const go = async (entries: readonly PlanEntry[]): Promise<void> => {
    setStarted(true);
    setRunning(true);
    let held: string | null = null;
    for (const entry of entries) {
      if (held !== null) {
        set(entry.domain, { kind: "held", detail: held });
        continue;
      }
      set(entry.domain, { kind: "working" });
      try {
        await perform(action, entry.domain);
        set(entry.domain, { kind: "done" });
      } catch (error) {
        const hold = heldOf(error);
        if (hold !== null) {
          held = hold.kind === "waiting" ? t("apps.bulk.waitingApproval") : hold.detail;
          set(entry.domain, { kind: "held", detail: held });
        } else if (action === "update" && isNothingNew(error)) {
          set(entry.domain, { kind: "nothingNew" });
        } else {
          const described = describeError(error);
          set(entry.domain, { kind: "failed", detail: described.detail, hint: described.hint });
        }
      }
    }
    setRunning(false);
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
  };

  const choose = (next: BulkAction): void => {
    setAction(next);
    setOutcomes(new Map());
    setStarted(false);
  };

  const doing = plan.filter((entry) => entry.skip === null);

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !running) onClose();
      }}
      size="lg"
      title={t("apps.bulk.title", { count: apps.length })}
      description={t("apps.bulk.description")}
      footer={
        <>
          <Button variant="ghost" disabled={running} onClick={onClose}>
            {t("apps.bulk.close")}
          </Button>
          {finished && unfinished.length > 0 ? (
            <Button
              onClick={() => {
                void go(unfinished);
              }}
            >
              {t("apps.bulk.retry")}
            </Button>
          ) : null}
          {finished ? null : (
            <Button
              loading={running}
              disabled={run === 0}
              onClick={() => {
                void go(doing);
              }}
            >
              {t(`apps.bulk.run.${action}`, { count: run })}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <SegmentedControl<BulkAction>
          label={t("apps.bulk.action")}
          options={BULK_ACTIONS.map((verb) => ({ value: verb, label: t(`apps.bulk.verb.${verb}`) }))}
          value={action}
          onValueChange={(next) => {
            if (!running) choose(next);
          }}
        />
        {action === "update" ? <p className="text-13 text-fg-muted">{t("apps.bulk.updateNote")}</p> : null}

        <RowList aria-label={t("apps.bulk.plan")}>
          {plan.map((entry) => {
            const skip = entry.skip;
            const view = skip === null ? outcomeWords(t, action, outcomeOf(entry.domain)) : null;
            return (
              <RowList.Item key={entry.domain} className="text-13">
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1">
                  <Mono>{entry.domain}</Mono>
                  {skip !== null ? (
                    <span className="text-fg-muted">{skipWords(t, skip)}</span>
                  ) : view !== null ? (
                    <span className="inline-flex items-center gap-1.5">
                      <StatusGlyph state={view.state} size={12} className={stateTextClass(view.state)} />
                      <span>{view.words}</span>
                    </span>
                  ) : null}
                </div>
                {view?.detail !== undefined ? (
                  <p className="text-12 break-words text-fg-muted">
                    <Mono tone="muted">{view.detail}</Mono>
                  </p>
                ) : null}
              </RowList.Item>
            );
          })}
        </RowList>

        <p role="status" className="text-13 text-fg-muted">
          {finished
            ? failed.length > 0
              ? t("apps.bulk.finishedFailed", { done, total: run, failed: failed.length })
              : t("apps.bulk.finished", { done, total: run })
            : t("apps.bulk.summary", { run, total: plan.length, skipped })}
        </p>
      </div>
    </Dialog>
  );
}
