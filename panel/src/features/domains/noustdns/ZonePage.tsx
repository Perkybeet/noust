import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { request } from "../../../api/client";
import { noustDnsKeys, noustServerQuery, noustZoneQuery } from "../../../api/queries/noustDns";
import type { NoustZone } from "../../../api/queries/noustDns";
import { DetailPage } from "../../../components/page/DetailPage";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { QueryState } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem } from "../../../components/ui/Menu";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { DelegationView } from "./DelegationView";
import { DnssecView } from "./DnssecView";
import { RecordDialog, RecordsView } from "./RecordsView";
import type { ZoneView } from "./zoneSearch";

export interface ZonePageProps {
  zone: string;
  view: ZoneView;
  onViewChange: (view: ZoneView) => void;
}

/**
 * One zone of Noust DNS: its records (the domain's names and where they point), its DNSSEC, and the
 * steps to delegate the domain to these servers with the check that they were done. The three share
 * the header, and the section is in the URL. A zone this server only copies is read only: its
 * primary is where it is edited.
 */
export function ZonePage({ zone, view, onViewChange }: ZonePageProps) {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const server = useQuery(noustServerQuery());
  const data = useQuery(noustZoneQuery(zone));
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const editable = server.data?.role !== "secondary";

  const header = (loaded: NoustZone | undefined) => ({
    title: zone,
    mono: true,
    ...(loaded !== undefined
      ? {
          status: <StatusPill state={loaded.dnssec ? "running" : "stopped"} label={loaded.dnssec ? t("noustDns.zone.signed") : t("noustDns.zone.unsigned")} />,
          meta: (
            <>
              <span>{t("noustDns.zone.recordCount", { count: loaded.records.length })}</span>
              {loaded.serial !== null && loaded.serial !== undefined ? <span>{t("noustDns.zone.serial", { serial: loaded.serial })}</span> : null}
            </>
          ),
        }
      : {}),
    ...(editable && view === "records"
      ? {
          primaryAction: (
            <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setAdding(true)}>
              {t("noustDns.records.add")}
            </Button>
          ),
        }
      : {}),
    ...(editable
      ? {
          overflow: (
            <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(true)}>
              {t("noustDns.deleteZone.action")}
            </MenuItem>
          ),
        }
      : {}),
  });

  return (
    <DetailPage
      header={header(data.data)}
      tabs={
        <SegmentedControl<ZoneView>
          label={t("noustDns.zone.viewLabel")}
          value={view}
          onValueChange={onViewChange}
          options={[
            { value: "records", label: t("noustDns.zone.records") },
            { value: "dnssec", label: t("noustDns.zone.dnssec") },
            { value: "delegation", label: t("noustDns.zone.delegation") },
          ]}
        />
      }
    >
      {editable ? null : <Notice title={t("noustDns.zone.readOnly")} />}
      <QueryState
        query={data}
        label={t("noustDns.zoneLoadingLabel")}
        skeleton={
          <LoadingRegion label={t("common.queryState.loading", { label: t("noustDns.zoneLoadingLabel") })} className="flex flex-col gap-3">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </LoadingRegion>
        }
      >
        {(loaded) =>
          view === "records" ? (
            <RecordsView zone={loaded} editable={editable} onAdd={() => setAdding(true)} />
          ) : view === "dnssec" ? (
            <DnssecView zone={zone} editable={editable} />
          ) : (
            <DelegationView zone={zone} />
          )
        }
      </QueryState>
      <RecordDialog zone={zone} record={null} open={adding} onOpenChange={setAdding} />
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        friction="type"
        confirmText={zone}
        server={node}
        destructive
        title={t("common.words.deleteNamed", { name: zone })}
        description={t("noustDns.deleteZone.description")}
        actionLabel={t("noustDns.deleteZone.action")}
        onConfirm={async () => {
          await request("delete", "/api/dns/noust/zones/{zone}", { params: { zone } });
          void queryClient.invalidateQueries({ queryKey: noustDnsKeys.zones });
          void navigate({ to: "/domains/dns/noust" });
        }}
      />
    </DetailPage>
  );
}
