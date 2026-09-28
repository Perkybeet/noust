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
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusGlyph } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { reportActionError } from "../../apps/useAppActions";
import { SettingsSection } from "../SettingsForm";
import { CreateGitHubApp } from "./CreateGitHubApp";
import { ExternalAnchor } from "./ExternalAnchor";
import { accountTypeWords, hooksState, repositorySelectionWords } from "./github";

const EXPOSE_COMMAND = "wasm web expose-hooks hooks.example.com";

const INSTALLATION_COLUMNS: readonly Column<GitHubInstallation>[] = [
  {
    id: "account",
    header: "Account",
    cell: (installation) => (
      <span translate="no" className="mono text-12 text-fg">
        {installation.account}
      </span>
    ),
  },
  { id: "type", header: "Type", cell: (installation) => <Badge>{accountTypeWords(installation.account_type)}</Badge> },
  {
    id: "repositories",
    header: "Repositories",
    hideBelow: "sm",
    cell: (installation) => <span className="text-13 text-fg">{repositorySelectionWords(installation.repository_selection)}</span>,
  },
];

/** The App itself: its name, owner and page on GitHub. */
function AppFacts({ status }: { status: GitHubStatus }) {
  return (
    <div className="rounded-card border border-border bg-surface px-5 py-2 shadow-raised">
      <KeyValueList
        items={[
          { label: "App", value: status.name ?? status.slug ?? null, mono: false },
          { label: "Owner", value: status.owner ?? null },
          { label: "App ID", value: status.app_id ?? null },
          {
            label: "On GitHub",
            value: status.html_url ? <ExternalAnchor href={status.html_url}>{status.html_url.replace(/^https:\/\//, "")}</ExternalAnchor> : null,
            copy: false,
          },
        ]}
      />
    </div>
  );
}

/** The accounts the App is installed on, and the way to add or refresh them. */
function Installations({ status }: { status: GitHubStatus }) {
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: syncGitHubInstallations,
    onSuccess: (result) => {
      toast.success(
        result.total === 1 ? "Synced 1 installation from GitHub" : `Synced ${String(result.total)} installations from GitHub`,
      );
      void queryClient.invalidateQueries({ queryKey: githubKeys.all });
    },
    onError: (error) => {
      reportActionError("The installations were not synced", error);
    },
  });
  const none = status.installations.length === 0;

  return (
    <Section
      level={3}
      title="Installations"
      description="The accounts the App is installed on. It reads only the repositories each installation lets it see."
      actions={
        none ? undefined : (
          <>
            <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={sync.isPending} onClick={() => sync.mutate()}>
              Sync installations
            </Button>
            <ExternalAnchor href={status.install_url} button="secondary" size="sm">
              Install on another account
            </ExternalAnchor>
          </>
        )
      }
    >
      {none ? (
        <div className="flex min-w-0 flex-col gap-3 rounded-card border border-accent/40 bg-surface p-5 shadow-raised">
          <div className="flex flex-col gap-1">
            <p className="text-14 font-medium text-fg">Next: install the App</p>
            <p className="text-13 text-pretty text-fg-muted">
              Install it on the account or organization that holds your repositories, and choose which repositories it
              can read. GitHub brings you back here when it is done.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ExternalAnchor href={status.install_url} button="primary">
              Install on GitHub
            </ExternalAnchor>
            <Button icon={<RotateCw aria-hidden="true" />} loading={sync.isPending} onClick={() => sync.mutate()}>
              Sync installations
            </Button>
          </div>
        </div>
      ) : (
        <DataTable
          caption="GitHub App installations"
          columns={INSTALLATION_COLUMNS}
          rows={status.installations}
          getRowId={(installation) => String(installation.installation_id)}
          rowActions={(installation) => (
            <ExternalAnchor href={installation.settings_url} label={`Manage ${installation.account} on GitHub`} className="text-12">
              <span className="max-sm:sr-only">Manage</span>
            </ExternalAnchor>
          )}
        />
      )}
    </Section>
  );
}

/** Whether GitHub can deliver pushes and pull requests to this server, and what to do if not. */
function WebhookState({ status }: { status: GitHubStatus }) {
  const state = hooksState(status);
  const url = status.hooks_url ?? "";
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-5 shadow-raised">
      {state === "unexposed" ? (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="warning" className="text-warn" />
            Not reachable from GitHub
          </p>
          <p className="text-13 text-pretty text-fg-muted">
            GitHub delivers pushes and pull requests to /hooks on a public domain, and this server does not expose one
            yet. Until it does, GitHub cannot trigger deploys on push or pull request previews. Point a domain at this
            server, then run:
          </p>
          <CommandHint command={EXPOSE_COMMAND} />
        </>
      ) : state === "inactive" ? (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="warning" className="text-warn" />
            Inactive on GitHub
          </p>
          <p className="text-13 text-pretty text-fg-muted">
            GitHub does not send the App's events until its webhook is switched on. In the App's settings on GitHub, set
            the webhook URL to the address below and tick Active; then, under Permissions &amp; events, subscribe to Push
            and Pull request. Once only. The first delivery marks it active here.
          </p>
          <code translate="no" className="mono w-fit max-w-full truncate rounded-control bg-bg-sunken px-2 py-1 text-12 text-fg">
            {url}
          </code>
          <ExternalAnchor href={status.settings_url}>Open the App's settings on GitHub</ExternalAnchor>
        </>
      ) : state === "active" ? (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="running" className="text-ok" />
            Receiving events
          </p>
          <p className="text-13 text-pretty text-fg-muted">GitHub delivers the App's pushes and pull requests to:</p>
          <code translate="no" className="mono w-fit max-w-full truncate rounded-control bg-bg-sunken px-2 py-1 text-12 text-fg">
            {url}
          </code>
        </>
      ) : (
        <>
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <StatusGlyph state="stopped" className="text-idle" />
            Ready for the App
          </p>
          <p className="text-13 text-pretty text-fg-muted">
            The App will be created with this address, so GitHub delivers its pushes and pull requests here from the
            start:
          </p>
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
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const name = status.name ?? status.slug ?? "github";
  return (
    <DangerZone>
      <DangerAction
        title="Remove the GitHub App from this server"
        description="Deletes the App's private key, its webhook secret and the list of installations from this server. Applications deployed from GitHub keep running, but stop deploying on push and can no longer clone a private repository. The App stays on GitHub until you delete it there."
        action={
          <Button
            variant="danger"
            icon={<Trash2 aria-hidden="true" />}
            onClick={() => {
              setOpen(true);
            }}
          >
            Remove GitHub App
          </Button>
        }
      />
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={`Remove ${name}`}
        description={
          <>
            This server forgets the App: its private key, webhook secret and installations are deleted here. GitHub keeps
            the App until you delete it in its settings there
            {status.settings_url ? (
              <>
                {": "}
                <ExternalAnchor href={status.settings_url}>the App's settings on GitHub</ExternalAnchor>
              </>
            ) : null}
            .
          </>
        }
        confirmText={name}
        actionLabel="Remove GitHub App"
        onConfirm={async () => {
          const result = await removeGitHubApp();
          toast.success(`Removed ${name} from this server`);
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
  const query = useQuery(githubStatusQuery());
  // Removing the App leaves it on GitHub; where to delete it stays on screen until then.
  const [removedAt, setRemovedAt] = useState<string | null | undefined>(undefined);

  return (
    <>
      <SettingsSection
        title="GitHub"
        description="Deploy from private repositories, on every push and for every pull request, through a GitHub App this server owns."
        commands={["wasm github status", "wasm github installations --sync"]}
      >
        <div className="flex min-w-0 flex-col gap-6">
          {removedAt !== undefined && query.data?.configured !== true ? (
            <div role="status" className="flex min-w-0 flex-col gap-2 rounded-card border border-border bg-bg-sunken p-4">
              <p className="text-13 font-medium text-fg">The App was removed from this server. It still exists on GitHub.</p>
              <p className="text-13 text-pretty text-fg-muted">Delete it there, so no copy of its key can act for it.</p>
              <ExternalAnchor href={removedAt}>Delete the App on GitHub</ExternalAnchor>
            </div>
          ) : null}
          <QueryState query={query} label="the GitHub integration" skeleton={<GitHubSkeleton />}>
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
          title="Push and pull request events"
          description="GitHub tells this server about pushes and pull requests by calling its public hooks address."
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
