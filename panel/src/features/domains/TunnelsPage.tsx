import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cable, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { networkKeys, tunnelsQuery } from "../../api/queries/network";
import type { RouteResult, Tunnel } from "../../api/queries/network";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { DnsConflictDialog } from "./DnsConflictDialog";
import { DomainsListPage } from "./DomainsListPage";
import type { RecordConflict } from "./dnsConflict";
import { conflictOf } from "./dnsConflict";

const NAME = /^[a-z0-9][a-z0-9-]{0,39}$/;

function CreateDialog({ open, accounts, onClose }: { open: boolean; accounts: readonly string[]; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [name, setName] = useState("");
  const [account, setAccount] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const chosen = account ?? (accounts.length === 1 ? (accounts[0] ?? null) : null);

  const create = useMutation({
    mutationFn: () => request("post", "/api/tunnels", { body: { name, ...(chosen !== null ? { account: chosen } : {}) } }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.tunnels });
      toast.success(t("network.tunnels.createdToast", { name: result.tunnel.name }));
      close(true);
    },
  });

  function close(settled = false): void {
    if (create.isPending && !settled) return;
    onClose();
    setName("");
    setAccount(null);
    setProblem(null);
    create.reset();
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!NAME.test(name)) {
      setProblem(t("network.tunnels.nameInvalid"));
      return;
    }
    if (chosen === null) {
      setProblem(t("network.tunnels.accountNeeded"));
      return;
    }
    setProblem(null);
    create.mutate();
  };

  const fields = create.error && isApiError(create.error) ? create.error.fields : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("network.tunnels.createTitle")}
      description={t("network.tunnels.createDescription")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending}>
            {t("network.tunnels.create")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("network.tunnels.nameLabel")} description={t("network.tunnels.nameHelp")} error={problem !== null && !NAME.test(name) ? problem : (fields?.["name"] ?? null)}>
          <Input mono value={name} onValueChange={setName} autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={40} />
        </Field>
        <Field label={t("network.tunnels.accountLabel")} description={t("network.tunnels.accountHelp")} nativeLabel={false} error={problem !== null && NAME.test(name) ? problem : (fields?.["account"] ?? null)}>
          <Select
            aria-label={t("network.tunnels.accountLabel")}
            value={chosen}
            placeholder={t("network.tunnels.accountPlaceholder")}
            onValueChange={setAccount}
            options={accounts.map((entry) => ({ value: entry, label: entry }))}
          />
        </Field>
        {create.isError && fields === null ? <ErrorBlock live compact error={create.error} title={t("network.tunnels.createFailed")} /> : null}
      </form>
    </Dialog>
  );
}

type Target = "app" | "service";

/** A member of the DNS outcome the server sent, when it is text; the outcome is free-form. */
function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** What happened to a name's CNAME, in a sentence the operator can act on. */
function dnsWords(t: T, outcome: RouteResult["dns"]): string | null {
  const type = text(outcome["type"]) || "CNAME";
  const hostname = text(outcome["hostname"]);
  switch (outcome["outcome"]) {
    case "created":
    case "adopted":
    case "replaced":
    case "exists":
      return t("network.tunnels.dnsDone", { type, name: hostname, outcome: outcome["outcome"] });
    case "manual":
      return t("network.tunnels.dnsManual", { name: hostname, content: text(outcome["content"]) });
    case "failed":
      return t("network.tunnels.dnsFailed", { detail: text(outcome["detail"]) });
    default:
      return null;
  }
}

function RouteDialog({ tunnel, onClose }: { tunnel: Tunnel | null; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const apps = useQuery({ ...appsQuery(), enabled: tunnel !== null });
  const [hostname, setHostname] = useState("");
  const [target, setTarget] = useState<Target>("app");
  const [app, setApp] = useState<string | null>(null);
  const [service, setService] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [conflict, setConflict] = useState<RecordConflict | null>(null);

  const add = useMutation({
    mutationFn: (replace?: RecordConflict) =>
      request("post", "/api/tunnels/{name}/routes", {
        params: { name: tunnel?.name ?? "" },
        body: {
          hostname: hostname.trim().toLowerCase(),
          dns: true,
          ...(target === "app" && app !== null ? { app_domain: app } : {}),
          ...(target === "service" ? { service: service.trim() } : {}),
          ...(replace ? { replace_record: replace.record_id, replace_current: replace.current } : {}),
        },
      }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.tunnels });
      const outcome = result.dns["outcome"];
      if (outcome === "conflict") {
        setConflict({
          hostname: result.route.hostname,
          record_id: text(result.dns["record_id"]),
          current: text(result.dns["current"]),
          wanted: text(result.dns["content"]),
        });
        return;
      }
      toast.success(t("network.tunnels.routeAddedToast", { name: result.route.hostname }), { description: dnsWords(t, result.dns) ?? "" });
      close(true);
    },
  });

  function close(settled = false): void {
    if (add.isPending && !settled) return;
    onClose();
    setHostname("");
    setApp(null);
    setService("");
    setProblem(null);
    setConflict(null);
    add.reset();
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (hostname.trim() === "") {
      setProblem(t("network.tunnels.hostnameNeeded"));
      return;
    }
    if (target === "app" && app === null) {
      setProblem(t("network.tunnels.appNeeded"));
      return;
    }
    if (target === "service" && service.trim() === "") {
      setProblem(t("network.tunnels.serviceNeeded"));
      return;
    }
    setProblem(null);
    add.mutate(undefined);
  };

  const fields = add.error && isApiError(add.error) ? add.error.fields : null;
  const apiConflict = conflictOf(add.error);

  return (
    <>
      <Dialog
        open={tunnel !== null}
        onOpenChange={(next) => {
          if (!next) close();
        }}
        title={t("network.tunnels.routeTitle", { name: tunnel?.name ?? "" })}
        description={t("network.tunnels.routeDescription")}
        footer={
          <>
            <Button disabled={add.isPending} onClick={() => close()}>
              {t("common.confirmDialog.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={add.isPending}>
              {t("network.tunnels.routeAdd")}
            </Button>
          </>
        }
      >
        <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
          <Field label={t("network.tunnels.hostnameLabel")} description={t("network.tunnels.hostnameHelp")} error={problem !== null && hostname.trim() === "" ? problem : (fields?.["hostname"] ?? null)}>
            <Input mono value={hostname} onValueChange={setHostname} autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="app.example.com" />
          </Field>
          <ChoiceCards<Target>
            legend={t("network.tunnels.targetLegend")}
            options={[
              { value: "app", label: t("network.tunnels.targetApp"), description: t("network.tunnels.targetAppHelp") },
              { value: "service", label: t("network.tunnels.targetService"), description: t("network.tunnels.targetServiceHelp") },
            ]}
            value={target}
            onValueChange={setTarget}
          />
          {target === "app" ? (
            <Field label={t("network.tunnels.appLabel")} nativeLabel={false} error={problem !== null && hostname.trim() !== "" ? problem : (fields?.["app_domain"] ?? null)}>
              <Select
                aria-label={t("network.tunnels.appLabel")}
                value={app}
                placeholder={t("network.tunnels.appPlaceholder")}
                onValueChange={setApp}
                options={(apps.data?.apps ?? []).map((entry) => ({ value: entry.domain, label: entry.domain }))}
              />
            </Field>
          ) : (
            <Field label={t("network.tunnels.serviceLabel")} description={t("network.tunnels.serviceHelp")} error={problem !== null && hostname.trim() !== "" ? problem : (fields?.["service"] ?? null)}>
              <Input mono value={service} onValueChange={setService} autoComplete="off" spellCheck={false} placeholder="http://localhost:3000" />
            </Field>
          )}
          {add.isError && fields === null && apiConflict === null ? <ErrorBlock live compact error={add.error} title={t("network.tunnels.routeFailed", { name: hostname })} /> : null}
        </form>
      </Dialog>
      <DnsConflictDialog
        conflict={conflict}
        onClose={() => setConflict(null)}
        onReplace={async (found) => {
          const result = await add.mutateAsync(found);
          setConflict(null);
          if (result.dns["outcome"] !== "conflict") close(true);
        }}
      />
    </>
  );
}

function TunnelDrawer({ tunnel, onClose, onAddRoute, onRemoveRoute }: { tunnel: Tunnel | null; onClose: () => void; onAddRoute: (tunnel: Tunnel) => void; onRemoveRoute: (tunnel: Tunnel, hostname: string) => void }) {
  const t = useT();
  const routes = tunnel?.routes ?? [];
  return (
    <Drawer
      open={tunnel !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={tunnel?.name ?? ""}
      description={t("network.tunnels.drawerDescription")}
      footer={
        tunnel ? (
          <Button icon={<Plus aria-hidden="true" />} onClick={() => onAddRoute(tunnel)} disabled={tunnel.state !== "ready"}>
            {t("network.tunnels.routeAdd")}
          </Button>
        ) : undefined
      }
    >
      {tunnel ? (
        <div className="flex flex-col gap-5">
          <KeyValueList
            items={[
              { label: t("network.tunnels.connector"), value: tunnel.active || tunnel.state, mono: false, copy: false },
              { label: t("network.tunnels.account"), value: tunnel.provider },
              { label: t("network.tunnels.target"), value: tunnel.target },
              { label: t("network.tunnels.unit"), value: tunnel.unit },
            ]}
          />
          {routes.length === 0 ? (
            <EmptyState variant="inline" title={t("network.tunnels.noRoutes")} />
          ) : (
            <ul aria-label={t("network.tunnels.routesLabel", { name: tunnel.name })} className="flex flex-col divide-y divide-border rounded-control border border-border">
              {routes.map((route) => (
                <li key={route.hostname} className="flex min-w-0 items-center justify-between gap-3 px-3 py-2">
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <Mono truncate>{route.hostname}</Mono>
                    <span className="text-12 text-fg-muted">
                      <Mono truncate>{route.service}</Mono>
                    </span>
                  </div>
                  <IconButton label={t("network.tunnels.routeRemoveNamed", { name: route.hostname })} icon={<Trash2 />} size="sm" onClick={() => onRemoveRoute(tunnel, route.hostname)} />
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </Drawer>
  );
}

function connectorState(tunnel: Tunnel): "running" | "deploying" | "failed" | "stopped" {
  if (tunnel.state !== "ready") return "deploying";
  if (tunnel.active === "active") return "running";
  return tunnel.active === "failed" ? "failed" : "stopped";
}

/**
 * The Tunnels tab: Cloudflare Tunnels as an application's way in, for a server with no public
 * address or no port to open. The connector dials out to Cloudflare; a name routed through it
 * reaches the application's site on this machine. Creating one installs a service and keeps a
 * token only root can read; deleting one stops what answered through it.
 */
export function TunnelsPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const { node } = useNode();
  const tunnels = useQuery(tunnelsQuery());
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [routing, setRouting] = useState<Tunnel | null>(null);
  const [deleting, setDeleting] = useState<Tunnel | null>(null);
  const [unrouting, setUnrouting] = useState<{ tunnel: Tunnel; hostname: string } | null>(null);
  const rows = tunnels.data?.tunnels ?? [];
  const support = tunnels.data?.support;
  const empty = tunnels.data !== undefined && rows.length === 0;
  const opened = rows.find((row) => row.name === open) ?? null;

  const columns: Column<Tunnel>[] = [
    { id: "name", header: t("network.tunnels.nameColumn"), cell: (row) => <span translate="no">{row.name}</span>, sortValue: (row) => row.name },
    {
      id: "state",
      header: t("network.tunnels.connectorColumn"),
      card: "status",
      cell: (row) => <StatusPill state={connectorState(row)} label={row.state === "ready" ? row.active : row.state} appearance="inline" size="sm" />,
    },
    { id: "account", header: t("network.tunnels.accountColumn"), hideBelow: "md", cell: (row) => row.provider },
    {
      id: "routes",
      header: t("network.tunnels.carriesColumn"),
      hideBelow: "sm",
      cell: (row) => ((row.routes ?? []).length === 0 ? t("network.tunnels.nothingYet") : (row.routes ?? []).map((route) => route.hostname).join(", ")),
    },
  ];

  return (
    <DomainsListPage
      primaryAction={
        <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setCreating(true)} disabled={support !== undefined && !support.available}>
          {t("network.tunnels.createAction")}
        </Button>
      }
      {...(support !== undefined && !support.available
        ? {
            notice: (
              <Notice tone="warning" title={support.problem ?? t("network.tunnels.unavailable")}>
                {support.hint ? <SystemOutput label={t("network.tunnels.unavailable")}>{support.hint}</SystemOutput> : null}
              </Notice>
            ),
          }
        : {})}
      footer={<CommandHint command="noust tunnel list" label={t("domains.fromTerminal")} />}
    >
      {tunnels.isError && tunnels.data === undefined ? (
        <ErrorBlock error={tunnels.error} title={t("network.tunnels.listFailed")} onRetry={() => void tunnels.refetch()} retrying={tunnels.isRefetching} />
      ) : empty ? (
        <EmptyState
          variant="firstUse"
          icon={<Cable />}
          title={t("network.tunnels.emptyTitle")}
          description={t("network.tunnels.emptyDescription")}
          {...(support?.available === false
            ? {}
            : {
                action: (
                  <Button icon={<Plus aria-hidden="true" />} onClick={() => setCreating(true)}>
                    {t("network.tunnels.createAction")}
                  </Button>
                ),
              })}
          command="noust tunnel create home"
        />
      ) : (
        <DataTable
          caption={t("network.tunnels.tableCaption")}
          columns={columns}
          rows={rows}
          getRowId={(row) => row.name}
          loading={tunnels.isPending}
          onRowActivate={(row) => setOpen(row.name)}
          rowActions={(row) => (
            <Menu align="end" trigger={<IconButton label={t("domains.actionsFor", { name: row.name })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}>
              <MenuItem icon={<Plus />} disabled={row.state !== "ready"} onClick={() => setRouting(row)}>
                {t("network.tunnels.routeAdd")}
              </MenuItem>
              <MenuItem icon={<Trash2 />} destructive onClick={() => setDeleting(row)}>
                {t("network.tunnels.delete")}
              </MenuItem>
            </Menu>
          )}
        />
      )}
      <CreateDialog open={creating} accounts={support?.accounts ?? []} onClose={() => setCreating(false)} />
      <RouteDialog key={routing?.name ?? "none"} tunnel={routing} onClose={() => setRouting(null)} />
      <TunnelDrawer tunnel={opened} onClose={() => setOpen(null)} onAddRoute={setRouting} onRemoveRoute={(tunnel, hostname) => setUnrouting({ tunnel, hostname })} />
      <ConfirmDialog
        open={unrouting !== null}
        onOpenChange={(next) => {
          if (!next) setUnrouting(null);
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.tunnels.routeRemoveTitle", { name: unrouting?.hostname ?? "" })}
        description={t("network.tunnels.routeRemoveDescription")}
        actionLabel={t("network.tunnels.routeRemove")}
        onConfirm={async () => {
          if (unrouting === null) return;
          await request("delete", "/api/tunnels/{name}/routes/{hostname}", { params: { name: unrouting.tunnel.name, hostname: unrouting.hostname } });
          void queryClient.invalidateQueries({ queryKey: networkKeys.tunnels });
          toast.success(t("network.tunnels.routeRemovedToast", { name: unrouting.hostname }));
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(next) => {
          if (!next) setDeleting(null);
        }}
        friction="type"
        confirmText={deleting?.name ?? ""}
        server={node}
        destructive
        title={t("network.tunnels.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("network.tunnels.deleteDescription")}
        actionLabel={t("network.tunnels.delete")}
        onConfirm={async () => {
          if (deleting === null) return;
          await request("delete", "/api/tunnels/{name}", { params: { name: deleting.name } });
          void queryClient.invalidateQueries({ queryKey: networkKeys.tunnels });
          toast.success(t("network.tunnels.deletedToast", { name: deleting.name }));
        }}
      />
    </DomainsListPage>
  );
}
