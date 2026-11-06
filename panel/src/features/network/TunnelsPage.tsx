import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { networkKeys, tunnelsQuery } from "../../api/queries/network";
import type { RouteResult, Tunnel } from "../../api/queries/network";
import { KeyValueList } from "../../components/page/KeyValueList";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { Can, GuardedButton, ReadOnlyLine } from "../../components/page/permissions";
import { ErrorBlock } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { FirstUse } from "../../components/ui/FirstUse";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { ResourceCard } from "../../components/ui/ResourceCard";
import { RowList } from "../../components/ui/RowList";
import { Select } from "../../components/ui/Select";
import { ServerText } from "../../components/ui/ServerText";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { DnsConflictDialog } from "../domains/DnsConflictDialog";
import type { RecordConflict } from "../domains/dnsConflict";
import { conflictOf } from "../domains/dnsConflict";
import { MachinePage } from "../server/MachinePage";
import { MissingSoftware } from "../server/software/MissingSoftware";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

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
        <Field label={t("common.words.name")} description={t("network.tunnels.nameHelp")} error={problem !== null && !NAME.test(name) ? problem : (fields?.["name"] ?? null)}>
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
            <RowList aria-label={t("network.tunnels.routesLabel", { name: tunnel.name })}>
              {routes.map((route) => (
                <RowList.Item
                  key={route.hostname}
                  actions={<IconButton label={t("network.tunnels.routeRemoveNamed", { name: route.hostname })} icon={<ICONS.delete />} size="sm" onClick={() => onRemoveRoute(tunnel, route.hostname)} />}
                >
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <Mono truncate>{route.hostname}</Mono>
                    <span className="text-12 text-fg-muted">
                      <Mono truncate>{route.service}</Mono>
                    </span>
                  </div>
                </RowList.Item>
              ))}
            </RowList>
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

/** What a tunnel needs that this server lacks, said where the page's own action waits for it. */
function whyNot(t: T, lacks: "sealed" | "connector" | "account" | "other"): string {
  switch (lacks) {
    case "sealed":
      return t("network.tunnels.lacks.sealed");
    case "connector":
      return t("network.tunnels.lacks.connector");
    case "account":
      return t("network.tunnels.lacks.account");
    case "other":
      return t("network.tunnels.unavailable");
  }
}

function TunnelCard({ tunnel, onOpen, onRoute, onDelete }: { tunnel: Tunnel; onOpen: () => void; onRoute: () => void; onDelete: () => void }) {
  const t = useT();
  const names = (tunnel.routes ?? []).map((route) => route.hostname);
  return (
    <ResourceCard
      name={tunnel.name}
      untranslated
      level={2}
      icon={OBJECT_ICONS.tunnel}
      status={<StatusPill state={connectorState(tunnel)} label={tunnel.state === "ready" ? tunnel.active : tunnel.state} size="sm" />}
      facts={[
        { label: t("network.tunnels.carriesColumn"), value: names.length === 0 ? t("network.tunnels.nothingYet") : <Mono>{names.join(", ")}</Mono> },
        { label: t("network.tunnels.accountColumn"), value: tunnel.provider },
      ]}
      actions={
        <RowMenu name={tunnel.name}>
          <MenuItem icon={<OBJECT_ICONS.tunnel />} onClick={onOpen}>
            {t("network.tunnels.seeRoutes")}
          </MenuItem>
          <Can permission="server.manage">
            <MenuItem icon={<Plus />} disabled={tunnel.state !== "ready"} onClick={onRoute}>
              {t("network.tunnels.routeAdd")}
            </MenuItem>
            <MenuItem icon={<ICONS.delete />} destructive onClick={onDelete}>
              {t("network.tunnels.delete")}
            </MenuItem>
          </Can>
        </RowMenu>
      }
    />
  );
}

/**
 * Tunnels, a section of Network: Cloudflare Tunnels as an application's way in, for a server with no public address or
 * no port to open. The connector dials out to Cloudflare; a name routed through it reaches the application's site on
 * this machine. Creating one installs a service and keeps a token only root can read; deleting one stops what answered
 * through it. A server that lacks what a tunnel needs says so where the action waits, and leads to what to do first.
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
  // What a tunnel needs that this server lacks, in the order it is fixed: the connector, then an account at Cloudflare.
  const lacks = support === undefined || support.available ? null : support.sealed ? "sealed" : support.cloudflared == null ? "connector" : (support.accounts ?? []).length === 0 ? "account" : "other";
  const unavailable =
    support !== undefined && !support.available ? (
      <Notice tone="warning" title={t("network.tunnels.unavailable")}>
        <ServerText coded={support.coded} text={support.problem} /> <ServerText coded={support.coded} text={support.hint} part="hint" />
      </Notice>
    ) : null;
  const opened = rows.find((row) => row.name === open) ?? null;

  return (
    <MachinePage
      title={t("domains.sections.tunnels.title")}
      description={t("domains.sections.tunnels.description")}
      header={{
        primaryAction: (
          <GuardedButton permission="server.manage" variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setCreating(true)} {...(lacks !== null ? { unavailable: whyNot(t, lacks) } : {})}>
            {t("network.tunnels.createAction")}
          </GuardedButton>
        ),
      }}
    >
      <div className="flex min-w-0 flex-col gap-4">
        <ReadOnlyLine permission="server.manage" />
        {lacks === "connector" ? (
          // The connector is a piece Noust installs: said where every missing piece is said, with its install. The explanation
          // below stands for a server whose registry cannot be read.
          <MissingSoftware component="cloudflared" onInstalled={() => void queryClient.invalidateQueries({ queryKey: networkKeys.tunnels })} fallback={unavailable} />
        ) : lacks !== null && !empty ? (
          unavailable
        ) : null}
        {/* design-exception: hand-error-ternary the cards draw their own skeleton; the failure replaces only what they would have listed */}
        {tunnels.isError && tunnels.data === undefined ? (
          <ErrorBlock error={tunnels.error} title={t("network.tunnels.listFailed")} onRetry={() => void tunnels.refetch()} retrying={tunnels.isRefetching} />
        ) : empty ? (
          <FirstUse
            {...(lacks === "account"
              ? {
                  action: (
                    <Link to="/domains/dns" className={buttonClassName("primary")}>
                      {t("network.tunnels.addAccount")}
                    </Link>
                  ),
                }
              : {})}
          >
            {lacks === "account" || lacks === "sealed" ? (
              <>
                {t("network.tunnels.emptyDescription")} <span className="text-fg">{t(`network.tunnels.lacks.${lacks}`)}</span>
              </>
            ) : (
              t("network.tunnels.emptyDescription")
            )}
          </FirstUse>
        ) : tunnels.data === undefined ? (
          <LoadingRegion label={t("common.queryState.loading", { label: t("domains.sections.tunnels.title") })} className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-36 rounded-card" />
            <Skeleton className="h-36 rounded-card" />
          </LoadingRegion>
        ) : (
          <ul aria-label={t("network.tunnels.tableCaption")} className="grid gap-4 md:grid-cols-2">
            {rows.map((row) => (
              <li key={row.name} className="min-w-0">
                <TunnelCard tunnel={row} onOpen={() => setOpen(row.name)} onRoute={() => setRouting(row)} onDelete={() => setDeleting(row)} />
              </li>
            ))}
          </ul>
        )}
      </div>
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
        title={t("common.words.deleteNamed", { name: deleting?.name ?? "" })}
        description={t("network.tunnels.deleteDescription")}
        actionLabel={t("network.tunnels.delete")}
        onConfirm={async () => {
          if (deleting === null) return;
          await request("delete", "/api/tunnels/{name}", { params: { name: deleting.name } });
          void queryClient.invalidateQueries({ queryKey: networkKeys.tunnels });
          toast.success(t("network.tunnels.deletedToast", { name: deleting.name }));
        }}
      />
    </MachinePage>
  );
}
