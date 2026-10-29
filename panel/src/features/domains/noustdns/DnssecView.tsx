import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { noustDnsKeys, noustDnssecQuery } from "../../../api/queries/noustDns";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { FieldGroup } from "../../../components/ui/Field";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";

/**
 * A zone's DNSSEC. Off, one button signs it. On, the DS record to give the registrar is here, to
 * copy, with the one thing nobody can do for you said plainly: it goes in after the name servers
 * have changed. Turning it off is the dangerous direction, so it asks for the registrar's DS to be
 * gone first and says why: a DS for a zone that is not signed makes the whole domain unreachable for
 * every resolver that validates.
 */
export function DnssecView({ zone, editable }: { zone: string; editable: boolean }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const dnssec = useQuery(noustDnssecQuery(zone));
  const [off, setOff] = useState(false);
  const [removed, setRemoved] = useState(false);
  const [missing, setMissing] = useState(false);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: noustDnsKeys.dnssec(zone) });
    void queryClient.invalidateQueries({ queryKey: noustDnsKeys.zone(zone) });
    void queryClient.invalidateQueries({ queryKey: noustDnsKeys.zones });
  };
  const turnOn = useMutation({
    mutationFn: () => request("put", "/api/dns/noust/zones/{zone}/dnssec", { params: { zone }, body: { enabled: true, ds_removed: false } }),
    onSuccess: refresh,
  });
  const published = useMutation({
    mutationFn: () => request("post", "/api/dns/noust/zones/{zone}/dnssec/ds-published", { params: { zone } }),
    onSuccess: refresh,
  });

  return (
    <QueryState
      query={dnssec}
      label={t("noustDns.dnssec.title")}
      skeleton={
        <LoadingRegion label={t("common.queryState.loading", { label: t("noustDns.dnssec.title") })}>
          <Skeleton className="h-40" />
        </LoadingRegion>
      }
    >
      {(state) => (
        <div className="flex flex-col gap-4">
          <Card
            level={2}
            title={t("noustDns.dnssec.title")}
            description={t("noustDns.dnssec.description")}
            status={<StatusPill state={state.enabled ? "running" : "stopped"} label={state.enabled ? t("noustDns.dnssec.on") : t("noustDns.dnssec.off")} size="sm" />}
            actions={
              editable ? (
                state.enabled ? (
                  <Button size="sm" onClick={() => setOff(true)}>
                    {t("noustDns.dnssec.turnOff")}
                  </Button>
                ) : (
                  <Button size="sm" variant="primary" loading={turnOn.isPending} onClick={() => turnOn.mutate()}>
                    {t("noustDns.dnssec.turnOn")}
                  </Button>
                )
              ) : undefined
            }
          >
            {turnOn.isError ? <ErrorBlock live compact error={turnOn.error} title={t("noustDns.dnssec.failed")} /> : null}
            {state.enabled && state.ds.length === 0 ? (
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-14 text-fg-muted">{t("noustDns.dnssec.makingKey")}</p>
                <Button size="sm" loading={dnssec.isFetching} onClick={() => void dnssec.refetch()}>
                  {t("noustDns.dnssec.checkAgain")}
                </Button>
              </div>
            ) : null}
          </Card>
          {state.enabled && state.ds.length > 0 ? (
            <Card level={2} title={t("noustDns.dnssec.dsTitle")} description={t("noustDns.dnssec.dsHelp")}>
              <div className="flex flex-col gap-5">
                {state.ds.map((ds) => (
                  <div key={`${ds.key_tag}-${ds.digest_type}`} className="flex flex-col gap-3">
                    <CopyValue label={t("noustDns.dnssec.dsCopy")} value={ds.text} />
                    <KeyValueList
                      items={[
                        { label: t("noustDns.dnssec.dsKeyTag"), value: String(ds.key_tag) },
                        { label: t("noustDns.dnssec.dsAlgorithm"), value: String(ds.algorithm) },
                        { label: t("noustDns.dnssec.dsDigestType"), value: String(ds.digest_type) },
                        { label: t("noustDns.dnssec.dsDigest"), value: ds.digest },
                      ]}
                    />
                  </div>
                ))}
                {editable ? (
                  <div>
                    <Button loading={published.isPending} onClick={() => published.mutate()}>
                      {t("noustDns.dnssec.published")}
                    </Button>
                  </div>
                ) : null}
                {published.isError ? <ErrorBlock live compact error={published.error} title={t("noustDns.dnssec.failed")} /> : null}
              </div>
            </Card>
          ) : null}
          <ConfirmDialog
            open={off}
            onOpenChange={(next) => {
              setOff(next);
              if (!next) {
                setRemoved(false);
                setMissing(false);
              }
            }}
            friction="simple"
            server={node}
            destructive
            title={t("noustDns.dnssec.offTitle", { zone })}
            description={t("noustDns.dnssec.offDescription")}
            actionLabel={t("noustDns.dnssec.turnOff")}
            onConfirm={async () => {
              if (!removed) {
                setMissing(true);
                throw new Error(t("noustDns.dnssec.dsRemovedMissing"));
              }
              await request("put", "/api/dns/noust/zones/{zone}/dnssec", { params: { zone }, body: { enabled: false, ds_removed: true } });
              refresh();
            }}
          >
            <FieldGroup legend={t("noustDns.dnssec.title")} error={missing && !removed ? t("noustDns.dnssec.dsRemovedMissing") : null}>
              <Checkbox
                label={t("noustDns.dnssec.dsRemoved")}
                checked={removed}
                onCheckedChange={(next) => {
                  setRemoved(next);
                  if (next) setMissing(false);
                }}
              />
            </FieldGroup>
          </ConfirmDialog>
        </div>
      )}
    </QueryState>
  );
}
