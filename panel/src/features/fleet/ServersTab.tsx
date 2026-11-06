import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Bell, BellOff, Download, FileUp, KeyRound, Link2, Play, RotateCw, Tags, UsersRound, X } from "lucide-react";
import { useMemo, useState } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { FilterBar } from "../../components/page/FilterBar";
import { Can } from "../../components/page/permissions";
import { ErrorBlock } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { Button, buttonClassName } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { Meter } from "../../components/ui/Progress";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { downloadText } from "../../lib/clipboard";
import { describeError } from "../../lib/errors";
import { MonitoringNote } from "../settings/servers/MonitoringNote";
import { PauseMonitoringDialog } from "../settings/servers/PauseMonitoringDialog";
import { RemoveServerDialog } from "../settings/servers/RemoveServerDialog";
import { nodeKeys, nodesQuery, setNodeMonitoring, testNode } from "./nodes";
import type { NodeMonitoring } from "./nodes";
import { useFleetActions } from "./BulkActionDialog";
import { fleetKeys, formatLabels, labelsOf, loadOf, noustOf, noustUpdatable, originOf, osUpdatesOf, outcomeKind, systemNameOf } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { LabelsDialog } from "./LabelsDialog";
import { ServerLink } from "./links";
import { NodeTeamDialog, RekeyDialog } from "./NodeAccessDialogs";
import { ConnectionDialog, InventoryDialog, LinkMachineDialog } from "./NodeToolsDialogs";
import { fetchInventory } from "./nodeTools";
import { NAME_LINK, OutcomePill, PartialNotice, StaleFrom, StateFilter, useFleetView } from "./parts";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/** The Servers view's own states, for its filter. */
const SERVER_STATES = ["answering", "down", "older"] as const;

/** What a server can be waiting for, for the filter of the servers that have an update. */
const UPDATE_STATES = ["noust", "os", "reboot"] as const;

function stateOf(outcome: NodeOutcome | undefined): (typeof SERVER_STATES)[number] | null {
  if (outcome === undefined) return null;
  const kind = outcomeKind(outcome);
  if (kind === "unsupported") return "older";
  if (kind === "unreachable" || kind === "stale" || kind === "error") return "down";
  return "answering";
}

/** Whether a server is waiting for Noust, for its system, or for a reboot. */
function needs(row: FleetRow, what: (typeof UPDATE_STATES)[number]): boolean {
  if (what === "noust") return noustUpdatable(noustOf(row).state);
  const os = osUpdatesOf(row);
  if (what === "os") return (os?.pending ?? 0) > 0;
  return os?.reboot === true;
}

/** The servers of the list that have something waiting: what the "With an update" view of the column lists. */
function waiting(row: FleetRow): boolean {
  return UPDATE_STATES.some((what) => needs(row, what));
}

interface RowHandlers {
  selected: ReadonlySet<string>;
  onSelect: (node: string, checked: boolean) => void;
  /** How long each server has been silent, or that its monitoring is paused. */
  monitoring: ReadonlyMap<string, NodeMonitoring | null>;
  /** Starts Noust's update on a server, through the same bulk action the header's button uses. */
  onUpdate: (node: string) => void;
  /** The view is the servers that are waiting for something: it says what, and leaves out the meters. */
  updates: boolean;
}

/** One reading of a server as a small meter that turns amber and red by itself; nothing where the server gave none. */
function LoadCell({ label, value }: { label: string; value: number | null | undefined }) {
  const t = useT();
  if (value === null || value === undefined) return <EmptyCell reason={t("fleet.summary.notRead")} />;
  return <Meter size="sm" label={label} value={value} className="w-full min-w-24" />;
}

/**
 * Which Noust a server runs and what to do about it: "up to date" beside the version, or the one press that updates it
 * and says to what. The version it is told is the one the server's own package source offers: nothing is written here.
 */
function NoustCell({ t, row, onUpdate }: { t: T; row: FleetRow; onUpdate: (node: string) => void }) {
  const noust = noustOf(row);
  const origin = originOf(row);
  if (noust.current === null) return <EmptyCell reason={t("fleet.summary.notRead")} />;
  const target = noust.latest;
  const behind = noust.state === "update_available" || noust.state === "index_behind";
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
      <Mono>{noust.current}</Mono>
      {noust.state === "up_to_date" ? <span className="text-12 text-fg-muted">{t("fleet.servers.upToDate")}</span> : null}
      {behind && target !== null ? (
        // This central updates from its own page, where it can watch itself come back; a server is updated from here.
        origin.local ? (
          <Link to="/server/updates" className={buttonClassName("secondary", "sm")}>
            {t("fleet.servers.updateTo", { version: target })}
          </Link>
        ) : (
          <Can permission="fleet.manage">
            <Button size="sm" icon={<RotateCw aria-hidden="true" />} onClick={() => onUpdate(origin.node)}>
              {t("fleet.servers.updateTo", { version: target })}
            </Button>
          </Can>
        )
      ) : null}
      {noust.state === "on_the_way" ? <StatusPill state="queued" label={t("fleet.updates.noust.onTheWay")} appearance="inline" size="sm" /> : null}
    </span>
  );
}

/** What the system has waiting, in a cell: how many packages, how many of them security ones, and a reboot that is due. */
function SystemCell({ t, row }: { t: T; row: FleetRow }) {
  const os = osUpdatesOf(row);
  const pending = os?.pending ?? null;
  if (os === null || pending === null) return <EmptyCell reason={t("fleet.updates.osUnknown")} />;
  return (
    <span className="flex min-w-0 flex-col text-13">
      {pending === 0 ? <span className="text-fg-muted">{t("fleet.updates.osNone")}</span> : <span className="text-fg tabular-nums">{t("fleet.updates.osPending", { count: pending })}</span>}
      {os.security !== null && os.security > 0 ? (
        <span className={cx("inline-flex items-center gap-1 font-medium", stateTextClass("warning"))}>
          <StatusGlyph state="warning" size={10} />
          {t("fleet.updates.osSecurity", { count: os.security })}
        </span>
      ) : null}
      {os.reboot === true ? <span className="text-12 text-fg-muted">{t("fleet.updates.rebootNeeded")}</span> : null}
    </span>
  );
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
      // The labels follow the name on its line, the first ones and "+N" for the rest: a server with many does not grow its row.
      cell: (row) => {
        const origin = originOf(row);
        const system = systemNameOf(row);
        // This central is said once, as a tag on its line: it is the one server of the list that is not a node.
        const tags = [...(origin.local ? [t("fleet.server.centralTag")] : []), ...labelsOf(row).map(([key, value]) => `${key}=${value}`)];
        return (
          <span className="flex min-w-0 flex-col items-start gap-0.5">
            <NameWithTags
              name={
                <ServerLink node={origin.local ? null : origin.node} path="/server" className={NAME_LINK} aria-label={t("fleet.server.open", { name: origin.node })}>
                  <Mono truncate>{origin.node}</Mono>
                </ServerLink>
              }
              tags={tags}
            />
            {system !== null ? <span className="text-12 text-fg-muted">{system}</span> : null}
            <StaleFrom outcome={outcomes.get(origin.node)} />
          </span>
        );
      },
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
      id: "noust",
      header: t("fleet.column.noust"),
      card: "meta",
      cell: (row) => <NoustCell t={t} row={row} onUpdate={handlers.onUpdate} />,
    },
    ...(handlers.updates
      ? [
          {
            id: "system",
            header: t("fleet.column.osUpdates"),
            card: "meta" as const,
            cell: (row: FleetRow) => <SystemCell t={t} row={row} />,
          },
        ]
      : [
          {
            id: "cpu",
            header: t("fleet.servers.cpu"),
            hideBelow: "md" as const,
            card: "hidden" as const,
            cell: (row: FleetRow) => <LoadCell label={t("fleet.servers.cpu")} value={loadOf(row)?.cpu} />,
          },
          {
            id: "memory",
            header: t("fleet.servers.memory"),
            hideBelow: "md" as const,
            card: "hidden" as const,
            cell: (row: FleetRow) => <LoadCell label={t("fleet.servers.memory")} value={loadOf(row)?.memory} />,
          },
          {
            id: "disk",
            header: t("fleet.servers.disk"),
            hideBelow: "md" as const,
            card: "hidden" as const,
            cell: (row: FleetRow) => <LoadCell label={t("fleet.servers.disk")} value={loadOf(row)?.disk} />,
          },
        ]),
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
      <MenuItem icon={<OBJECT_ICONS.server />} onClick={() => void navigate({ to: "/server", search: { node: origin.local ? undefined : origin.node } })}>
        {t("fleet.servers.open")}
      </MenuItem>
      {origin.local ? null : (
        <>
          <MenuItem icon={<RotateCw />} disabled={test.isPending} onClick={() => test.mutate()}>
            {t("fleet.servers.test")}
          </MenuItem>
          <MenuItem
            icon={<OBJECT_ICONS.update />}
            onClick={() => {
              actions.open({ action: "os_updates", nodes: [origin.node] });
            }}
          >
            {t("fleet.servers.installSystemUpdates")}
          </MenuItem>
          <MenuItem
            icon={<RotateCw />}
            onClick={() => {
              actions.open({ action: "index_refresh", nodes: [origin.node] });
            }}
          >
            {t("fleet.updates.refreshLists")}
          </MenuItem>
          <MenuItem icon={<OBJECT_ICONS.central />} onClick={() => void navigate({ to: "/server/central", search: { node: origin.node } })}>
            {t("fleet.servers.centralPermissions")}
          </MenuItem>
          <MenuItem icon={<Tags />} onClick={onLabels}>
            {t("fleet.servers.editLabels")}
          </MenuItem>
          {paused ? (
            <MenuItem icon={<Bell />} onClick={onResume}>
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
            icon={<OBJECT_ICONS.tunnel />}
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
          <MenuItem icon={<ICONS.delete />} destructive onClick={onRemove}>
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
  /**
   * `updates`: the servers that are waiting for something (Noust, their system, a reboot), with what each waits for in
   * place of the meters. The same list, read the other way: the column of the area has it as "With an update".
   */
  preset?: "all" | "updates";
}

/**
 * Every server of the fleet (T1), in one list that is also where they are updated (4.2, item 125: what were two views
 * of one list are one): whether it answers, which Noust it runs and the press that updates it, how loaded it is, its
 * labels after its name, and the menu of what can be done to it. Servers are chosen here, by hand, for a bulk
 * action; their labels, which aim an action at a group, are edited here too.
 */
export function ServersTab({ search, onSearchChange, preset = "all" }: ServersTabProps) {
  const t = useT();
  // The summary view carries what every server says of itself (its version, its machine snapshot and its server summary),
  // which is what both readings of the list need: the servers view had only who they are.
  const view = useFleetView("summary");
  const actions = useFleetActions();
  const updates = preset === "updates";
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
  const listed = updates ? rows.filter(waiting) : rows;
  const shown = listed.filter((row) => {
    const origin = originOf(row);
    if (search.state !== undefined) {
      const wanted = updates ? UPDATE_STATES.some((what) => what === search.state && needs(row, what)) : stateOf(view.outcomes.get(origin.node)) === search.state;
      if (!wanted) return false;
    }
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
            options={
              updates
                ? UPDATE_STATES.map((state) => ({ value: state, label: t(`fleet.updates.state.${state}`) }))
                : SERVER_STATES.map((state) => ({ value: state, label: t(`fleet.servers.state.${state}`) }))
            }
          />
        }
        count={view.data === undefined ? "" : isFiltered(search) ? t("fleet.servers.countFiltered", { shown: shown.length, total: listed.length }) : t("fleet.servers.count", { count: listed.length })}
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
        caption={updates ? t("fleet.updates.caption") : t("fleet.servers.caption")}
        columns={columns(t, view.outcomes, {
          monitoring,
          selected,
          updates,
          onUpdate: (node) => {
            actions.open({ action: "noust_update", nodes: [node] });
          },
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
          updates && !isFiltered(search) ? (
            <EmptyState variant="inline" title={t("fleet.updates.allCurrent")} />
          ) : (
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
                  {t("common.words.clearFilters")}
                </Button>
              }
            />
          )
        }
      />
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
