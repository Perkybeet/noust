import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { KeyRound, Webhook } from "lucide-react";
import { useId, useState } from "react";

import { request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { appKeys, webhookDeliveriesQuery } from "../../../api/queries/apps";
import type { App, WebhookDelivery } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { DeployStatePill } from "../../../components/page/AppStatePill";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { CopyButton } from "../../../components/ui/CopyButton";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { LINK, PANEL } from "./panel";

type WebhookSecret = ResponseOf<"/api/apps/{domain}/webhook-secret", "post">;

/** How many deliveries the tab lists; the rest are on the Deployments tab. */
export const DELIVERIES_SHOWN = 10;

/** The first line of a failure, which is what fits in a row; the deployment has the rest. */
export function firstLine(text: string | null | undefined): string | null {
  const line = text
    ?.split("\n")
    .map((part) => part.trim())
    .find((part) => part !== "");
  return line ?? null;
}

/** A value the operator copies into the forge: in full, never truncated, with its copy button. */
function CopyRow({ label, value }: { label: string; value: string }) {
  const t = useT();
  return (
    <div className="grid min-w-0 gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:items-center sm:gap-4">
      <span className="text-13 text-fg-muted">{label}</span>
      <div className="flex min-w-0 items-center gap-1 rounded-control border border-border bg-bg-sunken py-1 pr-1 pl-3">
        <code translate="no" className="min-w-0 flex-1 text-12 break-all text-fg">
          {value}
        </code>
        <CopyButton value={value} label={t("appSettings.webhook.copyLabel", { label: `${label.charAt(0).toLowerCase()}${label.slice(1)}` })} />
      </div>
    </div>
  );
}

/** The secret just created, shown this once, with what to paste where in each forge. */
function NewSecret({ secret, onDone }: { secret: WebhookSecret; onDone: () => void }) {
  const t = useT();
  const titleId = useId();
  return (
    <div role="region" aria-labelledby={titleId} className="flex flex-col gap-4 rounded-control border border-border-strong bg-surface-raised p-4">
      <div className="flex items-start gap-2.5">
        <KeyRound aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
        <div className="flex flex-col gap-0.5">
          <h3 id={titleId} className="text-14 font-medium text-fg">
            {t("appSettings.webhook.copySecretNow")}
          </h3>
          <p className="text-13 text-pretty text-fg-muted">{t("appSettings.webhook.copySecretNowDescription")}</p>
        </div>
      </div>
      <div className="flex flex-col gap-2.5">
        <CopyRow label={t("appSettings.webhook.payloadUrlLabel")} value={secret.hook_url} />
        <CopyRow label={t("appSettings.webhook.secretLabel")} value={secret.secret} />
      </div>
      <div className="grid gap-4 border-t border-border pt-4 text-13 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <h4 className="font-medium text-fg">{t("appSettings.webhook.githubGitea")}</h4>
          <p className="text-pretty text-fg-muted">
            {t.rich("appSettings.webhook.githubGiteaBody", { code: <code className="text-12 text-fg">application/json</code> })}
          </p>
        </div>
        <div className="flex flex-col gap-1">
          <h4 className="font-medium text-fg">{t("appSettings.webhook.gitlab")}</h4>
          <p className="text-pretty text-fg-muted">{t("appSettings.webhook.gitlabBody")}</p>
        </div>
      </div>
      <div>
        <Button onClick={onDone}>{t("appSettings.webhook.hideSecret")}</Button>
      </div>
    </div>
  );
}

/** What a delivery without an error came to, by its deployment's status. */
function outcomeOf(t: T, status: string): string {
  if (status === "success") return t("appSettings.webhook.outcomeDeployed");
  if (status === "rolled_back") return t("appSettings.webhook.outcomeRolledBack");
  if (status === "running") return t("appSettings.webhook.outcomeDeploying");
  if (status === "queued") return t("appSettings.webhook.outcomeQueued");
  return t("appSettings.webhook.noErrorRecorded");
}

function deliveryColumns(domain: string, t: T): Column<WebhookDelivery>[] {
  return [
    {
      id: "status",
      header: t("appSettings.webhook.statusHeader"),
      cell: (row) => <DeployStatePill status={row.status} appearance="inline" size="sm" />,
      width: "w-32",
    },
    {
      id: "commit",
      header: t("appSettings.webhook.commitHeader"),
      mono: true,
      cell: (row) => (row.git_commit ? <span translate="no">{row.git_commit.slice(0, 7)}</span> : <span className="text-fg-faint">-</span>),
      width: "w-24",
    },
    {
      id: "started",
      header: t("appSettings.webhook.startedHeader"),
      cell: (row) => <RelativeTime value={row.started_at} className="text-fg-muted" />,
      width: "w-28",
      hideBelow: "sm",
    },
    {
      id: "outcome",
      header: t("appSettings.webhook.outcomeHeader"),
      cell: (row) => {
        const line = firstLine(row.error);
        return line !== null ? (
          <code translate="no" title={line} className="block max-w-[32ch] truncate text-12 text-fg-muted lg:max-w-[52ch]">
            {line}
          </code>
        ) : (
          <span className="text-fg-muted">{outcomeOf(t, row.status)}</span>
        );
      },
      hideBelow: "md",
    },
    {
      id: "deployment",
      header: t("appSettings.webhook.deploymentHeader"),
      align: "end",
      cell: (row) => (
        <Link to="/apps/$domain/deployments/$id" params={{ domain, id: String(row.deployment_id) }} className={LINK}>
          {t("appSettings.webhook.deployLink", { id: row.deployment_id })}
        </Link>
      ),
      width: "w-32",
    },
  ];
}

/**
 * Deploys started by a push. Whether a secret is set is known; the secret itself is shown once,
 * when it is created, and never again. Creating another replaces it at once, so that is asked
 * first.
 */
export function WebhookSection({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const deliveries = useQuery(webhookDeliveriesQuery(domain));
  const [secret, setSecret] = useState<WebhookSecret | null>(null);
  const [confirm, setConfirm] = useState<"regenerate" | "disable" | null>(null);
  const enabled = app.webhook_enabled;

  const settle = (next: boolean): void => {
    queryClient.setQueryData<App>(appKeys.detail(domain), (known) => (known ? { ...known, webhook_enabled: next } : known));
    void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
  };

  const create = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/webhook-secret", { params: { domain } }),
    onSuccess: (result) => {
      setSecret(result);
      setConfirm(null);
      settle(true);
      announce(t("appSettings.webhook.secretCreatedAnnounce"));
    },
  });
  const disable = useMutation({
    mutationFn: () => request("delete", "/api/apps/{domain}/webhook-secret", { params: { domain } }),
    onSuccess: () => {
      setSecret(null);
      setConfirm(null);
      settle(false);
      toast.success(t("appSettings.webhook.disabledToast", { domain }));
    },
  });

  const items = deliveries.data?.items ?? [];
  const total = deliveries.data?.total ?? 0;
  const latest = items[0];

  const status = enabled ? (
    <StatusPill state="running" label={t("appSettings.webhook.enabled")} size="sm" />
  ) : (
    <StatusPill state="stopped" label={t("appSettings.webhook.disabled")} size="sm" />
  );

  const summary = !enabled
    ? t("appSettings.webhook.refusedUntilSecret")
    : latest
      ? null
      : deliveries.isPending
        ? t("appSettings.webhook.readingDeliveries")
        : t("appSettings.webhook.noDeployYet");

  const closeConfirm = (next: boolean): void => {
    if (!next && (create.isPending || disable.isPending)) return;
    if (!next) {
      setConfirm(null);
      create.reset();
      disable.reset();
    }
  };

  return (
    <Section title={t("appSettings.webhook.title")} description={t("appSettings.webhook.description")}>
      <div className={`${PANEL} flex flex-col gap-4 px-4 py-4`}>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <Webhook aria-hidden="true" className="size-4 shrink-0 text-fg-faint" />
            {status}
            {summary !== null ? (
              <p className="text-13 text-fg-muted">{summary}</p>
            ) : latest ? (
              <p className="text-13 text-fg-muted">
                {t.rich("appSettings.webhook.lastDelivery", {
                  time: <RelativeTime value={latest.started_at} className="text-fg" />,
                  deliveries: t("appSettings.webhook.deliveryCount", { count: total }),
                })}
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {enabled ? (
              <>
                <Button variant="ghost" onClick={() => setConfirm("disable")}>
                  {t("appSettings.webhook.disableWebhook")}
                </Button>
                <Button onClick={() => setConfirm("regenerate")}>{t("appSettings.webhook.regenerateSecret")}</Button>
              </>
            ) : (
              // Secondary, like every action inside a tab: the page's one primary is Update.
              <Button loading={create.isPending} onClick={() => create.mutate()}>
                {t("appSettings.webhook.enableWebhook")}
              </Button>
            )}
          </div>
        </div>
        {create.isError && confirm === null ? (
          <ErrorBlock live compact error={create.error} title={t("appSettings.webhook.secretNotCreated")} />
        ) : null}
        {secret !== null ? <NewSecret secret={secret} onDone={() => setSecret(null)} /> : null}
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <h3 className="text-14 font-medium text-fg">{t("appSettings.webhook.recentDeliveries")}</h3>
        {deliveries.isError && deliveries.data === undefined ? (
          <ErrorBlock compact error={deliveries.error} title={t("appSettings.webhook.couldNotLoadDeliveries")} onRetry={() => void deliveries.refetch()} />
        ) : (
          <DataTable
            caption={t("appSettings.webhook.tableCaption", { domain })}
            columns={deliveryColumns(domain, t)}
            rows={items.slice(0, DELIVERIES_SHOWN)}
            getRowId={(row) => String(row.deployment_id)}
            loading={deliveries.isPending}
            density="compact"
            empty={<p className="text-13 text-fg-muted">{t("appSettings.webhook.noDeliveriesYet")}</p>}
          />
        )}
        {total > DELIVERIES_SHOWN ? (
          <p className="text-12 text-fg-muted">
            {t("appSettings.webhook.newestOf", { shown: DELIVERIES_SHOWN, total: formatCount(total) })}
            <Link to="/apps/$domain/deployments" params={{ domain }} className={LINK}>
              {t("appSettings.webhook.everyDeployLink")}
            </Link>
          </p>
        ) : null}
      </div>

      <Dialog
        open={confirm !== null}
        onOpenChange={closeConfirm}
        size="sm"
        title={confirm === "disable" ? t("appSettings.webhook.disableConfirmTitle", { domain }) : t("appSettings.webhook.regenerateConfirmTitle")}
        description={
          confirm === "disable" ? t("appSettings.webhook.disableConfirmDescription") : t("appSettings.webhook.regenerateConfirmDescription")
        }
        footer={
          <>
            <Button disabled={create.isPending || disable.isPending} onClick={() => closeConfirm(false)}>
              {t("appSettings.cancel")}
            </Button>
            {confirm === "disable" ? (
              <Button variant="danger" loading={disable.isPending} onClick={() => disable.mutate()}>
                {t("appSettings.webhook.disableWebhook")}
              </Button>
            ) : (
              <Button variant="primary" loading={create.isPending} onClick={() => create.mutate()}>
                {t("appSettings.webhook.regenerateSecret")}
              </Button>
            )}
          </>
        }
      >
        {confirm === "disable" && disable.isError ? (
          <ErrorBlock live compact error={disable.error} title={t("appSettings.webhook.webhookNotDisabled")} />
        ) : confirm === "regenerate" && create.isError ? (
          <ErrorBlock live compact error={create.error} title={t("appSettings.webhook.secretNotCreated")} />
        ) : undefined}
      </Dialog>
    </Section>
  );
}
