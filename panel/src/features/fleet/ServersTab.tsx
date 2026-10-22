import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { BellOff, BellRing, Cable, Download, FileUp, KeyRound, Link2, PanelTop, Play, RotateCw, Tags, Trash2, UsersRound, X } from "lucide-react";
import { useMemo, useState } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { RowMenu } from "../../components/page/RowMenu";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { downloadText } from "../../lib/clipboard";
import { describeError } from "../../lib/errors";
import { MonitoringNote } from "../settings/servers/MonitoringNote";
import { PauseMonitoringDialog } from "../settings/servers/PauseMonitoringDialog";
import { RemoveServerDialog } from "../settings/servers/RemoveServerDialog";
import { nodeKeys, nodesQuery, setNodeMonitoring, testNode } from "./nodes";
import type { NodeMonitoring } from "./nodes";
import { useFleetActions } from "./BulkActionDialog";
import { accessOf, fleetKeys, formatLabels, labelsOf, noustOf, originOf, outcomeKind } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { LabelsDialog } from "./LabelsDialog";
import { NodeTeamDialog, RekeyDialog } from "./NodeAccessDialogs";
import { ConnectionDialog, InventoryDialog, LinkMachineDialog } from "./NodeToolsDialogs";
import { fetchInventory } from "./nodeTools";
import { OutcomePill, PartialNotice, ServerCell, StateFilter, useFleetView } from "./parts";

/** The Servers view's own states, for its filter. */
const SERVER_STATES = ["answering", "down", "older"] as const;

function stateOf(outcome: NodeOutcome | undefined): (typeof SERVER_STATES)[number] | null {
  if (outcome === undefined) return null;
  const kind = outcomeKind(outcome);
  if (kind === "unsupported") return "older";
  if (kind === "unreachable" || kind === "stale" || kind === "error") return "down";
  return "answering";
}

/** What the central may do on a server, in words, and whether it may also change how it is reached. */
function AccessCell({ t, row }: { t: T; row: FleetRow }) {
  const origin = originOf(row);
  if (origin.local) return <EmptyCell reason={t("fleet.access.thisCentral")} />;
  const access = accessOf(row);
  if (access === null) return <EmptyCell reason={t("fleet.access.unknown")} />;
  return (
    <span className="flex min-w-0 flex-col">
      <span className="text-13 text-fg">{t(`fleet.access.level.${access.level}`)}</span>
      <span className="text-12 text-fg-muted">{access.hostAccess ? t("fleet.access.hostAccessOn") : t("fleet.access.hostAccessOff")}</span>
    </span>
  );
}

interface RowHandlers {
  selected: ReadonlySet<string>;
  onSelect: (node: string, checked: boolean) => void;
  /** How long each server has been silent, or that its monitoring is paused. */
  monitoring: ReadonlyMap<string, NodeMonitoring | null>;
}

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>, handlers: RowHandlers): Column<FleetRow>[] {
  return [
    {
      id: "select",
      header: t("fleet.servers.select"),
      width: "w-12",
      // On a phone too, in a slot of its own: choosing servers for an action is this view's job.
      card: "control",
      cell: (row) => {
        const origin = originOf(row);
        // A bulk action runs on the central's servers, never on the central itself.
        if (origin.local) return null;
        return (
          <Checkbox
            aria-label={t("fleet.servers.selectOne", { name: origin.node })}
            checked={handlers.selected.has(origin.node)}
            onCheckedChange={(checked) => {
              handlers.onSelect(origin.node, checked);
            }}
          />
        );
      },
    },
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "title",
      sortValue: (row) => (originOf(row).local ? "" : originOf(row).node),
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
    {
      id: "state",
      header: t("fleet.column.state"),
      width: "w-40",
      card: "status",
      cell: (row) => {
        const outcome = outcomes.get(originOf(row).node);
        if (outcome === undefined) return <EmptyCell reason={t("fleet.summary.notRead")} />;
        return (
          <span className="flex min-w-0 flex-col items-start gap-1">
            <OutcomePill outcome={outcome} />
            <MonitoringNote monitoring={handlers.monitoring.get(originOf(row).node) ?? null} />
          </span>
        );
      },
    },
    {
      id: "version",
      header: t("fleet.column.version"),
      card: "meta",
      cell: (row) => {
        const noust = noustOf(row);
        if (noust.current === null) return <EmptyCell reason={t("fleet.summary.notRead")} />;
        return (
          <span className="flex flex-wrap items-center gap-1.5">
            <Mono>{noust.current}</Mono>
            {noust.state === "update_available" && noust.latest !== null ? <Badge>{t("fleet.servers.updateAvailable", { version: noust.latest })}</Badge> : null}
          </span>
        );
      },
    },
    {
      id: "labels",
      header: t("fleet.column.labels"),
      hideBelow: "md",
      // A phone hides the column as a narrow table would: the labels are edited from the menu.
      card: "hidden",
      cell: (row) => {
        const labels = labelsOf(row);
        if (originOf(row).local) return <EmptyCell reason={t("fleet.labels.notForCentral")} />;
        if (labels.length === 0) return <span className="text-13 text-fg-muted">{t("fleet.labels.none")}</span>;
        return (
          <span className="flex flex-wrap gap-1">
            {labels.map(([key, value]) => (
              <Badge key={key} mono>{`${key}=${value}`}</Badge>
            ))}
          </span>
        );
      },
    },
    {
      id: "access",
      header: t("fleet.column.access"),
      hideBelow: "lg",
      card: "hidden",
      cell: (row) => <AccessCell t={t} row={row} />,
    },
    {
      id: "last-seen",
      header: t("fleet.column.lastSeen"),
      hideBelow: "md",
      card: "meta",
      cell: (row) =>
        originOf(row).local ? (
          <span className="text-13 text-fg-muted">{t("fleet.servers.now")}</span>
        ) : (
          <RelativeTime value={typeof row["last_seen"] === "string" ? row["last_seen"] : null} fallback={t("fleet.servers.never")} className="text-13 text-fg-muted" />
        ),
    },
  ];
}

type Tool = "connection" | "inventory" | "link" | "rekey" | "team";

function ServerMenu({
  t,
  row,
  paused,
  onLabels,
  onRemove,
  onTool,
  onPause,
  onResume,
}: {
  t: T;
  row: FleetRow;
  /** Its monitoring is paused for maintenance. */
  paused: boolean;
  onLabels: () => void;
  onRemove: () => void;
  onTool: (tool: Tool) => void;
  onPause: () => void;
  onResume: () => void;
}) {
  const origin = originOf(row);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const actions = useFleetActions();
  const test = useMutation({
    mutationFn: () => testNode(origin.node),
    onSuccess: (result) => {
      if (result.reachable) {
        toast.success(t("servers.settings.testedToast", { name: origin.node, latency: result.latencyMs ?? 0 }), {
          ...(result.version !== null ? { description: t("servers.settings.testedDescription", { version: result.version }) } : {}),
        });
      } else {
        toast.error(t("servers.settings.testFailedToast", { name: origin.node }), {
          ...(result.error !== null ? { description: result.error } : {}),
          ...(result.details !== null ? { detail: result.details } : {}),
        });
      }
    },
    onError: (error) => {
      const described = describeError(error);
      toast.error(t("servers.settings.testFailedToast", { name: origin.node }), {
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
  const exportInventory = useMutation({
    mutationFn: () => fetchInventory(origin.node),
    onSuccess: (document) => {
      downloadText(`${origin.node}.inventory.json`, `${JSON.stringify(document, null, 2)}\n`, "application/json");
      toast.success(t("nodeTools.inventory.exportDone", { name: origin.node }));
    },
    onError: (error) => {
      const described = describeError(error);
      toast.error(t("nodeTools.inventory.exportFailed", { name: origin.node }), {
        ...(described.hint !== null ? { description: described.hint } : {}),
        detail: described.detail,
      });
    },
  });
  return (
    <RowMenu name={origin.node}>
      <MenuItem icon={<PanelTop />} onClick={() => void navigate({ to: "/", search: { node: origin.local ? undefined : origin.node } })}>
        {t("fleet.servers.open")}
      </MenuItem>
      {origin.local ? null : (
        <>
          <MenuItem icon={<RotateCw />} disabled={test.isPending} onClick={() => test.mutate()}>
            {t("fleet.servers.test")}
          </MenuItem>
          <MenuItem icon={<Tags />} onClick={onLabels}>
            {t("fleet.servers.editLabels")}
          </MenuItem>
          {paused ? (
            <MenuItem icon={<BellRing />} onClick={onResume}>
              {t("servers.settings.monitoring.resume")}
            </MenuItem>
          ) : (
            <MenuItem icon={<BellOff />} onClick={onPause}>
              {t("servers.settings.monitoring.pause")}
            </MenuItem>
          )}
          <MenuItem
            icon={<Play />}
            onClick={() => {
              actions.open({ nodes: [origin.node] });
            }}
          >
            {t("fleet.servers.runOn")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem
            icon={<Cable />}
            onClick={() => {
              onTool("connection");
            }}
          >
            {t("nodeTools.menu.connection")}
          </MenuItem>
          <MenuItem
            icon={<Link2 />}
            onClick={() => {
              onTool("link");
            }}
          >
            {t("nodeTools.menu.linkMachine")}
          </MenuItem>
          <MenuItem
            icon={<KeyRound />}
            onClick={() => {
              onTool("rekey");
            }}
          >
            {t("nodeTools.menu.rotateKey")}
          </MenuItem>
          <MenuItem
            icon={<UsersRound />}
            onClick={() => {
              onTool("team");
            }}
          >
            {t("nodeTools.menu.assignTeam")}
          </MenuItem>
          <MenuItem icon={<Download />} disabled={exportInventory.isPending} onClick={() => exportInventory.mutate()}>
            {t("nodeTools.menu.exportInventory")}
          </MenuItem>
          <MenuItem
            icon={<FileUp />}
            onClick={() => {
              onTool("inventory");
            }}
          >
            {t("nodeTools.menu.applyInventory")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={<Trash2 />} destructive onClick={onRemove}>
            {t("fleet.servers.remove")}
          </MenuItem>
        </>
      )}
    </RowMenu>
  );
}

export interface ServersTabProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every server of the fleet (T1): whether it answers, which Noust it runs, its labels, what it
 * lets this central do there, and when it last answered. Servers are chosen here, by hand, for
 * a bulk action; their labels, which aim an action at a group, are edited here too.
 */
export function ServersTab({ search, onSearchChange }: ServersTabProps) {
  const t = useT();
  const view = useFleetView("servers");
  const actions = useFleetActions();
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [labelling, setLabelling] = useState<FleetRow | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [tool, setTool] = useState<{ kind: Tool; node: string } | null>(null);
  const [pausing, setPausing] = useState<string | null>(null);
  const queryClient = useQueryClient();
  // The monitoring state is the servers list's own (`/api/nodes`); the fleet view answers for the rest.
  const nodes = useQuery(nodesQuery());
  const { data: session } = useQuery(sessionQuery());
  const monitoring = useMemo(() => new Map((nodes.data?.items ?? []).map((node) => [node.name, node.monitoring ?? null] as const)), [nodes.data]);
  const resume = useMutation({
    mutationFn: (name: string) => setNodeMonitoring(name, false),
    // No success toast: the note under the server goes and the menu says "Pause monitoring" again.
    onError: (error, name) => {
      const described = describeError(error);
      toast.error(t("servers.settings.monitoring.resumeFailedToast", { name }), { ...(described.hint !== null ? { description: described.hint } : {}), detail: described.detail });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
    },
  });

  const rows = useMemo(() => view.data?.items ?? [], [view.data]);
  const shown = rows.filter((row) => {
    const origin = originOf(row);
    if (search.state !== undefined && stateOf(view.outcomes.get(origin.node)) !== search.state) return false;
    return matchesQuery([origin.node, formatLabels(labelsOf(row)), noustOf(row).current], search.q);
  });
  const chosen = [...selected].filter((node) => rows.some((row) => originOf(row).node === node));

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {session !== undefined && !session.totp_enabled ? (
        // The central refuses to add a server while anyone can sign in without a second factor: said before it is tried.
        <InlineMessage
          trailing={
            <TextLink to="/settings/security" size="ui">
              {t("servers.settings.twoFactor.link")}
            </TextLink>
          }
        >
          {t("servers.settings.twoFactor.title")}
        </InlineMessage>
      ) : null}
      <PartialNotice view={view.data} />
      <FilterBar
        label={t("fleet.servers.filterLabel")}
        search={{
          value: search.q ?? "",
          onChange: (value) => {
            set({ q: value }, true);
          },
          label: t("fleet.servers.searchLabel"),
          placeholder: t("fleet.servers.searchPlaceholder"),
        }}
        filters={
          <StateFilter
            value={search.state}
            onChange={(state) => {
              set({ state });
            }}
            options={SERVER_STATES.map((state) => ({ value: state, label: t(`fleet.servers.state.${state}`) }))}
          />
        }
        count={view.data === undefined ? "" : isFiltered(search) ? t("fleet.servers.countFiltered", { shown: shown.length, total: rows.length }) : t("fleet.servers.count", { count: rows.length })}
        actions={
          <>
            {chosen.length > 0 ? (
              <Button
                variant="ghost"
                icon={<X aria-hidden="true" />}
                onClick={() => {
                  setSelected(new Set());
                }}
              >
                {t("fleet.servers.clearSelection")}
              </Button>
            ) : null}
            <Button
              icon={<Play aria-hidden="true" />}
              onClick={() => {
                actions.open(chosen.length > 0 ? { nodes: chosen } : {});
              }}
            >
              {chosen.length > 0 ? t("fleet.servers.runOnSelected", { count: chosen.length }) : t("fleet.page.runAction")}
            </Button>
          </>
        }
      />
      <DataTable
        caption={t("fleet.servers.caption")}
        columns={columns(t, view.outcomes, {
          monitoring,
          selected,
          onSelect: (node, checked) => {
            setSelected((current) => {
              const next = new Set(current);
              if (checked) next.add(node);
              else next.delete(node);
              return next;
            });
          },
        })}
        rows={shown}
        getRowId={(row) => originOf(row).node}
        loading={view.isPending}
        skeletonRows={3}
        mobile="cards"
        pagination="fleet-servers"
        rowActions={(row) => (
          <ServerMenu
            t={t}
            row={row}
            paused={monitoring.get(originOf(row).node)?.paused === true}
            onPause={() => {
              setPausing(originOf(row).node);
            }}
            onResume={() => {
              resume.mutate(originOf(row).node);
            }}
            onLabels={() => {
              setLabelling(row);
            }}
            onRemove={() => {
              setRemoving(originOf(row).node);
              setConfirming(true);
            }}
            onTool={(kind) => {
              setTool({ kind, node: originOf(row).node });
            }}
          />
        )}
        empty={
          <EmptyState
            variant="inline"
            title={t("fleet.filters.noMatch")}
            action={
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  onSearchChange({});
                }}
              >
                {t("fleet.filters.clear")}
              </Button>
            }
          />
        }
      />
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust node list" />
      {labelling !== null ? (
        <LabelsDialog
          node={originOf(labelling).node}
          labels={labelsOf(labelling)}
          onClose={() => {
            setLabelling(null);
          }}
        />
      ) : null}
      {pausing !== null ? (
        <PauseMonitoringDialog
          key={pausing}
          name={pausing}
          open
          onOpenChange={(open) => {
            if (!open) setPausing(null);
          }}
        />
      ) : null}
      {removing !== null ? <RemoveServerDialog key={removing} name={removing} open={confirming} onOpenChange={setConfirming} /> : null}
      {tool?.kind === "connection" ? (
        <ConnectionDialog
          key={tool.node}
          node={tool.node}
          onClose={() => {
            setTool(null);
          }}
        />
      ) : null}
      {tool?.kind === "inventory" ? (
        <InventoryDialog
          key={tool.node}
          node={tool.node}
          onClose={() => {
            setTool(null);
          }}
        />
      ) : null}
      {tool?.kind === "link" ? (
        <LinkMachineDialog
          key={tool.node}
          node={tool.node}
          onClose={() => {
            setTool(null);
          }}
        />
      ) : null}
      {tool?.kind === "rekey" ? (
        <RekeyDialog
          key={tool.node}
          node={tool.node}
          onClose={() => {
            setTool(null);
          }}
        />
      ) : null}
      {tool?.kind === "team" ? (
        <NodeTeamDialog
          key={tool.node}
          node={tool.node}
          onClose={() => {
            setTool(null);
          }}
        />
      ) : null}
    </div>
  );
}
