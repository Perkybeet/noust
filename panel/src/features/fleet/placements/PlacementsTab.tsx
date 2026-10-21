import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, Copy, Hammer, Network, Pencil, RefreshCw, Rocket, Server, Trash2 } from "lucide-react";
import { useState } from "react";

import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { appStatus } from "../../../components/page/status";
import { RowMenu } from "../../../components/page/RowMenu";
import { textLinkClassName } from "../../../components/ui/TextLink";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton } from "../../../components/ui/IconButton";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatLabels } from "../data";
import { HrefLink } from "../links";
import { buildersQuery, placementKeys, placementQuery, placementsQuery, removePlacement, syncView } from "../placements";
import type { Placement, PlacementServer } from "../placements";
import { CopyDialog, RolloutDialog } from "./PlacementActionDialog";
import { PlacementDialog } from "./PlacementDialog";
import { BuilderDialog, ShipDialog } from "./ShipDialog";
import { UpstreamsDialog } from "./UpstreamsDialog";
import { LoadingRegion } from "../../../components/page/LoadingRegion";

const NAME_LINK = textLinkClassName("inline", "-mx-1 block max-w-full truncate px-1 py-0.5", "row");

function columns(t: T): Column<PlacementServer>[] {
  return [
    {
      id: "server",
      header: t("placements.column.server"),
      card: "title",
      sortValue: (row) => row.node,
      cell: (row) =>
        row.present ? (
          <HrefLink href={row.href} className={NAME_LINK}>
            {row.node}
          </HrefLink>
        ) : (
          <Mono className="font-medium">{row.node}</Mono>
        ),
    },
    {
      id: "state",
      header: t("placements.column.state"),
      width: "w-32",
      card: "status",
      cell: (row) => {
        if (!row.present || row.status === null || row.status === undefined) return <EmptyCell reason={t("placements.sync.missing")} />;
        const view = appStatus(row.status, t.locale);
        return <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />;
      },
    },
    {
      id: "commit",
      header: t("placements.column.commit"),
      width: "w-40",
      card: "meta",
      cell: (row) =>
        row.commit === null || row.commit === undefined ? (
          <EmptyCell reason={t("placements.noCommit")} />
        ) : (
          <span className="flex min-w-0 flex-col">
            <Mono>{row.commit}</Mono>
            <RelativeTime value={row.deployed_at ?? null} className="text-12 text-fg-muted" />
          </span>
        ),
    },
    {
      id: "sync",
      header: t("placements.column.sync"),
      card: "meta",
      cell: (row) => {
        const view = syncView(row.sync);
        return (
          <span className="flex min-w-0 flex-col items-start gap-0.5">
            <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
            {row.stale ? <span className="text-12 text-fg-muted">{t("placements.stale")}</span> : null}
            {row.message !== null && row.message !== undefined && row.sync !== "current" ? <span className="text-12 text-pretty text-fg-muted">{row.message}</span> : null}
          </span>
        );
      },
    },
  ];
}

type Dialogs = "rollout" | "copy" | "ship" | "builder" | "upstreams" | "edit" | "remove" | null;

/** One placed application: its servers compared, and what can be done across them. */
function PlacementCard({ placement }: { placement: Placement }) {
  const t = useT();
  const queryClient = useQueryClient();
  const detail = useQuery(placementQuery(placement.domain));
  const builders = useQuery(buildersQuery());
  const builtOn = builders.data?.builders.find((row) => row.domain === placement.domain)?.node;
  const [open, setOpen] = useState<Dialogs>(null);
  const status = detail.data?.status;
  const servers = status?.servers ?? [];
  const close = (): void => {
    setOpen(null);
  };

  const summary =
    status === undefined ? null : status.in_sync ? (
      <StatusPill
        state="running"
        label={status.reference_commit ? t("placements.card.inSync", { commit: status.reference_commit }) : t("placements.card.inSyncNoCommit")}
        appearance="inline"
        size="sm"
      />
    ) : (
      <StatusPill state="warning" label={t("placements.card.notInSync")} appearance="inline" size="sm" />
    );

  return (
    <Card
      as="article"
      level={3}
      padding="none"
      loading={detail.isPending}
      title={
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <Mono className="font-medium">{placement.domain}</Mono>
          {summary}
        </span>
      }
      description={
        <span className="flex flex-wrap gap-x-3 gap-y-0.5">
          <span>{placement.members.length === 0 ? t("placements.card.noServers") : t("placements.card.servers", { count: placement.members.length })}</span>
          {Object.keys(placement.labels).length > 0 ? <span>{t("placements.card.label", { label: formatLabels(placement.labels) })}</span> : null}
          {builtOn !== undefined ? <span>{t("placements.card.builtOn", { node: builtOn })}</span> : null}
          {status?.reference_commit !== null && status?.reference_commit !== undefined && !status.in_sync ? (
            <span>{t("placements.card.reference", { commit: status.reference_commit })}</span>
          ) : null}
        </span>
      }
      actions={
        <span className="flex items-center gap-1">
          <IconButton
            label={t("placements.card.refresh")}
            icon={<RefreshCw />}
            size="sm"
            disabled={detail.isFetching}
            onClick={() => {
              void queryClient.invalidateQueries({ queryKey: placementKeys.one(placement.domain) });
            }}
          />
          <RowMenu name={placement.domain}>
            <MenuItem
              icon={<Rocket />}
              onClick={() => {
                setOpen("rollout");
              }}
            >
              {t("placements.card.rollout")}
            </MenuItem>
            <MenuItem
              icon={<Hammer />}
              onClick={() => {
                setOpen("ship");
              }}
            >
              {t("placements.card.ship")}
            </MenuItem>
            <MenuItem
              icon={<Server />}
              onClick={() => {
                setOpen("builder");
              }}
            >
              {t("placements.card.builder")}
            </MenuItem>
            <MenuItem
              icon={<Copy />}
              onClick={() => {
                setOpen("copy");
              }}
            >
              {t("placements.card.copy")}
            </MenuItem>
            <MenuItem
              icon={<ArrowRightLeft />}
              onClick={() => {
                setOpen("upstreams");
              }}
            >
              {t("placements.card.upstreams")}
            </MenuItem>
            <MenuItem
              icon={<Pencil />}
              onClick={() => {
                setOpen("edit");
              }}
            >
              {t("placements.card.edit")}
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              icon={<Trash2 />}
              destructive
              onClick={() => {
                setOpen("remove");
              }}
            >
              {t("placements.card.remove")}
            </MenuItem>
          </RowMenu>
        </span>
      }
    >
      {/* design-exception: hand-error-ternary each card compares its own placement and keeps its frame while it loads */}
      {detail.isError && detail.data === undefined ? (
        <div className="px-5 pb-5">
          <ErrorBlock compact error={detail.error} title={t("placements.statusFailed", { domain: placement.domain })} onRetry={() => void detail.refetch()} retrying={detail.isRefetching} />
        </div>
      ) : detail.isPending ? (
        <LoadingRegion label={t("placements.card.comparing", { domain: placement.domain })} className="flex flex-col gap-2 px-5 pb-5">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </LoadingRegion>
      ) : (
        <DataTable caption={t("placements.card.caption", { domain: placement.domain })} columns={columns(t)} rows={servers} getRowId={(row) => row.node} density="compact" mobile="cards" />
      )}
      {open === "rollout" ? <RolloutDialog placement={placement} members={placement.members} onClose={close} /> : null}
      {open === "copy" ? <CopyDialog placement={placement} servers={servers} onClose={close} /> : null}
      {open === "ship" ? <ShipDialog placement={placement} onClose={close} /> : null}
      {open === "builder" ? <BuilderDialog placement={placement} onClose={close} /> : null}
      {open === "upstreams" ? <UpstreamsDialog domain={placement.domain} onClose={close} /> : null}
      {open === "edit" ? <PlacementDialog placement={placement} onClose={close} /> : null}
      {open === "remove" ? <RemoveDialog placement={placement} onClose={close} /> : null}
    </Card>
  );
}

function RemoveDialog({ placement, onClose }: { placement: Placement; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => removePlacement(placement.domain),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: placementKeys.all });
      toast.success(t("placements.remove.done", { domain: placement.domain }));
    },
  });
  return (
    <ConfirmDialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      friction="simple"
      title={t("placements.remove.title", { domain: placement.domain })}
      description={t("placements.remove.description")}
      actionLabel={t("placements.remove.confirm")}
      destructive
      onConfirm={async () => {
        await remove.mutateAsync();
        onClose();
      }}
    />
  );
}

/**
 * Fleet > On several servers (T1): the applications that run on more than one server, each with its
 * servers side by side - the commit each serves, which most agree on, who is missing or down - and
 * what can be done across them: roll one commit out to all, put the application on a server that lacks
 * it, and write the servers for the load balancer in front of them. The central routes no traffic.
 */
export function PlacementsTab() {
  const t = useT();
  const list = useQuery(placementsQuery());
  const [placing, setPlacing] = useState(false);
  const placements = list.data?.placements ?? [];

  const place = (
    <Button
      variant="primary"
      icon={<Server aria-hidden="true" />}
      onClick={() => {
        setPlacing(true);
      }}
    >
      {t("placements.place")}
    </Button>
  );

  // design-exception: hand-error-ternary the list is read once for the page; a card per placement draws its own loading
  if (list.isError && list.data === undefined) {
    return <ErrorBlock error={list.error} title={t("placements.loadFailed")} onRetry={() => void list.refetch()} retrying={list.isRefetching} />;
  }

  return (
    <Section icon={Network} title={t("placements.title")} description={t("placements.description")} actions={placements.length > 0 ? place : undefined} loading={list.isPending}>
      {list.isPending ? (
        <div className="flex flex-col gap-3">
          <span className="sr-only">{t("placements.card.comparing", { domain: "" })}</span>
          <Skeleton className="h-32 w-full" />
        </div>
      ) : placements.length === 0 ? (
        <EmptyState
          variant="firstUse"
          level={3}
          icon={<ArrowRightLeft />}
          title={t("placements.empty.title")}
          description={t("placements.empty.description")}
          action={place}
          command="noust fleet placement add shop.example.com --nodes web-1,web-2"
        />
      ) : (
        <div className="flex min-w-0 flex-col gap-6">
          {placements.map((placement) => (
            <PlacementCard key={placement.domain} placement={placement} />
          ))}
        </div>
      )}
      <CommandHint label={t("placements.fromTerminal")} command="noust fleet placement status shop.example.com" />
      {placing ? (
        <PlacementDialog
          placement={null}
          onClose={() => {
            setPlacing(false);
          }}
        />
      ) : null}
    </Section>
  );
}
