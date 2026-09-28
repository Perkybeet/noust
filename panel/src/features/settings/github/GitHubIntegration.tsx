import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { githubKeys, githubStatusQuery, removeGitHubApp, syncGitHubInstallations } from "../../../api/queries/github";
import type { GitHubInstallation, GitHubStatus } from "../../../api/queries/github";
import { CommandHint } from "../../../components/page/CommandHint";
import { DangerAction, DangerZone } from "../../../components/page/DangerZone";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusGlyph } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { SettingsSection } from "../SettingsForm";
import { CreateGitHubApp } from "./CreateGitHubApp";
import { accountTypeWords, hooksState, repositorySelectionWords } from "./github";

const EXPOSE_COMMAND = "wasm web expose-hooks hooks.example.com";

function installationColumns(t: T): readonly Column<GitHubInstallation>[] {
  return [
    {
      id: "account",
      header: t("settings.integrations.github.installations.columnAccount"),
      cell: (installation) => (
        <span translate="no" className="mono text-12 text-fg">
          {installation.account}
        </span>
      ),
    },
    {
      id: "type",
      header: t("settings.integrations.github.installations.columnType"),
      cell: (installation) => <Badge>{accountTypeWords(installation.account_type, t.locale)}</Badge>,
    },
    {
      id: "repositories",
      header: t("settings.integrations.github.installations.columnRepositories"),
      hideBelow: "sm",
      cell: (installation) => <span className="text-13 text-fg">{repositorySelectionWords(installation.repository_selection, t.locale)}</span>,
    },
  ];
}

/** The App itself: its name, owner and page on GitHub. */
function AppFacts({ status }: { status: GitHubStatus }) {
  const t = useT();
  return (
    <div className="rounded-card border border-border bg-surface px-5 py-2 shadow-raised">
      <KeyValueList
        items={[
          { label: t("settings.integrations.github.appFacts.app"), value: status.name ?? status.slug ?? null, mono: false },
          { label: t("settings.integrations.github.appFacts.owner"), value: status.owner ?? null },
          { label: t("settings.integrations.github.appFacts.appId"), value: status.app_id ?? null },
          {
            label: t("settings.integrations.github.appFacts.onGitHub"),
            value: status.html_url ? <ExternalLink href={status.html_url}>{status.html_url.replace(/^https:\/\//, "")}</ExternalLink> : null,
            copy: false,
          },
        ]}
      />
    </div>
  );
}

/** The accounts the App is installed on, and the way to add or refresh them. */
function Installations({ status }: { status: GitHubStatus }) {
  const t = useT();
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: syncGitHubInstallations,
    onSuccess: (result) => {
      toast.success(
        result.total === 1
          ? t("settings.integrations.github.installations.syncedToastOne")
          : t("settings.integrations.github.installations.syncedToast", { count: result.total }),
      );
      void queryClient.invalidateQueries({ queryKey: githubKeys.all });
    },
    onError: (error) => {
      reportActionError(t("settings.integrations.github.installations.syncFailed"), error);
    },
  });
  const none = status.installations.length === 0;

  return (
    <Section
      level={3}
      title={t("settings.integrations.github.installations.title")}
      description={t("settings.integrations.github.installations.description")}
      actions={
        none ? undefined : (
          <>
            <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={sync.isPending} onClick={() => sync.mutate()}>
              {t("settings.integrations.github.installations.syncInstallations")}
            </Button>
            <ExternalLink href={status.install_url} button="secondary" size="sm">
              {t("settings.integrations.github.installations.installOnAnother")}
            </ExternalLink>
          </>
        )
      }
    >
      {none ? (
        <div className="flex min-w-0 flex-col gap-3 rounded-card border border-accent/40 bg-surface p-5 shadow-raised">
          <div className="flex flex-col gap-1">
            <p className="text-14 font-medium text-fg">{t("settings.integrations.github.installations.nextTitle")}</p>
            <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.github.installations.nextDescription")}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ExternalLink href={status.install_url} button="primary">
              {t("settings.integrations.github.installations.installOnGitHub")}
            </ExternalLink>
            <Button icon={<RotateCw aria-hidden="true" />} loading={sync.isPending} onClick={() => sync.mutate()}>
              {t("settings.integrations.github.installations.syncInstallations")}
            </Button>
          </div>
        </div>
      ) : (
        <DataTable
          caption={t("settings.integrations.github.installations.tableCaption")}
          columns={installationColumns(t)}
          rows={status.installations}
          getRowId={(installation) => String(installation.installation_id)}
          rowActions={(installation) => (
            <ExternalLink
              href={installation.settings_url}
              label={t("settings.integrations.github.installations.manageLabel", { account: installation.account })}
              className="text-12"
            >
              <span className="max-sm:sr-only">{t("settings.integrations.github.installations.manage")}</span>
            </ExternalLink>
          )}
        />
      )}
    </Section>
  );
}

/** Whether GitHub can deliver pushes and pull requests to this server, and what to do if not. */
function WebhookState({ status }: { status: GitHubStatus }) {
  const t = useT();
  const state = hooksState(status);
  const url = status.hooks_url ?? "";
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-5 shadow-raised">
      {state === "unexposed" ? (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="warning" className="text-warn" />
            {t("settings.integrations.github.webhook.unexposedTitle")}
          </p>
          <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.github.webhook.unexposedDescription")}</p>
          <CommandHint command={EXPOSE_COMMAND} />
        </>
      ) : state === "inactive" ? (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="warning" className="text-warn" />
            {t("settings.integrations.github.webhook.inactiveTitle")}
          </p>
          <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.github.webhook.inactiveDescription")}</p>
          <code translate="no" className="mono w-fit max-w-full truncate rounded-control bg-bg-sunken px-2 py-1 text-12 text-fg">
            {url}
          </code>
          <ExternalLink href={status.settings_url}>{t("settings.integrations.github.webhook.openAppSettings")}</ExternalLink>
        </>
      ) : state === "active" ? (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="running" className="text-ok" />
            {t("settings.integrations.github.webhook.activeTitle")}
          </p>
          <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.github.webhook.activeDescription")}</p>
          <code translate="no" className="mono w-fit max-w-full truncate rounded-control bg-bg-sunken px-2 py-1 text-12 text-fg">
            {url}
          </code>
        </>
      ) : (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="stopped" className="text-idle" />
            {t("settings.integrations.github.webhook.readyTitle")}
          </p>
          <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.github.webhook.readyDescription")}</p>
          <code translate="no" className="mono w-fit max-w-full truncate rounded-control bg-bg-sunken px-2 py-1 text-12 text-fg">
            {url}
          </code>
        </>
      )}
    </div>
  );
}

/** Forgetting the App on this server, with where to delete it on GitHub. */
function RemoveApp({ status, onRemoved }: { status: GitHubStatus; onRemoved: (settingsUrl: string | null) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const name = status.name ?? status.slug ?? "github";
  return (
    <DangerZone>
      <DangerAction
        title={t("settings.integrations.github.remove.title")}
        description={t("settings.integrations.github.remove.description")}
        action={
          <Button
            variant="danger"
            icon={<Trash2 aria-hidden="true" />}
            onClick={() => {
              setOpen(true);
            }}
          >
            {t("settings.integrations.github.remove.action")}
          </Button>
        }
      />
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t("settings.integrations.github.remove.confirmTitle", { name })}
        description={
          <>
            {t("settings.integrations.github.remove.confirmDescription")}
            {status.settings_url ? (
              <>
                {": "}
                <ExternalLink href={status.settings_url}>{t("settings.integrations.github.remove.confirmDescriptionLink")}</ExternalLink>
              </>
            ) : null}
            .
          </>
        }
        confirmText={name}
        actionLabel={t("settings.integrations.github.remove.action")}
        onConfirm={async () => {
          const result = await removeGitHubApp();
          toast.success(t("settings.integrations.github.remove.removedToast", { name }));
          onRemoved(result.settings_url ?? status.settings_url ?? null);
          void queryClient.invalidateQueries({ queryKey: githubKeys.all });
        }}
      />
    </DangerZone>
  );
}

function GitHubSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-card border border-border bg-surface px-5 py-2 shadow-raised">
        <KeyValueListSkeleton rows={4} />
      </div>
      <Skeleton className="h-24" />
    </div>
  );
}

/**
 * Settings > Integrations > GitHub: create this server's own GitHub App, install it on the
 * accounts that hold the repositories, see whether GitHub can deliver its events, and remove
 * it. Every change goes through "Confirm it's you".
 */
export function GitHubIntegration() {
  const t = useT();
  const query = useQuery(githubStatusQuery());
  // Removing the App leaves it on GitHub; where to delete it stays on screen until then.
  const [removedAt, setRemovedAt] = useState<string | null | undefined>(undefined);

  return (
    <>
      <SettingsSection
        title={t("settings.integrations.github.title")}
        description={t("settings.integrations.github.description")}
        commands={["wasm github status", "wasm github installations --sync"]}
      >
        <div className="flex min-w-0 flex-col gap-6">
          {removedAt !== undefined && query.data?.configured !== true ? (
            <div role="status" className="flex min-w-0 flex-col gap-2 rounded-card border border-border bg-bg-sunken p-4">
              <p className="text-13 font-medium text-fg">{t("settings.integrations.github.removedNotice")}</p>
              <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.github.removedHint")}</p>
              <ExternalLink href={removedAt}>{t("settings.integrations.github.deleteOnGitHub")}</ExternalLink>
            </div>
          ) : null}
          <QueryState query={query} label={t("settings.integrations.github.loadingLabel")} skeleton={<GitHubSkeleton />}>
            {(status) =>
              status.configured ? (
                <div className="flex min-w-0 flex-col gap-6">
                  <AppFacts status={status} />
                  <Installations status={status} />
                </div>
              ) : (
                <CreateGitHubApp />
              )
            }
          </QueryState>
        </div>
      </SettingsSection>

      {query.data !== undefined ? (
        <SettingsSection
          title={t("settings.integrations.github.webhook.title")}
          description={t("settings.integrations.github.webhook.description")}
          commands={["wasm web status"]}
        >
          <WebhookState status={query.data} />
        </SettingsSection>
      ) : null}

      {query.data?.configured === true ? (
        <RemoveApp
          status={query.data}
          onRemoved={(url) => {
            setRemovedAt(url);
          }}
        />
      ) : null}
    </>
  );
}
