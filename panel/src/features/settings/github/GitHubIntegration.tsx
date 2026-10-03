import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useState } from "react";

import { githubKeys, githubStatusQuery, removeGitHubApp, syncGitHubInstallations } from "../../../api/queries/github";
import type { GitHubInstallation, GitHubStatus } from "../../../api/queries/github";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Stepper } from "../../../components/page/Stepper";
import { Subsection } from "../../../components/page/Subsection";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusGlyph, StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { CreateGitHubApp } from "./CreateGitHubApp";
import { accountTypeWords, hooksState, repositorySelectionWords } from "./github";

const EXPOSE_COMMAND = "noust web expose-hooks hooks.example.com";

type SetupStep = "create" | "install" | "events";

/** Where the setup stands: the first step not done yet, or null when the App works end to end. */
export function setupStep(status: GitHubStatus): SetupStep | null {
  if (!status.configured) return "create";
  if (status.installations.length === 0) return "install";
  return hooksState(status) === "active" ? null : "events";
}

function installationColumns(t: T): readonly Column<GitHubInstallation>[] {
  return [
    {
      id: "account",
      header: t("settings.integrations.github.installations.columnAccount"),
      cell: (installation) => <Mono>{installation.account}</Mono>,
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

/** Refreshes the installations from GitHub, for an App installed or changed there. */
function useSync() {
  const t = useT();
  const queryClient = useQueryClient();
  return useMutation({
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
}

/** Step 2: the App exists and is installed nowhere yet. */
function InstallStep({ status }: { status: GitHubStatus }) {
  const t = useT();
  const sync = useSync();
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.integrations.github.installations.nextDescription")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <ExternalLink href={status.install_url} button="primary">
          {t("settings.integrations.github.installations.installOnGitHub")}
        </ExternalLink>
        <Button icon={<RotateCw aria-hidden="true" />} loading={sync.isPending} onClick={() => sync.mutate()}>
          {t("settings.integrations.github.installations.syncInstallations")}
        </Button>
      </div>
    </div>
  );
}

/** Step 3: GitHub cannot deliver the App's pushes yet, and what to do about it. */
function EventsStep({ status }: { status: GitHubStatus }) {
  const t = useT();
  const state = hooksState(status);
  if (state === "unexposed") {
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.integrations.github.webhook.unexposedDescription")}</p>
        <CommandHint command={EXPOSE_COMMAND} />
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.integrations.github.webhook.inactiveDescription")}</p>
      <Mono className="w-fit max-w-full rounded-control bg-bg-sunken px-2 py-1 text-12" truncate>
        {status.hooks_url ?? ""}
      </Mono>
      <ExternalLink href={status.settings_url}>{t("settings.integrations.github.webhook.openAppSettings")}</ExternalLink>
    </div>
  );
}

/**
 * The setup, one step at a time: create the App, install it on an account, let GitHub reach
 * this server. Done steps are ticked in the stepper; only the current one has its content.
 */
function Setup({ status, step }: { status: GitHubStatus; step: SetupStep }) {
  const t = useT();
  const steps = [
    { id: "create", label: t("settings.integrations.github.steps.create") },
    { id: "install", label: t("settings.integrations.github.steps.install") },
    { id: "events", label: t("settings.integrations.github.steps.events") },
  ] as const;
  const current = steps.find((candidate) => candidate.id === step) ?? steps[0];
  return (
    <Card>
      <div className="flex min-w-0 flex-col gap-6 md:flex-row">
        <Stepper steps={steps} current={step} className="shrink-0 md:w-48" />
        <div className="flex min-w-0 flex-1 flex-col gap-3 md:border-l md:border-border md:pl-6">
          <Subsection title={current.label}>
            {step === "create" ? <CreateGitHubApp /> : step === "install" ? <InstallStep status={status} /> : <EventsStep status={status} />}
          </Subsection>
        </div>
      </div>
    </Card>
  );
}

/** The App itself: its name, owner and page on GitHub, and where it delivers. */
function AppFacts({ status }: { status: GitHubStatus }) {
  const t = useT();
  const state = hooksState(status);
  return (
    <Card padding="sm">
      <KeyValueList
        items={[
          { label: t("settings.integrations.github.appFacts.app"), value: status.name ?? status.slug ?? null, mono: false },
          { label: t("settings.integrations.github.appFacts.owner"), value: status.owner ?? null },
          {
            label: t("settings.integrations.github.appFacts.events"),
            value:
              state === "active" ? (
                <span className="inline-flex items-center gap-2 text-13 text-fg">
                  <StatusGlyph state="running" size={10} className="text-ok" />
                  {t("settings.integrations.github.webhook.activeTitle")}
                </span>
              ) : (
                <span className="inline-flex items-center gap-2 text-13 text-fg">
                  <StatusGlyph state="warning" size={10} className="text-warn" />
                  {state === "unexposed" ? t("settings.integrations.github.webhook.unexposedTitle") : t("settings.integrations.github.webhook.inactiveTitle")}
                </span>
              ),
            copy: false,
            mono: false,
            ...(state === "active" && status.hooks_url ? { hint: <Mono tone="muted" className="break-all">{status.hooks_url}</Mono> } : {}),
          },
          {
            label: t("settings.integrations.github.appFacts.onGitHub"),
            value: status.html_url ? <ExternalLink href={status.html_url}>{status.html_url.replace(/^https:\/\//, "")}</ExternalLink> : null,
            copy: false,
          },
        ]}
      />
    </Card>
  );
}

/** The accounts the App is installed on, and the way to add or refresh them. */
function Installations({ status }: { status: GitHubStatus }) {
  const t = useT();
  const sync = useSync();
  return (
    <Card
      padding="none"
      title={t("settings.integrations.github.installations.title")}
      description={t("settings.integrations.github.installations.description")}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={sync.isPending} onClick={() => sync.mutate()}>
            {t("settings.integrations.github.installations.sync")}
          </Button>
          <ExternalLink href={status.install_url} button="secondary" size="sm">
            {t("settings.integrations.github.installations.installOnAnother")}
          </ExternalLink>
        </div>
      }
    >
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
    </Card>
  );
}

/** Forgetting the App on this server: its key cannot be had back, so the name is typed. */
function RemoveApp({ status, open, onOpenChange, onRemoved }: { status: GitHubStatus; open: boolean; onOpenChange: (open: boolean) => void; onRemoved: (settingsUrl: string | null) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const name = status.name ?? status.slug ?? "github";
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("settings.integrations.github.remove.confirmTitle", { name })}
      description={t("settings.integrations.github.remove.description")}
      confirmText={name}
      actionLabel={t("settings.integrations.github.remove.action")}
      onConfirm={async () => {
        const result = await removeGitHubApp();
        toast.success(t("settings.integrations.github.remove.removedToast", { name }));
        onRemoved(result.settings_url ?? status.settings_url ?? null);
        void queryClient.invalidateQueries({ queryKey: githubKeys.all });
      }}
    />
  );
}

function GitHubSkeleton() {
  return (
    <Card padding="sm">
      <KeyValueListSkeleton rows={4} />
    </Card>
  );
}

/**
 * The App's state beside the section's title, readable at a glance: connected in the running
 * green, not connected in the stopped grey, and a setup left half done as waiting (the still,
 * dashed ring), because it waits for the operator's next step.
 */
function GitHubState({ status }: { status: GitHubStatus | undefined }) {
  const t = useT();
  if (status === undefined) return null;
  const step = setupStep(status);
  if (!status.configured) return <StatusPill state="stopped" label={t("settings.integrations.github.state.notConnected")} size="sm" />;
  if (step === null) return <StatusPill state="running" label={t("settings.integrations.github.state.connected")} size="sm" />;
  return <StatusPill state="queued" label={t("settings.integrations.github.state.settingUp")} size="sm" />;
}

/**
 * Settings > Integrations > GitHub: the state first, then what is left to set up - create this
 * server's own GitHub App, install it on the accounts that hold the repositories, let GitHub
 * deliver its events - and, once it exists, its facts and installations. Removing it is in
 * "More actions", behind the App's name. Every change goes through "Confirm it's you".
 */
export function GitHubIntegration() {
  const t = useT();
  const query = useQuery(githubStatusQuery());
  const [removing, setRemoving] = useState(false);
  // Removing the App leaves it on GitHub; where to delete it stays on screen until then.
  const [removedAt, setRemovedAt] = useState<string | null | undefined>(undefined);
  const status = query.data;

  return (
    <Section
      title={t("settings.integrations.github.title")}
      description={t("settings.integrations.github.description")}
      badge={<GitHubState status={status} />}
      actions={
        status?.configured === true ? (
          <Menu align="end" trigger={<IconButton label={t("settings.integrations.github.moreActions")} icon={<ICONS.more />} tooltip={false} />}>
            <MenuItem
              destructive
              icon={<ICONS.delete />}
              onClick={() => {
                setRemoving(true);
              }}
            >
              {t("settings.integrations.github.remove.action")}
            </MenuItem>
          </Menu>
        ) : undefined
      }
    >
      <div className="flex min-w-0 flex-col gap-4">
        {removedAt !== undefined && status?.configured !== true ? (
          <Notice title={t("settings.integrations.github.removedNotice")} live>
            <p className="text-pretty">{t("settings.integrations.github.removedHint")}</p>
            <ExternalLink href={removedAt}>{t("settings.integrations.github.deleteOnGitHub")}</ExternalLink>
          </Notice>
        ) : null}
        <QueryState query={query} label={t("settings.integrations.github.loadingLabel")} skeleton={<GitHubSkeleton />}>
          {(loaded) => {
            const step = setupStep(loaded);
            return (
              <>
                {step !== null ? <Setup status={loaded} step={step} /> : null}
                {loaded.configured ? <AppFacts status={loaded} /> : null}
                {loaded.configured && loaded.installations.length > 0 ? <Installations status={loaded} /> : null}
              </>
            );
          }}
        </QueryState>
      </div>
      {status?.configured === true ? (
        <RemoveApp
          status={status}
          open={removing}
          onOpenChange={setRemoving}
          onRemoved={(url) => {
            setRemovedAt(url);
          }}
        />
      ) : null}
    </Section>
  );
}
