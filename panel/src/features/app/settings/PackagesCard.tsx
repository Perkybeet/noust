import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { installAppPackages, appPackagesQuery, packagesKeys, putAppPackages } from "../../../api/queries/packages";
import type { AppPackages } from "../../../api/queries/packages";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { splitErrors } from "../../settings/formErrors";
import { SETTINGS_ICONS } from "./objectIcons";

/** One name per line, blank lines left out and a repeated name once: what the API takes. */
function namesOf(text: string): string[] {
  const seen = new Set<string>();
  for (const line of text.split(/[\n,\s]+/)) {
    const name = line.trim();
    if (name !== "") seen.add(name);
  }
  return [...seen];
}

/** The list as a form in a drawer with its own footer, mounted fresh for every opening. */
function PackagesDrawer({ domain, state, onClose }: { domain: string; state: AppPackages; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [text, setText] = useState(state.declared.join("\n"));
  const names = namesOf(text);
  const changed = JSON.stringify(names) !== JSON.stringify(state.declared);

  const save = useMutation({
    mutationFn: () => putAppPackages(domain, names),
    onSuccess: (result) => {
      queryClient.setQueryData(packagesKeys.forApp(domain), result);
      announce(t("appPackages.saved", { domain }));
      onClose();
    },
  });
  const split = splitErrors(save.error, ["packages"] as const);
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (changed && !save.isPending) save.mutate();
  };

  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
      title={t("appPackages.title")}
      description={t("appPackages.when")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={!changed}>
            {t("appPackages.save")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label={t("appPackages.field")}
          description={state.policy.length > 0 ? t("appPackages.fieldPolicy", { allowed: state.policy.join(", ") }) : t("appPackages.fieldDescription")}
          error={split.fields.packages}
        >
          <Textarea mono rows={8} value={text} placeholder={t("appPackages.placeholder")} onChange={(event) => setText(event.target.value)} />
        </Field>
        {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("appPackages.notSaved", { domain })} /> : null}
      </form>
    </Drawer>
  );
}

/** The packages the application declares and whether the host has each; the card's buttons edit them and install what is missing. */
function PackagesBody({ domain, state }: { domain: string; state: AppPackages }) {
  const t = useT();
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const handled = useRef<string | null>(null);
  const [editing, setEditing] = useState(false);

  const install = useMutation({
    mutationFn: () => installAppPackages(domain),
    onSuccess: (result) => {
      followed.follow(result.job as Job);
    },
  });
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

  const refused = new Set(state.refused);
  const missing = new Set(state.missing);

  return (
    <Card
      icon={SETTINGS_ICONS.packages}
      title={t("appPackages.title")}
      description={t("appPackages.description")}
      actions={
        <>
          <Button icon={<SETTINGS_ICONS.edit aria-hidden="true" />} size="sm" onClick={() => setEditing(true)}>
            {t("appPackages.edit")}
          </Button>
          <Button icon={<SETTINGS_ICONS.install aria-hidden="true" />} size="sm" loading={installing} disabled={state.missing.length === 0 || !state.supported || state.refused.length > 0} onClick={() => install.mutate()}>
            {t("appPackages.install")}
          </Button>
        </>
      }
    >
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
          <EmptyState variant="inline" title={t("appPackages.none")} />
        )}
        {state.refused.length > 0 ? (
          <Notice tone="error" title={t("appPackages.refusedTitle")}>
            {t("appPackages.refusedBody", { names: state.refused.join(", ") })}
          </Notice>
        ) : null}
        {install.isError ? <ErrorBlock live compact error={install.error} title={t("appPackages.notStarted", { domain })} /> : null}
        {jobFailed ? <ErrorBlock live compact error={{ detail: job.error ?? t("appPackages.jobFailedSilently") }} title={t("appPackages.notInstalled", { domain })} /> : null}
      </div>
      {editing ? <PackagesDrawer domain={domain} state={state} onClose={() => setEditing(false)} /> : null}
    </Card>
  );
}

/**
 * The system packages the application needs on the host (an image library, a font, a converter),
 * which Noust installs before the next build. Only an operator writes them: a repository has no
 * way to ask for one, and the server's policy (`security.app_packages`) says which may be asked.
 * The list is edited in a drawer with its own footer. The same as `noust app packages`.
 */
export function PackagesCard({ domain }: { domain: string }) {
  const t = useT();
  const view = useQuery(appPackagesQuery(domain));
  if (view.isPending) {
    return (
      <LoadingRegion label={t("appPackages.loading")}>
        <SkeletonCard lines={2} />
      </LoadingRegion>
    );
  }
  if (view.isError) {
    return (
      <Card title={t("appPackages.title")} icon={SETTINGS_ICONS.packages} description={t("appPackages.description")}>
        <ErrorBlock compact error={view.error} title={t("appPackages.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  return <PackagesBody domain={domain} state={view.data} />;
}
