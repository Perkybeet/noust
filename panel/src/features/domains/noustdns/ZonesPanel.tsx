import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { noustDnsKeys, noustZonesQuery } from "../../../api/queries/noustDns";
import type { NoustZoneSummary } from "../../../api/queries/noustDns";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import type { Column } from "../../../components/ui/DataTable";
import { DataTable } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";

/** The glue a person typed, one `name=address` per line, as the API takes it: addresses by name. */
export function glueOf(text: string): Record<string, string[]> {
  const glue: Record<string, string[]> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf("=");
    if (at === -1) continue;
    const name = line.slice(0, at).trim().toLowerCase();
    const addresses = line
      .slice(at + 1)
      .split(/[\s,]+/)
      .filter((entry) => entry !== "");
    if (name !== "" && addresses.length > 0) glue[name] = addresses;
  }
  return glue;
}

export function AddZoneDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const formId = useId();
  const [name, setName] = useState("");
  const [dnssec, setDnssec] = useState(false);
  const [nameservers, setNameservers] = useState("");
  const [glue, setGlue] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () => {
      const servers = nameservers
        .split(/[\s,]+/)
        .map((entry) => entry.trim().toLowerCase())
        .filter((entry) => entry !== "");
      const addresses = glueOf(glue);
      return request("post", "/api/dns/noust/zones", {
        body: {
          name: name.trim().toLowerCase(),
          dnssec,
          default_ttl: 3600,
          ...(servers.length > 0 ? { nameservers: servers } : {}),
          ...(Object.keys(addresses).length > 0 ? { glue: addresses } : {}),
        },
      });
    },
    onSuccess: (zone) => {
      void queryClient.invalidateQueries({ queryKey: noustDnsKeys.zones });
      close(true);
      void navigate({ to: "/domains/dns/zones/$zone", params: { zone: zone.name } });
    },
  });

  function close(settled = false): void {
    if (add.isPending && !settled) return;
    onOpenChange(false);
    setName("");
    setDnssec(false);
    setNameservers("");
    setGlue("");
    setProblem(null);
    add.reset();
  }

  const fields = add.error && isApiError(add.error) ? add.error.fields : null;
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (name.trim() === "") {
      setProblem(t("noustDns.fieldRequired"));
      return;
    }
    setProblem(null);
    add.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("noustDns.addZone.title")}
      description={t("noustDns.addZone.description")}
      footer={
        <>
          <Button disabled={add.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={add.isPending}>
            {t("noustDns.addZone.action")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("noustDns.addZone.nameLabel")} description={t("noustDns.addZone.nameHelp")} error={problem ?? fields?.["name"] ?? null}>
          <Input mono value={name} onValueChange={setName} placeholder="example.com" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Checkbox label={t("noustDns.addZone.dnssecLabel")} description={t("noustDns.addZone.dnssecHelp")} checked={dnssec} onCheckedChange={setDnssec} />
        <Disclosure label={t("noustDns.addZone.moreOptions")} defaultOpen={false}>
          <div className="flex flex-col gap-5">
            <Field label={t("noustDns.addZone.nameserversLabel")} description={t("noustDns.addZone.nameserversHelp")} optional error={fields?.["nameservers"] ?? null}>
              <Input mono value={nameservers} onValueChange={setNameservers} placeholder="ns1.example.net, ns2.example.net" autoComplete="off" spellCheck={false} />
            </Field>
            <Field label={t("noustDns.addZone.glueLabel")} description={t("noustDns.addZone.glueHelp")} optional error={fields?.["glue"] ?? null}>
              <Textarea mono rows={3} value={glue} onChange={(event) => setGlue(event.target.value)} autoComplete="off" spellCheck={false} />
            </Field>
          </div>
        </Disclosure>
        {add.isError && fields === null ? <ErrorBlock live compact error={add.error} title={t("noustDns.addZone.failed")} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * The zones this server serves: the domains it answers for. A zone is held here (a primary's) or
 * copied from the primary (a secondary's, which is read only). A zone opens to its records, its DNSSEC
 * and the steps to delegate the domain to these servers.
 */
export function ZonesPanel({ canEdit, adding, onAddingChange }: { canEdit: boolean; adding: boolean; onAddingChange: (open: boolean) => void }) {
  const t = useT();
  const navigate = useNavigate();
  const zones = useQuery(noustZonesQuery());
  const rows = zones.data?.zones ?? [];

  const columns: Column<NoustZoneSummary>[] = [
    {
      id: "name",
      header: t("noustDns.zones.nameColumn"),
      cell: (zone) => <span translate="no">{zone.name}</span>,
      sortValue: (zone) => zone.name,
    },
    {
      id: "dnssec",
      header: t("noustDns.zones.dnssecColumn"),
      card: "status",
      width: "w-36",
      cell: (zone) =>
        zone.dnssec === null || zone.dnssec === undefined ? null : (
          <StatusPill state={zone.dnssec ? "running" : "stopped"} label={zone.dnssec ? t("noustDns.zones.signed") : t("noustDns.zones.unsigned")} appearance="inline" size="sm" />
        ),
    },
    { id: "records", header: t("noustDns.zones.recordsColumn"), align: "end", hideBelow: "sm", cell: (zone) => <span className="tabular-nums">{zone.records}</span>, sortValue: (zone) => zone.records },
    { id: "serial", header: t("noustDns.zones.serialColumn"), align: "end", hideBelow: "md", card: "hidden", cell: (zone) => <Mono className="tabular-nums">{zone.serial ?? ""}</Mono> },
    { id: "role", header: t("noustDns.zones.roleColumn"), hideBelow: "md", card: "meta", cell: (zone) => (zone.role === "slave" ? t("noustDns.zones.copied") : t("noustDns.zones.held")) },
  ];

  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  if (zones.isError && zones.data === undefined) {
    return <ErrorBlock error={zones.error} title={t("noustDns.zones.loadFailed")} onRetry={() => void zones.refetch()} retrying={zones.isRefetching} />;
  }
  return (
    <>
      <DataTable
        pagination="noust-zones"
        caption={t("noustDns.zones.caption")}
        columns={columns}
        rows={rows}
        getRowId={(zone) => zone.name}
        loading={zones.isPending}
        onRowActivate={(zone) => void navigate({ to: "/domains/dns/zones/$zone", params: { zone: zone.name } })}
        empty={
          <EmptyState
            variant="inline"
            title={t("noustDns.zones.emptyTitle")}
            description={t("noustDns.zones.emptyDescription")}
            {...(canEdit
              ? {
                  action: (
                    <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={() => onAddingChange(true)}>
                      {t("noustDns.zones.add")}
                    </Button>
                  ),
                }
              : {})}
          />
        }
      />
      <AddZoneDialog open={adding} onOpenChange={onAddingChange} />
    </>
  );
}
