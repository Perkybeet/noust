import { useMutation } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { downloadText } from "../../../lib/clipboard";
import { splitErrors } from "../../settings/formErrors";
import { reportActionError } from "../../apps/useAppActions";

type Applied = ResponseOf<"/api/apps/{domain}/apply", "post">;

/**
 * Paste a declaration, see what it would change, and apply it. The check changes nothing; what
 * runs commands (a build block, hooks, tasks, Compose arguments) is flagged in the plan and
 * needs the box ticked before the server will apply it.
 */
function ApplyDialog({ domain, open, onOpenChange }: { domain: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const [text, setText] = useState("");
  const [prune, setPrune] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [result, setResult] = useState<Applied | null>(null);

  const run = useMutation({
    mutationFn: (check: boolean) =>
      request("post", "/api/apps/{domain}/apply", { params: { domain }, body: { declaration: text, check, confirm: agreed, prune } }),
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
              <Button disabled={text.trim() === ""} loading={run.isPending && run.variables} onClick={() => run.mutate(true)}>
                {t("declaration.dialog.check")}
              </Button>
              <Button
                variant="primary"
                disabled={plan === null || plan.changes.length === 0 || (needsAgreement && !agreed)}
                loading={run.isPending && !run.variables}
                onClick={() => run.mutate(false)}
              >
                {t("declaration.dialog.applyNow")}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
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
            {plan.changes.length === 0 ? <p className="text-13 text-fg-muted">{t("declaration.plan.nothing")}</p> : null}
            <ul className="flex flex-col gap-1.5">
              {plan.changes.map((change) => (
                <li key={`${change.section}:${change.description}`} className="text-13 text-fg">
                  {change.description}
                  {change.root_equivalent ? <span className="ml-2 text-12 text-warn">{t("declaration.plan.runsCommands")}</span> : null}
                </li>
              ))}
            </ul>
            {plan.unchanged.length > 0 ? <p className="text-12 text-fg-muted">{t("declaration.plan.already", { sections: plan.unchanged.join(", ") })}</p> : null}
            {plan.notes.map((note) => (
              <Notice key={note}>{note}</Notice>
            ))}
            {needsAgreement && !result?.applied ? <Checkbox label={t("declaration.plan.confirm")} checked={agreed} onCheckedChange={setAgreed} /> : null}
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
            {t("declaration.apply")}
          </Button>
        </div>
        <CommandHint command={`noust app export ${domain} --as-yaml -o noust.yaml`} label={t("declaration.fromTerminal")} />
      </div>
      <ApplyDialog key={String(applying)} domain={domain} open={applying} onOpenChange={setApplying} />
    </Card>
  );
}
