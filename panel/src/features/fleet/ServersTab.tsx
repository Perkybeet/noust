import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { PanelTop, Play, RotateCw, Tags, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { describeError } from "../../lib/errors";
import { RemoveServerDialog } from "../settings/servers/RemoveServerDialog";
import { nodeKeys, testNode } from "./nodes";
import { useFleetActions } from "./BulkActionDialog";
import { accessOf, fleetKeys, formatLabels, labelsOf, noustOf, originOf, outcomeKind } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { LabelsDialog } from "./LabelsDialog";
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
        return outcome === undefined ? <EmptyCell reason={t("fleet.summary.notRead")} /> : <OutcomePill outcome={outcome} />;
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

function RowActions({
  t,
  row,
  onLabels,
  onRemove,
}: {
  t: T;
  row: FleetRow;
  onLabels: () => void;
  onRemove: () => void;
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
  return (
    <Menu align="end" trigger={<IconButton label={t("fleet.servers.actionsFor", { name: origin.node })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
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
          <MenuItem
            icon={<Play />}
            onClick={() => {
              actions.open({ nodes: [origin.node] });
            }}
          >
            {t("fleet.servers.runOn")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={<Trash2 />} destructive onClick={onRemove}>
            {t("fleet.servers.remove")}
          </MenuItem>
        </>
      )}
    </Menu>
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

  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
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
        rowActions={(row) => (
          <RowActions
            t={t}
            row={row}
            onLabels={() => {
              setLabelling(row);
            }}
            onRemove={() => {
              setRemoving(originOf(row).node);
              setConfirming(true);
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
      {removing !== null ? <RemoveServerDialog key={removing} name={removing} open={confirming} onOpenChange={setConfirming} /> : null}
    </div>
  );
}
