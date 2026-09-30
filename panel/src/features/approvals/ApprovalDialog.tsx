import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Textarea } from "../../components/ui/Textarea";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { roleLabel } from "../settings/accounts/roles";
import { approvalPolicyQuery, approvalQuery } from "./api";
import { ApprovalStateLabel } from "./ApprovalStateLabel";
import { dismiss, giveReason, openInbox, runNow, useApprovalPrompt } from "./store";
import type { ApprovalPrompt } from "./store";

/** How often the requester's dialog asks whether the request was decided. */
const POLL_MS = 4_000;

/** "PUT /api/services/worker/config", as the call goes out. */
function CallLine({ method, target }: { method: string; target: string }) {
  return (
    <Mono tone="default">
      {method} {target}
    </Mono>
  );
}

function approversOf(t: T, approvers: readonly string[] | undefined): string {
  return (approvers ?? ["security"]).map((role) => roleLabel(t, role)).join(", ");
}

/** Asking why: the server files nothing without a reason. */
function ReasonStep({ prompt }: { prompt: Extract<ApprovalPrompt, { kind: "reason" }> }) {
  const t = useT();
  const { data: policy } = useQuery(approvalPolicyQuery());
  const [reason, setReason] = useState("");
  const [missing, setMissing] = useState(false);
  const formId = useId();
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) dismiss("approval_cancelled");
      }}
      size="md"
      title={t("approvals.dialog.reasonTitle")}
      description={t("approvals.dialog.reasonDescription", { approvers: approversOf(t, policy?.approvers) })}
      footer={
        <>
          <Button onClick={() => dismiss("approval_cancelled")}>{t("approvals.dialog.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary">
            {t("approvals.dialog.ask")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (reason.trim() === "") {
            setMissing(true);
            return;
          }
          giveReason(reason.trim());
        }}
      >
        <p className="text-13 text-fg-muted">
          {t.rich("approvals.dialog.theCall", { call: <CallLine key="call" method={prompt.call.method} target={prompt.call.target} /> })}
        </p>
        <Field label={t("approvals.dialog.reasonLabel")} description={t("approvals.dialog.reasonHint")} error={missing ? t("approvals.dialog.reasonMissing") : null}>
          <Textarea
            rows={3}
            maxLength={500}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
              if (missing) setMissing(false);
            }}
          />
        </Field>
      </form>
    </Dialog>
  );
}

/** Waiting for the decision: the request, its state, and running it once approved. */
function RequestedStep({ prompt }: { prompt: Extract<ApprovalPrompt, { kind: "requested" }> }) {
  const t = useT();
  const { requested } = prompt;
  const { data: policy } = useQuery(approvalPolicyQuery());
  const query = useQuery({
    ...approvalQuery(requested.id),
    refetchInterval: (current) => (current.state.data === undefined || current.state.data.state === "requested" ? POLL_MS : false),
  });
  const approval = query.data;
  const state = approval?.state ?? requested.state ?? "requested";
  const approved = state === "approved";
  const closed = state === "rejected" || state === "expired" || state === "executed";

  const close = (): void => {
    dismiss(state === "rejected" ? "approval_rejected" : state === "expired" ? "approval_expired" : "approval_pending");
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="md"
      title={approved ? t("approvals.dialog.approvedTitle") : closed ? t("approvals.dialog.closedTitle") : t("approvals.dialog.waitingTitle")}
      description={
        approved
          ? t("approvals.dialog.approvedDescription")
          : closed
            ? t("approvals.dialog.closedDescription")
            : t("approvals.dialog.waitingDescription", { approvers: approversOf(t, policy?.approvers) })
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => {
              close();
              openInbox();
            }}
          >
            {t("approvals.dialog.openInbox")}
          </Button>
          {approved ? (
            <>
              <Button onClick={close}>{t("approvals.dialog.later")}</Button>
              <Button variant="primary" onClick={runNow}>
                {t("approvals.dialog.runNow")}
              </Button>
            </>
          ) : (
            <Button variant={closed ? "primary" : "secondary"} onClick={close}>
              {closed ? t("approvals.dialog.close") : t("approvals.dialog.leaveWaiting")}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <KeyValueList
          items={[
            { label: t("approvals.fields.request"), value: `#${requested.id}` },
            { label: t("approvals.fields.state"), value: <ApprovalStateLabel t={t} state={state} />, mono: false, copy: false },
            { label: t("approvals.fields.call"), value: <CallLine method={requested.call.method} target={requested.call.target} />, copy: false },
            ...(approval?.decider
              ? [{ label: t("approvals.fields.decider"), value: approval.decider.name }]
              : []),
          ]}
        />
        {approval?.decision_comment ? (
          <Notice title={t("approvals.dialog.comment", { name: approval.decider?.name ?? "" })}>{approval.decision_comment}</Notice>
        ) : null}
        {state === "requested" ? <p className="text-13 text-fg-muted">{t("approvals.dialog.leaveNote")}</p> : null}
        {query.isError ? <ErrorBlock compact error={query.error} title={t("approvals.dialog.loadFailed")} onRetry={() => void query.refetch()} /> : null}
      </div>
    </Dialog>
  );
}

/**
 * The dialog of a call that needs a second person: mounted once, at the root, and opened by
 * the API client. It asks why (when the server wants a reason), says who can approve, waits
 * with the operator, and runs the call once it is approved - or lets them leave it waiting,
 * to run later from the approvals inbox.
 */
export function ApprovalDialog() {
  const prompt = useApprovalPrompt();
  if (prompt === null) return null;
  return prompt.kind === "reason" ? <ReasonStep key="reason" prompt={prompt} /> : <RequestedStep key={prompt.requested.id} prompt={prompt} />;
}
