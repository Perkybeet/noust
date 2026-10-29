import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { request } from "../../../api/client";
import { noustDnsKeys, noustServerQuery } from "../../../api/queries/noustDns";
import type { NoustServer } from "../../../api/queries/noustDns";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { QueryState } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { DangerAction, DangerZone } from "../../../components/page/DangerZone";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { ICONS } from "../../../components/ui/icons";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { CommandHint } from "../../../components/page/CommandHint";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { DomainsListPage } from "../DomainsListPage";
import { InstallPanel } from "./InstallPanel";
import { SecondariesCard } from "./SecondariesCard";
import { SetupPanel } from "./SetupPanel";
import { noustState, NOUST_PILL, stateLabel } from "./state";
import { ZonesPanel } from "./ZonesPanel";

function RemoveCard({ server }: { server: NoustServer }) {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [deleteZones, setDeleteZones] = useState(false);
  const remove = useMutation({
    mutationFn: () => request("delete", "/api/dns/noust/server", { query: { delete_zones: deleteZones } }),
  });
  return (
    <DangerZone title={t("noustDns.remove.heading")}>
      <DangerAction
        title={t("noustDns.remove.action")}
        description={t("noustDns.remove.description")}
        action={
          // design-exception: danger-button-outside-confirm the action of the DangerAction, which opens the one confirmation below
          <Button variant="danger" onClick={() => setOpen(true)}>
            {t("noustDns.remove.action")}
          </Button>
        }
      />
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        friction="type"
        confirmText={server.hostname ?? "noust-dns"}
        server={node}
        destructive
        title={t("noustDns.remove.confirmQuestion")}
        description={t("noustDns.remove.confirmDescription")}
        actionLabel={t("noustDns.remove.action")}
        onConfirm={async () => {
          await remove.mutateAsync();
          void queryClient.invalidateQueries({ queryKey: noustDnsKeys.server });
          void queryClient.invalidateQueries({ queryKey: noustDnsKeys.zones });
          void navigate({ to: "/domains/dns" });
        }}
      >
        <Checkbox label={t("noustDns.remove.deleteZones")} description={t("noustDns.remove.deleteZonesHelp")} checked={deleteZones} onCheckedChange={setDeleteZones} />
      </ConfirmDialog>
    </DangerZone>
  );
}

/** A server that is set up: what it is, its zones, and for a primary its secondaries. */
function Running({ server, adding, onAddingChange }: { server: NoustServer; adding: boolean; onAddingChange: (open: boolean) => void }) {
  const t = useT();
  const primary = server.role !== "secondary";
  return (
    <div className="flex flex-col gap-6">
      <Card level={2} title={t("noustDns.server.roleLabel")} icon={OBJECT_ICONS.dns} padding="sm" status={<StateBadge server={server} />}>
        <KeyValueList
          items={[
            { label: t("common.words.name"), value: server.hostname ?? null },
            { label: t("noustDns.server.listenLabel"), value: (server.listen ?? []).join(", ") },
            ...(primary ? [] : [{ label: t("noustDns.server.primaryLabel"), value: server.primary_address ?? null }]),
            { label: t("noustDns.server.versionLabel"), value: server.version ?? null },
          ]}
        />
      </Card>
      {primary ? null : <Notice title={t("noustDns.server.secondaryNote")} />}
      <ZonesPanel canEdit={primary} adding={adding} onAddingChange={onAddingChange} />
      {primary ? <SecondariesCard server={server} /> : null}
      <RemoveCard server={server} />
    </div>
  );
}

/** The state of this server's Noust DNS as a pill: its word is the role, or what is missing. */
function StateBadge({ server }: { server: NoustServer }) {
  const t = useT();
  const state = noustState(server);
  return <StatusPill state={NOUST_PILL[state]} label={stateLabel(t, state)} size="sm" />;
}

/**
 * Noust DNS on this server: three states, one page. Not installed: what running a name server takes
 * on, and the install behind an acceptance. Installed: say what the server does and where it answers.
 * Set up: its zones (the domains it answers for), its secondaries and, last, taking it away. The zone
 * is the unit: it opens to its records, its DNSSEC and the delegation.
 */
export function NoustDnsPage() {
  const t = useT();
  const status = useQuery(noustServerQuery());
  const [adding, setAdding] = useState(false);
  const server = status.data;
  const state = server === undefined ? null : noustState(server);
  const addable = server !== undefined && server.configured && server.role !== "secondary";

  return (
    <DomainsListPage
      section="dns"
      title={t("noustDns.title")}
      description={t("noustDns.description")}
      {...(addable
        ? {
            primaryAction: (
              <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setAdding(true)}>
                {t("noustDns.zones.add")}
              </Button>
            ),
          }
        : {})}
      {...(state === "stopped"
        ? { notice: <Notice tone="error" variant="banner" title={t("noustDns.server.notRunningTitle")}>{t("noustDns.server.notRunningHint")}</Notice> }
        : {})}
      {...(server?.configured === true ? { footer: <CommandHint command="noust dns zone create example.com" label={t("common.words.fromTerminal")} /> } : {})}
    >
      <QueryState
        query={status}
        label={t("noustDns.loadingLabel")}
        skeleton={
          <LoadingRegion label={t("common.queryState.loading", { label: t("noustDns.loadingLabel") })} className="flex flex-col gap-4">
            <Skeleton className="h-40" />
            <Skeleton className="h-24" />
          </LoadingRegion>
        }
      >
        {(data) => (!data.installed ? <InstallPanel server={data} /> : !data.configured ? <SetupPanel server={data} /> : <Running server={data} adding={adding} onAddingChange={setAdding} />)}
      </QueryState>
    </DomainsListPage>
  );
}
