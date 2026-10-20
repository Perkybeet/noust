import { useMutation, useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field, FieldGroup } from "../../../components/ui/Field";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { downloadText } from "../../../lib/clipboard";
import { splitErrors } from "../../settings/formErrors";
import { reportActionError } from "../../apps/useAppActions";
import { discardBlock } from "./declarationDiff";
import { DeclarationReview } from "./DeclarationReview";

type Applied = ResponseOf<"/api/apps/{domain}/apply", "post">;

/**
 * Build a declaration (pasted, or started from what runs now), review what it would change block
 * by block, put back the blocks that should stay as they are, and apply the rest as one set
 * (checklist M-35). The check changes nothing; what runs commands (a build block, hooks, tasks,
 * Compose arguments) is flagged in the plan and needs the box ticked before the server will apply it.
 */
function ApplyDialog({ domain, open, onOpenChange }: { domain: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const [text, setText] = useState("");
  const [prune, setPrune] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [result, setResult] = useState<Applied | null>(null);

  // The declaration of the application as it runs now: what a draft is compared with. Read once a
  // plan exists (or when a draft is started from it), so a draft is never compared with a stale one.
  const [started, setStarted] = useState(false);
  const current = useQuery({
    queryKey: ["app", domain, "declaration"],
    queryFn: () => request("get", "/api/apps/{domain}/declaration", { params: { domain } }),
    enabled: open && (result !== null || started),
    retry: false,
    staleTime: 0,
  });
  const fromNow = useMutation({
    mutationFn: () => request("get", "/api/apps/{domain}/declaration", { params: { domain } }),
    onSuccess: (answer) => {
      setText(answer.declaration);
      setStarted(true);
      setResult(null);
    },
  });

  const run = useMutation({
    mutationFn: ({ check, declaration }: { check: boolean; declaration?: string }) =>
      request("post", "/api/apps/{domain}/apply", { params: { domain }, body: { declaration: declaration ?? text, check, confirm: agreed, prune } }),
    onSuccess: (answer) => {
      setResult(answer);
      if (answer.applied) {
        const missed = answer.steps.filter((step) => !step.applied).length;
        announce(missed === 0 ? t("declaration.dialog.applied", { domain }) : t("declaration.dialog.partly", { count: missed }));
      }
    },
  });
  const split = splitErrors(run.error, ["declaration"] as const, "declaration");
  const plan = result?.plan ?? null;
  const needsAgreement = (plan?.confirm.length ?? 0) > 0;
  // Nothing to apply until a check has found something; the agreement is asked for when it is pressed.
  const nothingToApply = plan === null || plan.changes.length === 0;
  const [agreementAsked, setAgreementAsked] = useState(false);

  function close(): void {
    setResult(null);
    setAgreed(false);
    run.reset();
    onOpenChange(false);
  }
  const edit = (value: string): void => {
    setText(value);
    // A plan is of the text that was checked.
    setResult(null);
  };
  // One block put back to what runs now, and the rest checked again: the plan follows the draft.
  const discard = (key: string): void => {
    if (current.data === undefined) return;
    const next = discardBlock(current.data.declaration, text, key);
    setText(next);
    setAgreed(false);
    setResult(null);
    run.mutate({ check: true, declaration: next });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else if (!run.isPending) close();
      }}
      size="lg"
      title={t("declaration.dialog.title", { domain })}
      description={t("declaration.dialog.description")}
      footer={
        <>
          <Button disabled={run.isPending} onClick={close}>
            {t("declaration.dialog.close")}
          </Button>
          {result?.applied ? null : (
            <>
              <Button disabled={text.trim() === ""} loading={run.isPending && run.variables.check} onClick={() => run.mutate({ check: true })}>
                {t("declaration.dialog.check")}
              </Button>
              <Button
                variant="primary"
                disabled={nothingToApply}
                loading={run.isPending && !run.variables.check}
                onClick={() => {
                  if (needsAgreement && !agreed) {
                    setAgreementAsked(true);
                    return;
                  }
                  run.mutate({ check: false });
                }}
              >
                {t("declaration.dialog.applyNow")}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Disclosure label={t("declaration.scope.title")}>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("declaration.scope.body")}</p>
        </Disclosure>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" loading={fromNow.isPending} disabled={run.isPending} onClick={() => fromNow.mutate()}>
            {t("declaration.dialog.startFromNow")}
          </Button>
          {fromNow.isError ? <ErrorBlock live compact error={fromNow.error} title={t("declaration.dialog.startFailed", { domain })} /> : null}
        </div>
        <Field label={t("declaration.dialog.label")} error={split.fields.declaration}>
          <Textarea mono rows={10} value={text} placeholder={t("declaration.dialog.placeholder")} onChange={(event) => edit(event.target.value)} />
        </Field>
        <Checkbox label={t("declaration.dialog.prune")} description={t("declaration.dialog.pruneDescription")} checked={prune} onCheckedChange={(value) => {
          setPrune(value);
          setResult(null);
        }} />
        {run.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("declaration.dialog.failed")} /> : null}
        {plan !== null ? (
          <Subsection title={t("declaration.plan.title")}>
            <div className="flex flex-col gap-3" aria-live="polite">
            {plan.changes.length === 0 ? <EmptyState variant="inline" title={t("declaration.plan.nothing")} /> : null}
            <ul className="flex flex-col gap-1.5">
              {plan.changes.map((change) => (
                <li key={`${change.section}:${change.description}`} className="text-13 text-fg">
                  {change.description}
                  {change.root_equivalent ? <StatusPill state="warning" size="sm" label={t("declaration.plan.runsCommands")} className="ml-2" /> : null}
                </li>
              ))}
            </ul>
            {current.data !== undefined ? (
              <DeclarationReview current={current.data.declaration} text={text} plan={plan} onDiscard={discard} locked={run.isPending || result?.applied === true} />
            ) : null}
            {plan.unchanged.length > 0 ? <p className="text-12 text-fg-muted">{t("declaration.plan.already", { sections: plan.unchanged.join(", ") })}</p> : null}
            {plan.notes.map((note) => (
              <p key={note} className="max-w-measure text-13 text-pretty text-fg-muted">
                {note}
              </p>
            ))}
            {needsAgreement && !result?.applied ? (
              <FieldGroup legend={t("declaration.plan.confirmLegend")} error={agreementAsked && !agreed ? t("declaration.plan.confirmAsked") : undefined}>
                <Checkbox label={t("declaration.plan.confirm")} checked={agreed} onCheckedChange={setAgreed} />
              </FieldGroup>
            ) : null}
            {result?.applied
              ? result.steps.filter((step) => !step.applied).map((step) => (
                  <Notice key={step.part} tone="warning" title={`${t("declaration.plan.notApplied")}: ${step.part}`}>
                    {step.detail}
                  </Notice>
                ))
              : null}
            </div>
          </Subsection>
        ) : null}
      </div>
    </Dialog>
  );
}

/**
 * The application as a declaration file (`noust app export --as-yaml`), downloaded, and an
 * application set from one (`noust app apply`). The file is the keys of `noust.yaml`; secrets are
 * never in it.
 */
export function DeclarationCard({ domain }: { domain: string }) {
  const t = useT();
  const [applying, setApplying] = useState(false);
  const download = useMutation({
    mutationFn: () => request("get", "/api/apps/{domain}/declaration", { params: { domain } }),
    onSuccess: (answer) => {
      downloadText("noust.yaml", answer.declaration, "text/yaml");
      // design-exception: toast-success A download happens outside the page: nothing on screen says it went through.
      toast.success(t("declaration.downloaded", { domain }));
    },
    onError: (error: unknown) => {
      reportActionError(t("declaration.downloadFailed"), error);
    },
  });
  return (
    <Card title={t("declaration.title")} description={t("declaration.description")}>
      <div className="flex flex-col gap-4">
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("declaration.secrets")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button icon={<Download aria-hidden="true" />} loading={download.isPending} onClick={() => download.mutate()}>
            {t("declaration.download")}
          </Button>
          <Button variant="ghost" onClick={() => setApplying(true)}>
            {t("declaration.fromFile")}
          </Button>
        </div>
      </div>
      <ApplyDialog key={String(applying)} domain={domain} open={applying} onOpenChange={setApplying} />
    </Card>
  );
}
