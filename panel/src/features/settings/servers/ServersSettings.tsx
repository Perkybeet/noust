import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowUpRight, Network, Plus, RotateCw, ShieldAlert, Trash2 } from "lucide-react";
import { useState } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section, Sections } from "../../../components/page/Section";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { describeError } from "../../../lib/errors";
import { useCentral } from "../../central/central";
import { CentralLockedNotice } from "../../central/CentralLockedNotice";
import { ServerLink } from "../../fleet/links";
import { nodeKeys, nodeStatus, nodesQuery, sshAddress, testNode } from "../../fleet/nodes";
import type { NodeRecord } from "../../fleet/nodes";
import { ReachabilityPill } from "../../fleet/ReachabilityPill";
import { AddServerDialog } from "./AddServerDialog";
import { RemoveServerDialog } from "./RemoveServerDialog";

function columnsFor(t: T, locked: boolean): readonly Column<NodeRecord>[] {
  return [
    {
      id: "name",
      header: t("servers.settings.column.name"),
      sortValue: (node) => node.name,
      cell: (node) => (
        <span translate="no" className="mono text-13 font-medium text-fg">
          {node.name}
        </span>
      ),
    },
    {
      id: "address",
      header: t("servers.settings.column.address"),
      hideBelow: "md",
      cell: (node) => (
        <span translate="no" className="mono text-12 text-fg-muted">
          {sshAddress(node)}
        </span>
      ),
    },
    {
      id: "status",
      header: t("servers.settings.column.status"),
      cell: (node) => <ReachabilityPill reachability={locked ? "locked" : nodeStatus(node)} />,
    },
    {
      id: "version",
      header: t("servers.settings.column.version"),
      hideBelow: "sm",
      cell: (node) =>
        node.version ? (
          <span translate="no" className="mono text-13 text-fg">
            {node.version}
          </span>
        ) : (
          <span className="text-13 text-fg-faint">{t("servers.fleet.notRead")}</span>
        ),
    },
    {
      id: "last-seen",
      header: t("servers.settings.column.lastSeen"),
      hideBelow: "lg",
      cell: (node) => <RelativeTime value={node.last_seen} fallback={t("servers.settings.never")} className="text-13 text-fg-muted" />,
    },
  ];
}

function RowActions({ t, node, testing, onTest, onRemove }: { t: T; node: NodeRecord; testing: boolean; onTest: () => void; onRemove: () => void }) {
  return (
    <div className="flex items-center justify-end gap-1">
      <Button size="sm" variant="ghost" aria-label={t("servers.settings.testLabel", { name: node.name })} icon={<RotateCw />} loading={testing} onClick={onTest}>
        <span className="max-sm:sr-only">{t("servers.settings.test")}</span>
      </Button>
      <ServerLink node={node.name} path="/" aria-label={t("servers.settings.openLabel", { name: node.name })} className={buttonClassName("ghost", "sm")}>
        <ArrowUpRight aria-hidden="true" />
        <span className="max-sm:sr-only">{t("servers.settings.open")}</span>
      </ServerLink>
      <Button size="sm" variant="ghost" aria-label={t("servers.settings.removeLabel", { name: node.name })} icon={<Trash2 />} onClick={onRemove}>
        <span className="max-sm:sr-only">{t("servers.settings.remove")}</span>
      </Button>
    </div>
  );
}

/** Two-factor sign-in is off: the central will refuse to add a server, so say it first. */
function TwoFactorFirst({ t }: { t: T }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-card border border-warn/40 bg-warn-soft px-4 py-3">
      <div className="flex min-w-0 items-start gap-2.5">
        <ShieldAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-13 font-medium text-fg">{t("servers.settings.twoFactor.title")}</p>
          <p className="text-13 text-pretty text-fg-muted">{t("servers.settings.twoFactor.description")}</p>
        </div>
      </div>
      <Link to="/settings/security" className={buttonClassName("secondary", "sm")}>
        {t("servers.settings.twoFactor.link")}
      </Link>
    </div>
  );
}

export interface ServersSettingsProps {
  /** The add flow is open (the URL says so, so the Fleet page can link straight to it). */
  adding?: boolean;
  onAddingChange?: (adding: boolean) => void;
}

/** Settings > Servers: the servers this central manages, and adding, testing and removing one. */
export function ServersSettings({ adding: addingProp, onAddingChange }: ServersSettingsProps) {
  const t = useT();
  useDocumentTitle(t("servers.settings.documentTitle"), 1);
  const queryClient = useQueryClient();
  const central = useCentral();
  const { data: session } = useQuery(sessionQuery());
  const query = useQuery(nodesQuery());
  const [addingState, setAddingState] = useState(false);
  const adding = addingProp ?? addingState;
  const setAdding = (next: boolean): void => {
    setAddingState(next);
    onAddingChange?.(next);
  };
  const [removing, setRemoving] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const columns = columnsFor(t, central.locked);

  const test = useMutation({
    mutationFn: (name: string) => testNode(name),
    onSuccess: (result, name) => {
      if (result.reachable) {
        toast.success(t("servers.settings.testedToast", { name, latency: result.latencyMs ?? 0 }), {
          ...(result.version !== null ? { description: t("servers.settings.testedDescription", { version: result.version }) } : {}),
        });
      } else {
        toast.error(t("servers.settings.testFailedToast", { name }), {
          ...(result.error !== null ? { description: result.error } : {}),
          ...(result.details !== null ? { detail: result.details } : {}),
        });
      }
    },
    onError: (error, name) => {
      const described = describeError(error);
      toast.error(t("servers.settings.testFailedToast", { name }), {
        ...(described.hint !== null ? { description: described.hint } : {}),
        detail: described.detail,
        ...(described.output !== null ? { output: described.output } : {}),
      });
    },
    onSettled: (_result, _error, name) => {
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["fleet", name] });
    },
  });

  const add = (
    <Button
      variant="primary"
      icon={<Plus aria-hidden="true" />}
      onClick={() => {
        setAdding(true);
      }}
    >
      {t("servers.settings.addServer")}
    </Button>
  );

  return (
    <Sections>
      {central.locked ? <CentralLockedNotice /> : null}
      {session !== undefined && !session.totp_enabled ? <TwoFactorFirst t={t} /> : null}
      <Section
        title={t("servers.settings.title")}
        description={t("servers.settings.description")}
        actions={query.data !== undefined && query.data.items.length > 0 ? add : undefined}
      >
        <QueryState
          query={query}
          label={t("servers.settings.loadingLabel")}
          skeleton={<DataTable caption={t("servers.settings.tableCaption")} columns={columns} rows={[]} getRowId={(node) => node.name} loading skeletonRows={2} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              icon={<Network />}
              title={t("servers.settings.empty.title")}
              description={t("servers.settings.empty.description")}
              action={add}
            />
          }
        >
          {(data) => (
            <DataTable
              caption={t("servers.settings.tableCaption")}
              columns={columns}
              rows={[...data.items].sort((a, b) => a.name.localeCompare(b.name))}
              getRowId={(node) => node.name}
              rowActions={(node) => (
                <RowActions
                  t={t}
                  node={node}
                  testing={test.isPending && test.variables === node.name}
                  onTest={() => {
                    test.mutate(node.name);
                  }}
                  onRemove={() => {
                    setRemoving(node.name);
                    setConfirming(true);
                  }}
                />
              )}
            />
          )}
        </QueryState>
        <CommandHint label={t("servers.settings.fromTerminal")} command="noust node list" />
      </Section>

      <AddServerDialog
        open={adding}
        onClose={() => {
          setAdding(false);
        }}
      />
      {removing !== null ? <RemoveServerDialog key={removing} name={removing} open={confirming} onOpenChange={setConfirming} /> : null}
    </Sections>
  );
}
