import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useState } from "react";

import { githubKeys, githubStatusQuery, removeGitHubApp, syncGitHubInstallations } from "../../../api/queries/github";
import type { GitHubInstallation, GitHubStatus } from "../../../api/queries/github";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { RowMenu } from "../../../components/page/RowMenu";
import { Stepper } from "../../../components/page/Stepper";
import { Subsection } from "../../../components/page/Subsection";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Can, useCan } from "../../../components/page/permissions";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { ResourceCard } from "../../../components/ui/ResourceCard";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { CreateGitHubApp } from "./CreateGitHubApp";
import { GitHubHost, statusHost } from "./GitHubHost";
import { accountTypeWords, hooksState, repositorySelectionWords } from "./github";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

const EXPOSE_COMMAND = "noust web expose-hooks hooks.example.com";

/** What every change of the App takes (the server's, `routes_integrations.py`). */
const MANAGE = "apps.manage";

type SetupStep = "create" | "install";

/**
 * Where the setup stands: the first step not done yet, or null when the App can clone. An App that
 * is created and installed already reads every repository it reaches; letting GitHub deliver its
 * pushes comes after, as a task with a state (`EventsTask`), and blocks nothing.
 */
export function setupStep(status: GitHubStatus): SetupStep | null {
  if (!status.configured) return "create";
  return status.installations.length === 0 ? "install" : null;
}

function installationColumns(t: T): readonly Column<GitHubInstallation>[] {
  return [
    {
      id: "account",
      header: t("settings.integrations.github.installations.columnAccount"),
      // The kind of account is a tag of its name, on its line.
      cell: (installation) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono>{installation.account}</Mono>
          <Badge>{accountTypeWords(installation.account_type, t.locale)}</Badge>
        </span>
      ),
    },
    {
      id: "repositories",
      header: t("settings.integrations.github.installations.columnRepositories"),
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
        {/* design-exception: write-without-permission the step is drawn only for apps.manage, in the section that holds it */}
        <Button icon={<RotateCw aria-hidden="true" />} loading={sync.isPending} onClick={() => sync.mutate()}>
          {t("settings.integrations.github.installations.syncInstallations")}
        </Button>
      </div>
    </div>
  );
}

/** GitHub cannot deliver the App's pushes yet, and what to do about it. */
function EventsHelp({ status }: { status: GitHubStatus }) {
  const t = useT();
  const state = hooksState(status);
  if (state === "unexposed") {
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.integrations.github.webhook.unexposedDescription")}</p>
        {/* The console cannot expose a domain: the terminal does, on this machine. */}
        <CommandHint command={EXPOSE_COMMAND} reason="solo-terminal" />
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
 * The task that comes after the App works: let GitHub reach this server, so a push deploys and a
 * pull request gets a preview. It says what does not happen until then, and how; the App clones
 * and lists repositories without it.
 */
function EventsTask({ status }: { status: GitHubStatus }) {
  const t = useT();
  return (
    <Subsection title={t("settings.integrations.github.steps.events")}>
      <StatusPill state="warning" label={t("settings.integrations.github.webhook.pushesWaiting")} size="sm" className="self-start" />
      <EventsHelp status={status} />
    </Subsection>
  );
}

/**
 * The setup, one step at a time: create the App, install it on an account. Done steps are
 * ticked in the stepper; only the current one has its content.
 */
function Setup({ status, step }: { status: GitHubStatus; step: SetupStep }) {
  const t = useT();
  const steps = [
    { id: "create", label: t("settings.integrations.github.steps.create") },
    { id: "install", label: t("settings.integrations.github.steps.install") },
  ] as const;
  const current = steps.find((candidate) => candidate.id === step) ?? steps[0];
  return (
    <div className="flex min-w-0 flex-col gap-6 md:flex-row">
      <Stepper steps={steps} current={step} className="shrink-0 md:w-48" />
      <div className="flex min-w-0 flex-1 flex-col gap-3 md:border-l md:border-border md:pl-6">
        <Subsection title={current.label}>
          {step === "create" ? (
            <>
              <GitHubHost key={statusHost(status)} status={status} />
              <CreateGitHubApp />
            </>
          ) : (
            <InstallStep status={status} />
          )}
        </Subsection>
      </div>
    </div>
  );
}

/**
 * The App itself, as a card: its name, whether GitHub delivers its pushes, who owns it and on which GitHub, its page
 * there, and what can be done with it (sync, remove). The actions are the role's: a menu drawn only for who manages
 * applications.
 */
function AppCard({ status, onRemove }: { status: GitHubStatus; onRemove: () => void }) {
  const t = useT();
  const sync = useSync();
  const name = status.name ?? status.slug ?? "GitHub";
  const state = hooksState(status);
  return (
    <ResourceCard
      name={name}
      untranslated
      icon={OBJECT_ICONS.integration}
      status={
        state === "active" ? (
          <StatusPill state="running" label={t("settings.integrations.github.webhook.activeTitle")} size="sm" />
        ) : (
          <StatusPill state="warning" label={state === "unexposed" ? t("settings.integrations.github.webhook.unexposedTitle") : t("settings.integrations.github.webhook.inactiveTitle")} size="sm" />
        )
      }
      facts={[
        ...(status.owner ? [{ label: t("settings.integrations.github.appFacts.owner"), value: <Mono>{status.owner}</Mono> }] : []),
        {
          label: t("githubHost.fact"),
          value: (
            <>
              <Mono>{statusHost(status)}</Mono>
              <span className="block text-12 text-fg-muted">{t("githubHost.factDescription")}</span>
            </>
          ),
        },
      ]}
      actions={
        <Can permission={MANAGE}>
          <RowMenu name={name}>
            {/* design-exception: write-without-permission the menu is drawn only for apps.manage, by Can */}
            <MenuItem icon={<RotateCw />} disabled={sync.isPending} onClick={() => sync.mutate()}>
              {t("settings.integrations.github.installations.sync")}
            </MenuItem>
            <MenuItem destructive icon={<ICONS.delete />} onClick={onRemove}>
              {t("settings.integrations.github.remove.action")}
            </MenuItem>
          </RowMenu>
        </Can>
      }
    >
      {status.html_url ? <ExternalLink href={status.html_url}>{status.html_url.replace(/^https:\/\//, "")}</ExternalLink> : null}
    </ResourceCard>
  );
}

/** The accounts the App is installed on, and the way to add or refresh them. */
function Installations({ status }: { status: GitHubStatus }) {
  const t = useT();
  const canManage = useCan(MANAGE);
  return (
    <Card
      padding="none"
      level={3}
      title={t("settings.integrations.github.installations.title")}
      description={t("settings.integrations.github.installations.description")}
      actions={
        canManage ? (
          <ExternalLink href={status.install_url} button="secondary" size="sm">
            {t("settings.integrations.github.installations.installOnAnother")}
          </ExternalLink>
        ) : undefined
      }
      className="overflow-hidden"
    >
      <DataTable
        embedded
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
    <ConfirmDialog friction="type"
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
 * Settings > Integrations > GitHub: a slab with the state beside its title, then what is left to set up - create this
 * server's own GitHub App, install it on the accounts that hold the repositories, let GitHub deliver its events - and,
 * once it exists, the App as a card beside where it is installed. Removing it is in the card's menu, behind its name.
 * Every change goes through "Confirm it's you".
 */
export function GitHubIntegration() {
  const t = useT();
  const canManage = useCan(MANAGE);
  const query = useQuery(githubStatusQuery());
  const [removing, setRemoving] = useState(false);
  // Removing the App leaves it on GitHub; where to delete it stays on screen until then.
  const [removedAt, setRemovedAt] = useState<string | null | undefined>(undefined);
  const status = query.data;

  return (
    <SurfaceSection
      title={t("settings.integrations.github.title")}
      icon={OBJECT_ICONS.source}
      status={<GitHubState status={status} />}
      description={t("settings.integrations.github.description")}
    >
      <div className="flex min-w-0 flex-col gap-4">
        {removedAt !== undefined && status?.configured !== true ? (
          <Notice title={t("settings.integrations.github.removedNotice")} live>
            <p className="text-pretty">{t("settings.integrations.github.removedHint")}</p>
            <ExternalLink href={removedAt}>{t("settings.integrations.github.deleteOnGitHub")}</ExternalLink>
          </Notice>
        ) : null}
        <QueryState query={query} label={t("settings.integrations.github.loadingLabel")} skeleton={<Skeleton className="h-32 w-full rounded-card" />}>
          {(loaded) => {
            const step = setupStep(loaded);
            return (
              <>
                {step !== null ? <Setup status={loaded} step={step} /> : null}
                {step === null && hooksState(loaded) !== "active" ? <EventsTask status={loaded} /> : null}
                {loaded.configured ? (
                  <div className="flex min-w-0 flex-col gap-4">
                    <AppCard status={loaded} onRemove={() => setRemoving(true)} />
                    {loaded.installations.length > 0 ? <Installations status={loaded} /> : null}
                  </div>
                ) : null}
              </>
            );
          }}
        </QueryState>
      </div>
      {status?.configured === true && canManage ? (
        <RemoveApp
          status={status}
          open={removing}
          onOpenChange={setRemoving}
          onRemoved={(url) => {
            setRemovedAt(url);
          }}
        />
      ) : null}
    </SurfaceSection>
  );
}
