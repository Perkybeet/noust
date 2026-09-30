import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowUpRight, Network, Plus, RotateCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section, Sections } from "../../../components/page/Section";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { describeError } from "../../../lib/errors";
import { useCentral } from "../../central/central";
import { CentralLockedNotice } from "../../central/CentralLockedNotice";
import { accessOf, fleetKeys, fleetViewQuery, originOf } from "../../fleet/data";
import type { AccessCeiling } from "../../fleet/data";
import { nodeKeys, nodeStatus, nodesQuery, sshAddress, testNode } from "../../fleet/nodes";
import type { NodeRecord } from "../../fleet/nodes";
import { ReachabilityPill } from "../../fleet/ReachabilityPill";
import { AddServerDialog } from "./AddServerDialog";
import { RemoveServerDialog } from "./RemoveServerDialog";

function columnsFor(t: T, locked: boolean, access: ReadonlyMap<string, AccessCeiling | null>): readonly Column<NodeRecord>[] {
  return [
    {
      id: "name",
      header: t("servers.settings.column.name"),
      sortValue: (node) => node.name,
      cell: (node) => <Mono className="font-medium">{node.name}</Mono>,
    },
    {
      id: "address",
      header: t("servers.settings.column.address"),
      hideBelow: "md",
      cell: (node) => <Mono tone="muted">{sshAddress(node)}</Mono>,
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
      cell: (node) => (node.version ? <Mono>{node.version}</Mono> : <EmptyCell reason={t("servers.fleet.notRead")} />),
    },
    {
      id: "access",
      header: t("servers.settings.column.access"),
      hideBelow: "md",
      cell: (node) => {
        const ceiling = access.get(node.name) ?? null;
        return ceiling === null ? <EmptyCell reason={t("fleet.access.unknown")} /> : <span className="text-13 text-fg">{t(`fleet.access.level.${ceiling.level}`)}</span>;
      },
    },
    {
      id: "last-seen",
      header: t("servers.settings.column.lastSeen"),
      hideBelow: "lg",
      cell: (node) => <RelativeTime value={node.last_seen} fallback={t("servers.settings.never")} className="text-13 text-fg-muted" />,
    },
  ];
}

/** A server's actions, in its row's menu: test the tunnel, open it, remove it from the fleet. */
function RowActions({ t, node, testing, onTest, onRemove }: { t: T; node: NodeRecord; testing: boolean; onTest: () => void; onRemove: () => void }) {
  const navigate = useNavigate();
  return (
    <Menu align="end" trigger={<IconButton label={t("fleet.servers.actionsFor", { name: node.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      <MenuItem icon={<RotateCw />} disabled={testing} onClick={onTest}>
        {t("fleet.servers.test")}
      </MenuItem>
      <MenuItem icon={<ArrowUpRight />} onClick={() => void navigate({ to: "/", search: { node: node.name } })}>
        {t("servers.settings.openLabel", { name: node.name })}
      </MenuItem>
      <MenuSeparator />
      <MenuItem icon={<Trash2 />} destructive onClick={onRemove}>
        {t("fleet.servers.remove")}
      </MenuItem>
    </Menu>
  );
}

/** Two-factor sign-in is off: the central will refuse to add a server, so say it first. */
function TwoFactorFirst({ t }: { t: T }) {
  return (
    <Notice
      tone="warning"
      variant="banner"
      title={t("servers.settings.twoFactor.title")}
      action={
        <Link to="/settings/security" className={buttonClassName("secondary", "sm")}>
          {t("servers.settings.twoFactor.link")}
        </Link>
      }
    >
      {t("servers.settings.twoFactor.description")}
    </Notice>
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
  // What each server lets this central do there, as it last published it (the fleet's own read).
  const fleet = useQuery({ ...fleetViewQuery("servers"), enabled: (query.data?.items.length ?? 0) > 0 });
  const access = new Map((fleet.data?.items ?? []).map((row) => [originOf(row).node, accessOf(row)]));
  const [addingState, setAddingState] = useState(false);
  const adding = addingProp ?? addingState;
  const setAdding = (next: boolean): void => {
    setAddingState(next);
    onAddingChange?.(next);
  };
  const [removing, setRemoving] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const columns = columnsFor(t, central.locked, access);

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
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
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
