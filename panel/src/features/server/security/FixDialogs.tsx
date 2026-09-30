/**
 * The three ways out of a finding: fix it automatically, follow the guided steps, or accept the
 * risk with a reason and an end date.
 *
 * An automatic fix of how the server is reached (sshd, the firewall) shows the effective
 * settings before and after, the proof that another way in works, and says that the change
 * undoes itself unless it is confirmed from a new SSH login. Turning off passwords or root
 * logins, and switching the firewall on, ask for the host name to be typed; every other fix asks
 * once.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock } from "../errors";
import { identityQuery, serverKeys, sshFixQuery } from "../queries";
import type { FixPlan, SecurityCheck } from "../queries";
import { useServerJob } from "../serverJob";
import { checkTitle, sshFixOf, typedFriction } from "./data";

/** Before and after of each sshd directive a fix changes, in sshd's own words. */
export function DirectiveChanges({ plan }: { plan: FixPlan }) {
  const t = useT();
  if (plan.changes.length === 0) return null;
  return (
    <ul aria-label={t("server.fix.changesLabel")} className="flex flex-col divide-y divide-border rounded-control border border-border bg-bg-sunken">
      {plan.changes.map((change) => (
        <li key={change.directive} className="flex min-w-0 flex-wrap items-baseline gap-x-2 px-3 py-1.5 text-12">
          <Mono>{change.directive}</Mono>
          <Mono tone="muted">{change.before === "" ? t("server.fix.unset") : change.before}</Mono>
          <span aria-hidden="true" className="text-fg-faint">
            →
          </span>
          <span className="sr-only">{t("server.fix.becomes")}</span>
          <Mono>{change.after}</Mono>
        </li>
      ))}
    </ul>
  );
}

/** Why an sshd fix cannot run now, and the steps that make it possible, verbatim. */
export function FixBlocked({ plan }: { plan: FixPlan }) {
  const t = useT();
  return (
    <Notice tone="warning" title={t("server.fix.blockedTitle")}>
      <div className="flex flex-col gap-2">
        {plan.blockers.map((blocker) => (
          <p key={blocker}>{blocker}</p>
        ))}
        {plan.guidance.length > 0 ? (
          <ol className="flex list-decimal flex-col gap-1 pl-5">
            {plan.guidance.map((step) => (
              <li key={step} className="whitespace-pre-wrap">
                {step}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </Notice>
  );
}

function SshPlan({ fix }: { fix: string }) {
  const t = useT();
  const plan = useQuery(sshFixQuery(fix));
  if (plan.isError) return <ServerErrorBlock compact error={plan.error} title={t("server.fix.planFailed")} />;
  if (plan.data === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-2">
        <span className="sr-only">{t("server.fix.planLoading")}</span>
        <Skeleton className="h-12 w-full rounded-control" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <DirectiveChanges plan={plan.data} />
      {/* The proof is already in the fix's summary, which the dialog's description says. */}
      {!plan.data.allowed ? <FixBlocked plan={plan.data} /> : null}
    </div>
  );
}

function requiresEpel(error: unknown): boolean {
  if (!isApiError(error) || error.error !== "confirmation_required") return false;
  const required = error.extra["required"];
  return typeof required === "object" && required !== null && (required as Record<string, unknown>)["epel"] === true;
}

export interface FixDialogProps {
  check: SecurityCheck;
  onClose: () => void;
}

/** Applies a check's automatic fix, as a job, after showing what it changes. */
export function FixDialog({ check, onClose }: FixDialogProps) {
  const t = useT();
  const jobs = useServerJob();
  const identity = useQuery(identityQuery());
  const [epel, setEpel] = useState(false);
  const [needsEpel, setNeedsEpel] = useState(false);
  const fix = check.fix;
  const action = fix?.action ?? null;
  const sshFix = sshFixOf(action);
  const hostname = identity.data?.hostname.hostname ?? "";

  const run = async (): Promise<void> => {
    try {
      const accepted = await request("post", "/api/server/security/checks/{check_id}/fix", { params: { check_id: check.id }, body: { epel } });
      jobs.track(accepted.job_id, "security");
    } catch (error: unknown) {
      if (requiresEpel(error)) setNeedsEpel(true);
      throw error;
    }
  };

  // Typing needs the name to type: until the identity answers, the question waits for it.
  if (typedFriction(action) && hostname === "" && !identity.isError) return null;
  return (
    <ActionDialog
      title={t("server.fix.title", { check: checkTitle(t, check) })}
      description={fix?.summary !== undefined && fix.summary !== "" ? fix.summary : t("server.fix.description")}
      actionLabel={t("server.fix.apply")}
      confirmText={typedFriction(action) ? hostname : undefined}
      onConfirm={run}
      onClose={onClose}
    >
      {sshFix !== null ? <SshPlan fix={sshFix} /> : null}
      {fix?.reverts ? <Notice title={t("server.fix.revertsTitle")}>{t("server.fix.reverts")}</Notice> : null}
      {needsEpel ? <Checkbox label={t("server.fix.epelLabel")} description={t("server.fix.epelDescription")} checked={epel} onCheckedChange={setEpel} /> : null}
      {fix?.cli ? <CommandHint command={fix.cli} label={t("server.fromTerminal")} /> : null}
    </ActionDialog>
  );
}

/** The guided steps of a finding that has no automatic fix (or whose fix cannot run now). */
export function GuidedDrawer({ check, onClose }: { check: SecurityCheck; onClose: () => void }) {
  const t = useT();
  const fix = check.fix;
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("server.fix.guidedTitle", { check: checkTitle(t, check) })}
      description={check.reason}
    >
      <div className="flex flex-col gap-4">
        {fix?.blocked ? <Notice tone="warning">{fix.blocked}</Notice> : null}
        {fix?.summary ? <p className="text-14 text-fg">{fix.summary}</p> : null}
        {fix !== null && fix !== undefined && fix.steps.length > 0 ? (
          <ol aria-label={t("server.fix.stepsLabel")} className="flex list-decimal flex-col gap-2 pl-5 text-13 text-fg">
            {fix.steps.map((step) => (
              <li key={step} className="whitespace-pre-wrap">
                {step}
              </li>
            ))}
          </ol>
        ) : null}
        {fix?.cli ? <CommandHint command={fix.cli} label={t("server.fromTerminal")} /> : null}
      </div>
    </Drawer>
  );
}

/** Ninety days from today, as the date input writes it: the default end of an acceptance. */
function inNinetyDays(): string {
  const date = new Date(Date.now() + 90 * 86_400_000);
  return date.toISOString().slice(0, 10);
}

/** Accepts a finding for a while, with a reason: it shows as accepted, not passed, until then. */
export function AcceptRiskDialog({ check, onClose }: { check: SecurityCheck; onClose: () => void }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState(inNinetyDays);
  const [error, setError] = useState<string | null>(null);
  const accept = useMutation({
    mutationFn: () => request("put", "/api/server/security/risks/{check_id}", { params: { check_id: check.id }, body: { reason: reason.trim(), expires_at: until } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serverKeys.security });
      onClose();
    },
  });
  const fields = isApiError(accept.error) ? accept.error.fields : null;
  const submit = (): void => {
    if (reason.trim().length < 10) {
      setError(t("server.risk.reasonShort"));
      return;
    }
    setError(null);
    accept.mutate();
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !accept.isPending) onClose();
      }}
      size="md"
      title={t("server.risk.title", { check: checkTitle(t, check) })}
      description={t("server.risk.description")}
      footer={
        <>
          <Button disabled={accept.isPending} onClick={onClose}>
            {t("server.risk.cancel")}
          </Button>
          <Button variant="primary" loading={accept.isPending} onClick={submit}>
            {t("server.risk.accept")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {node !== null ? <p className="text-13 text-fg-muted">{t.rich("server.risk.onServer", { server: <Mono>{node}</Mono> })}</p> : null}
        <Field label={t("server.risk.reasonLabel")} description={t("server.risk.reasonDescription")} error={error ?? fields?.["reason"] ?? null}>
          <Textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} />
        </Field>
        <Field label={t("server.risk.untilLabel")} description={t("server.risk.untilDescription")} error={fields?.["expires_at"] ?? null}>
          <Input type="date" value={until} onValueChange={(value: string) => setUntil(value)} className="w-48" />
        </Field>
        {accept.isError && fields === null ? <ServerErrorBlock live compact error={accept.error} title={t("server.risk.failed")} /> : null}
      </div>
    </Dialog>
  );
}
