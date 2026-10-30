/**
 * Moving the sites to the other web server (item 105), in the order a careful person decides it:
 * what it comes to, what stops it, what the new sites will not do (a protection first), and only
 * then the button. Nothing is changed by looking; the move is a job the page follows, and the
 * server puts everything back when the new web server does not accept a site or does not answer
 * for one, saying what it could not put back.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ElevationCancelledError } from "../../../api/client";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { WizardActions } from "../../../components/page/Wizard";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { ServerText } from "../../../components/ui/ServerText";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { jobState } from "../../databases/jobs";
import { JobLogPanel } from "../../jobs/JobLogPanel";
import { ServerQueryState } from "../errors";
import { serverKeys } from "../queries";
import { canMove, display, groupFindings, needsAcceptance, orderSites, planNumbers } from "./data";
import { movePlanQuery, startMove, webserverKeys } from "./queries";
import type { MovePlan, Sentence, SitePlan, WebServerName } from "./queries";

/** One finding or blocker: the sentence in the console's words and its fix under it. */
function FindingRow({ finding }: { finding: Sentence }) {
  const coded = { code: finding.code, params: finding.params ?? {} };
  return (
    <li className="flex min-w-0 flex-col gap-0.5 py-1.5 first:pt-0 last:pb-0">
      <span className="max-w-measure text-13 text-pretty text-fg">
        <ServerText coded={coded} text={finding.text} />
      </span>
      {finding.hint !== null && finding.hint !== undefined ? (
        <span className="max-w-measure text-12 text-pretty text-fg-muted">
          <ServerText coded={coded} text={finding.hint} part="hint" />
        </span>
      ) : null}
    </li>
  );
}

function FindingList({ title, tone, findings }: { title: string; tone: "failed" | "warning" | "unknown"; findings: readonly Sentence[] }) {
  if (findings.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <p className={`flex items-center gap-1.5 text-13 font-medium ${stateTextClass(tone)}`}>
        <StatusGlyph state={tone} size={12} />
        {title}
      </p>
      <ul className="flex flex-col divide-y divide-border pl-5">
        {findings.map((finding, index) => (
          <FindingRow key={`${finding.code}:${String(index)}`} finding={finding} />
        ))}
      </ul>
    </div>
  );
}

/** One site: its name, whether it moves, and what the move does to it, the parts that cost something first. */
function SiteRow({ t, site }: { t: T; site: SitePlan }) {
  const found = groupFindings(site);
  const open = found.blocks.length + found.protections.length + found.loses.length > 0 || !site.movable;
  const summary = !site.movable ? t("serverWeb.move.stays") : found.protections.length > 0 ? t("serverWeb.move.protectionsLost", { count: found.protections.length }) : undefined;
  return (
    <li className="min-w-0">
      <Disclosure variant="card" defaultOpen={open} label={site.domain} {...(summary !== undefined ? { summary } : {})}>
        <div className="flex min-w-0 flex-col gap-3 pb-1">
          <FindingList title={t("serverWeb.move.blocks")} tone="failed" findings={[...(site.blockers ?? []), ...found.blocks]} />
          <FindingList title={t("serverWeb.move.protections")} tone="warning" findings={found.protections} />
          <FindingList title={t("serverWeb.move.loses")} tone="warning" findings={found.loses} />
          <FindingList title={t("serverWeb.move.notes")} tone="unknown" findings={found.notes} />
          <KeyValueList
            items={[
              { label: t("serverWeb.move.fileNow"), value: site.source_path },
              { label: t("serverWeb.move.fileAfter"), value: site.target_path },
            ]}
          />
        </div>
      </Disclosure>
    </li>
  );
}

function Plan({ t, plan, accepted, onAccepted }: { t: T; plan: MovePlan; accepted: boolean; onAccepted: (next: boolean) => void }) {
  const numbers = planNumbers(plan);
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <p className="max-w-measure text-14 text-pretty text-fg">
        <ServerText coded={{ code: plan.confirmation.code, params: plan.confirmation.params ?? {} }} text={plan.confirmation.text} />
      </p>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{plan.mode === "handover" ? t("serverWeb.move.mode.handover", { target: display(plan.target), source: display(plan.source) }) : t("serverWeb.move.mode.live", { target: display(plan.target) })}</p>
      {plan.blockers !== undefined && plan.blockers.length > 0 ? (
        <Notice tone="error" title={t("serverWeb.move.cannotTitle")}>
          <ul className="flex flex-col gap-2">
            {plan.blockers.map((blocker) => (
              <li key={blocker.code} className="flex min-w-0 flex-col gap-0.5">
                <ServerText coded={{ code: blocker.code, params: blocker.params ?? {} }} text={blocker.text} />
                {blocker.hint !== null && blocker.hint !== undefined && !(blocker.code === "webserver_move.target_missing" && plan.install_command !== "") ? (
                  <span className="text-fg-muted">
                    <ServerText coded={{ code: blocker.code, params: blocker.params ?? {} }} text={blocker.hint} part="hint" />
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}
      {!plan.target_installed && plan.install_command !== "" ? (
        <div className="flex min-w-0 flex-col gap-1.5">
          <p className="text-13 text-fg">{t("serverWeb.move.install", { target: display(plan.target) })}</p>
          <CopyValue value={plan.install_command} label={t("serverWeb.move.copyInstall")} />
        </div>
      ) : null}
      {(plan.left_behind ?? []).length > 0 ? (
        <Notice tone="warning" title={t("serverWeb.move.leftBehind", { count: (plan.left_behind ?? []).length, source: display(plan.source) })}>
          <Mono>{(plan.left_behind ?? []).join(", ")}</Mono>
        </Notice>
      ) : null}
      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-13 font-medium text-fg">{t("serverWeb.move.sitesTitle", { movable: numbers.movable, total: numbers.sites })}</p>
        <ul className="flex flex-col gap-2">
          {orderSites(plan.sites).map((site) => (
            <SiteRow key={site.domain} t={t} site={site} />
          ))}
        </ul>
      </div>
      {needsAcceptance(plan) ? (
        <Checkbox
          checked={accepted}
          onCheckedChange={onAccepted}
          label={t("serverWeb.move.accept", { count: plan.losses })}
          description={numbers.protections > 0 ? t("serverWeb.move.acceptProtections", { count: numbers.protections }) : t("serverWeb.move.acceptHelp")}
        />
      ) : null}
    </div>
  );
}

export interface MoveDialogProps {
  /** The web server the sites are moved to. */
  to: WebServerName;
  onClose: () => void;
}

/** The plan of moving every site to the other web server, the agreement it asks for, and the job that does it. */
export function MoveDialog({ to, onClose }: MoveDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const plan = useQuery(movePlanQuery(to, null));
  const [accepted, setAccepted] = useState(false);
  const followed = useFollowedJob();
  const start = useMutation({
    mutationFn: () => startMove({ to, all: true, accept_losses: accepted, confirm: true }),
    onSuccess: (queued) => followed.follow(queued.job_id),
    onError: (error) => {
      // Declining "Confirm it's you" changes nothing: the plan stays where it was.
      if (error instanceof ElevationCancelledError) start.reset();
    },
  });
  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const running = followed.id !== null && !finished;
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: webserverKeys.status });
    void queryClient.invalidateQueries({ queryKey: serverKeys.all });
  }, [job, queryClient]);
  const name = display(to);
  const close = (): void => {
    if (start.isPending) return;
    onClose();
  };

  let body;
  let footer;
  if (followed.id !== null) {
    const state = job === null ? "queued" : jobState(job.status);
    body = (
      <JobLogPanel
        job={job}
        words={{ queued: t("serverWeb.move.job.queued", { target: name }), running: t("serverWeb.move.job.running", { target: name }), succeeded: t("serverWeb.move.job.succeeded", { target: name }), failed: t("serverWeb.move.job.failed") }}
        label={t("serverWeb.move.job.log", { target: name })}
        height={260}
      />
    );
    footer = (
      <>
        {state === "failed" ? <p className="mr-auto max-w-measure text-13 text-pretty text-fg-muted">{t("serverWeb.move.job.putBack")}</p> : null}
        <Button variant="primary" onClick={close}>
          {running ? t("common.words.close") : t("common.words.done")}
        </Button>
      </>
    );
  } else {
    body = (
      <ServerQueryState query={plan} label={t("serverWeb.move.planLabel")} skeleton={<SkeletonSection rows={4} />}>
        {(data) => <Plan t={t} plan={data} accepted={accepted} onAccepted={setAccepted} />}
      </ServerQueryState>
    );
    const ready = plan.data !== undefined && canMove(plan.data);
    footer = (
      <div className="flex w-full flex-col gap-3">
        {start.isError && !(start.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={start.error} title={t("serverWeb.move.startFailed")} /> : null}
        {ready ? (
          <WizardActions
            className="w-full"
            back={{ label: t("common.words.cancel"), onClick: close }}
            next={{ label: t("serverWeb.move.go", { target: name }), loading: start.isPending, onClick: () => start.mutate() }}
            {...(needsAcceptance(plan.data) && !accepted ? { missing: t("serverWeb.move.acceptFirst") } : {})}
          />
        ) : (
          <div className="flex justify-end">
            <Button onClick={close}>{t("common.words.close")}</Button>
          </div>
        )}
      </div>
    );
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="lg"
      title={t("serverWeb.move.title", { target: name })}
      description={t("serverWeb.move.description")}
      footer={footer}
    >
      {body}
    </Dialog>
  );
}
