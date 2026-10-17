import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { installAppPackages, appPackagesQuery, packagesKeys, putAppPackages } from "../../../api/queries/packages";
import type { AppPackages } from "../../../api/queries/packages";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { splitErrors } from "../../settings/formErrors";

/** One name per line, blank lines left out and a repeated name once: what the API takes. */
function namesOf(text: string): string[] {
  const seen = new Set<string>();
  for (const line of text.split(/[\n,\s]+/)) {
    const name = line.trim();
    if (name !== "") seen.add(name);
  }
  return [...seen];
}

/** The list and the form that changes it; keyed on the stored list so a save redraws it clean. */
function PackagesForm({ domain, state }: { domain: string; state: AppPackages }) {
  const t = useT();
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const handled = useRef<string | null>(null);
  const [text, setText] = useState(state.declared.join("\n"));
  const names = namesOf(text);
  const changed = JSON.stringify(names) !== JSON.stringify(state.declared);

  const save = useMutation({
    mutationFn: () => putAppPackages(domain, names),
    onSuccess: (result) => {
      queryClient.setQueryData(packagesKeys.forApp(domain), result);
      announce(t("appPackages.saved", { domain }));
    },
  });
  const install = useMutation({
    mutationFn: () => installAppPackages(domain),
    onSuccess: (result) => {
      followed.follow(result.job as Job);
    },
  });
  const split = splitErrors(save.error, ["packages"] as const);
  const job = followed.job;
  const installing = install.isPending || (job !== null && !isJobFinished(job));
  const jobFailed = job !== null && isJobFinished(job) && job.status !== "completed";

  // What the host has changes when the install ends: read it again.
  useEffect(() => {
    if (job === null || !isJobFinished(job) || handled.current === job.id) return;
    handled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: packagesKeys.forApp(domain) });
    if (job.status === "completed") announce(t("appPackages.installed", { domain }));
  }, [job, domain, queryClient, t]);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (changed && !save.isPending) save.mutate();
  };
  const refused = new Set(state.refused);
  const missing = new Set(state.missing);

  return (
    <div className="flex flex-col gap-4">
      {!state.supported ? (
        <Notice tone="warning" title={t("appPackages.unsupportedTitle")}>
          {t("appPackages.unsupportedBody")}
        </Notice>
      ) : null}
      {state.declared.length > 0 ? (
        <ul aria-label={t("appPackages.listLabel")} className="flex flex-col gap-1.5 text-13">
          {state.declared.map((name) => (
            <li key={name} className="flex min-w-0 items-center gap-2">
              <Mono tone="default" truncate>
                {name}
              </Mono>
              {refused.has(name) ? (
                <StatusPill state="failed" size="sm" label={t("appPackages.refused")} />
              ) : missing.has(name) ? (
                <StatusPill state={state.supported ? "warning" : "unknown"} size="sm" label={state.supported ? t("appPackages.missing") : t("appPackages.notChecked")} />
              ) : (
                <StatusPill state="running" size="sm" label={t("appPackages.hasIt")} />
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("appPackages.none")}</p>
      )}
      {state.refused.length > 0 ? (
        <Notice tone="error" title={t("appPackages.refusedTitle")}>
          {t("appPackages.refusedBody", { names: state.refused.join(", ") })}
        </Notice>
      ) : null}
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label={t("appPackages.field")}
          description={state.policy.length > 0 ? t("appPackages.fieldPolicy", { allowed: state.policy.join(", ") }) : t("appPackages.fieldDescription")}
          error={split.fields.packages}
        >
          <Textarea mono rows={4} value={text} placeholder={t("appPackages.placeholder")} onChange={(event) => setText(event.target.value)} />
        </Field>
        {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("appPackages.notSaved", { domain })} /> : null}
        {install.isError ? <ErrorBlock live compact error={install.error} title={t("appPackages.notStarted", { domain })} /> : null}
        {jobFailed ? <ErrorBlock live compact error={{ detail: job.error ?? t("appPackages.jobFailedSilently") }} title={t("appPackages.notInstalled", { domain })} /> : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" size="sm" loading={save.isPending} disabled={!changed}>
            {t("appPackages.save")}
          </Button>
          <Button size="sm" loading={installing} disabled={changed || state.missing.length === 0 || !state.supported || state.refused.length > 0} onClick={() => install.mutate()}>
            {t("appPackages.install")}
          </Button>
        </div>
        <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("appPackages.when")}</p>
      </form>
    </div>
  );
}

/**
 * The system packages the application needs on the host (an image library, a font, a converter),
 * which Noust installs before the next build. Only an operator writes them: a repository has no
 * way to ask for one, and the server's policy (`security.app_packages`) says which may be asked.
 * The same as `noust app packages`.
 */
export function PackagesCard({ domain }: { domain: string }) {
  const t = useT();
  const view = useQuery(appPackagesQuery(domain));
  const title = t("appPackages.title");
  const description = t("appPackages.description");
  if (view.isPending) {
    return (
      <Card title={title} description={description} loading>
        <span className="sr-only">{t("appPackages.loading")}</span>
        <Skeleton className="h-16 w-full rounded-control" />
      </Card>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock compact error={view.error} title={t("appPackages.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  return (
    <Card title={title} description={description}>
      <PackagesForm key={JSON.stringify(view.data)} domain={domain} state={view.data} />
      <CommandHint command={`noust app packages show ${domain}`} label={t("appPackages.fromTerminal")} />
    </Card>
  );
}
