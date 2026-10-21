import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ElevationCancelledError, request } from "../../../api/client";
import { exposureKeys } from "../../../api/queries/consoleExposure";
import type { ExposurePlan } from "../../../api/queries/consoleExposure";
import { isJobFinished, jobKeys, useFollowedJob } from "../../../api/queries/jobs";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Stepper } from "../../../components/page/Stepper";
import { WizardActions } from "../../../components/page/Wizard";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { JobLogPanel } from "../../jobs/JobLogPanel";
import { jobState } from "../../databases/jobs";
import { EMPTY_FORM, bodyOf, formProblem } from "./data";
import type { PublishForm } from "./data";

type Step = "name" | "review" | "publish";

/** What the job answers with once the console is published. */
function resultOf(value: unknown): { url: string | null; notes: string[] } {
  const record = typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  const notes = Array.isArray(record["notes"]) ? record["notes"].filter((note): note is string => typeof note === "string") : [];
  return { url: typeof record["url"] === "string" ? record["url"] : null, notes };
}

export interface PublishConsoleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Whether the ENS profile lets the console be published: when it does not, forcing is offered. */
  ensAllows: boolean;
}

/**
 * Publishing the console: the name and who may reach it, then what Noust will do (the server's
 * own checks, run first, so a refusal is an answer here and not a failed job), then the job with
 * its output. The refusals - an account without a second factor, the ENS profile, a name that
 * is an application's - are shown as the server wrote them. The last button asks "Confirm it's
 * you".
 */
export function PublishConsoleDialog({ open, onOpenChange, ensAllows }: PublishConsoleDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step>("name");
  const [form, setForm] = useState<PublishForm>(EMPTY_FORM);
  const [fieldError, setFieldError] = useState<{ field: "domain" | "allow" | "ens"; text: string } | null>(null);
  const [plan, setPlan] = useState<ExposurePlan | null>(null);
  const followed = useFollowedJob();
  const domainRef = useRef<HTMLInputElement>(null);
  const body = bodyOf(form);

  const preview = useMutation({
    mutationFn: () => request("post", "/api/console/exposure/preview", { body }),
    onSuccess: (answer) => {
      setPlan(answer);
      setStep("review");
    },
  });
  const publish = useMutation({
    mutationFn: () => request("post", "/api/console/exposure", { body }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      followed.follow(accepted.job_id);
      setStep("publish");
    },
    onError: (error) => {
      if (error instanceof ElevationCancelledError) publish.reset();
    },
  });

  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: exposureKeys.all });
  }, [job, queryClient]);

  const change = (patch: Partial<PublishForm>): void => {
    setForm((previous) => ({ ...previous, ...patch }));
    setFieldError(null);
    preview.reset();
  };

  const close = (): void => {
    if (preview.isPending || publish.isPending) return;
    onOpenChange(false);
    setStep("name");
    setForm(EMPTY_FORM);
    setFieldError(null);
    setPlan(null);
    followed.dismiss();
    preview.reset();
    publish.reset();
  };

  const next = (): void => {
    if (step === "name") {
      const problem = formProblem(form, ensAllows);
      if (problem !== null) {
        setFieldError({
          field: problem.problem,
          text:
            problem.problem === "domain"
              ? t("consoleAccess.dialog.domainRequired")
              : problem.problem === "allow"
                ? t("consoleAccess.dialog.allowInvalid", { entries: problem.entries.join(", ") })
                : t("consoleAccess.dialog.forceEnsNeedsList"),
        });
        if (problem.problem === "domain") domainRef.current?.focus();
        return;
      }
      preview.mutate();
      return;
    }
    publish.mutate();
  };

  const state = job === null ? "queued" : jobState(job.status);
  const result = job !== null && state === "succeeded" ? resultOf(job.result) : null;
  const steps = [
    { id: "name", label: t("consoleAccess.dialog.steps.name") },
    { id: "review", label: t("consoleAccess.dialog.steps.review") },
    { id: "publish", label: t("consoleAccess.dialog.steps.publish") },
  ];

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) close();
      }}
      size="lg"
      initialFocus={domainRef}
      title={t("consoleAccess.dialog.title")}
      description={t("consoleAccess.dialog.description")}
      footer={
        step === "publish" ? (
          <>
            {result?.url !== null && result?.url !== undefined ? (
              <ExternalLink href={result.url} button="secondary">
                {t("consoleAccess.actions.open", { url: result.url })}
              </ExternalLink>
            ) : null}
            <Button variant="primary" onClick={close}>
              {finished ? t("consoleAccess.dialog.done") : t("consoleAccess.dialog.close")}
            </Button>
          </>
        ) : (
          <WizardActions
            className="w-full"
            back={{
              label: step === "name" ? t("consoleAccess.dialog.cancel") : t("consoleAccess.dialog.back"),
              onClick: () => {
                if (publish.isPending || preview.isPending) return;
                if (step === "name") close();
                else {
                  publish.reset();
                  setStep("name");
                }
              },
            }}
            next={{ label: step === "name" ? t("consoleAccess.dialog.preview") : t("consoleAccess.dialog.submit"), loading: preview.isPending || publish.isPending, onClick: next }}
          />
        )
      }
    >
      <div className="flex min-w-0 flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {step === "name" ? (
          <form
            noValidate
            className="flex min-w-0 flex-col gap-5"
            onSubmit={(event) => {
              event.preventDefault();
              next();
            }}
          >
            <Field label={t("consoleAccess.dialog.domain")} description={t("consoleAccess.dialog.domainHelp")} error={fieldError?.field === "domain" ? fieldError.text : undefined}>
              <Input ref={domainRef} mono value={form.domain} onValueChange={(domain: string) => change({ domain })} placeholder="console.example.com" autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
            <Field label={t("consoleAccess.dialog.allow")} description={t("consoleAccess.dialog.allowHelp")} error={fieldError?.field === "allow" ? fieldError.text : undefined} optional>
              <Textarea mono rows={2} value={form.allow} onChange={(event) => change({ allow: event.target.value })} placeholder="203.0.113.0/24" autoComplete="off" />
            </Field>
            {!ensAllows ? (
              <div className="flex min-w-0 flex-col gap-2">
                <Checkbox checked={form.forceEns} onCheckedChange={(forceEns) => change({ forceEns })} label={t("consoleAccess.dialog.forceEns")} description={t("consoleAccess.dialog.forceEnsHelp")} />
                {fieldError?.field === "ens" ? <Notice tone="warning" live>{fieldError.text}</Notice> : null}
              </div>
            ) : null}
            <Disclosure label={t("consoleAccess.dialog.more")} defaultOpen={form.skipDns}>
              <Checkbox checked={form.skipDns} onCheckedChange={(skipDns) => change({ skipDns })} label={t("consoleAccess.dialog.skipDns")} description={t("consoleAccess.dialog.skipDnsHelp")} />
            </Disclosure>
            {preview.isError ? <ErrorBlock live compact error={preview.error} title={t("consoleAccess.dialog.previewFailed")} /> : null}
          </form>
        ) : step === "review" && plan !== null ? (
          <div className="flex min-w-0 flex-col gap-4">
            <p className="text-13 font-medium text-fg">{t("consoleAccess.dialog.reviewTitle")}</p>
            <ol className="flex list-decimal flex-col gap-2 pl-5 text-13 text-fg" translate="no">
              {plan.steps.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ol>
            <p className="text-13 text-fg-muted">{t("consoleAccess.dialog.reviewNote")}</p>
            {publish.isError && !(publish.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={publish.error} title={t("consoleAccess.dialog.publishFailed")} /> : null}
          </div>
        ) : (
          <JobLogPanel
            job={job}
            words={{
              queued: t("consoleAccess.dialog.jobRunning", { domain: body.domain }),
              running: t("consoleAccess.dialog.jobRunning", { domain: body.domain }),
              succeeded: t("consoleAccess.dialog.jobSucceeded", { domain: body.domain }),
              failed: t("consoleAccess.dialog.jobFailed", { domain: body.domain }),
            }}
            label={t("consoleAccess.dialog.logLabel")}
            result={
              result !== null ? (
                <div className="flex min-w-0 flex-col gap-2">
                  <p className="text-13 text-fg-muted">{t("consoleAccess.dialog.restarting")}</p>
                  {result.notes.length > 0 ? (
                    <ul className="flex flex-col gap-1 text-13 text-fg" translate="no">
                      {result.notes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null
            }
          />
        )}
      </div>
    </Dialog>
  );
}
