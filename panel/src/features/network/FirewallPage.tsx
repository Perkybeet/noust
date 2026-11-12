/**
 * The server's firewall, a section of Network: whether it filters, the rules it holds with who made each, and the way to
 * open a port. The listening ports are not drawn here (the table of who listens is one, in Listening ports); what is
 * loud here is the two ways a port is open without anybody meaning it: Docker publishing around the firewall, and a
 * database answering the internet. The two other things called a firewall are named beside it (`ThreeFirewalls`).
 *
 * Every change undoes itself unless it is confirmed from a new SSH login (PendingChanges, over the page), and the guard
 * refuses any change that would close SSH or a public console.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, Plus, Power } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { GuardedButton, ReadOnlyLine, useCan } from "../../components/page/permissions";
import { RowMenu } from "../../components/page/RowMenu";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Badge } from "../../components/ui/Badge";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { SkeletonSection } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { NodeCapabilityGate, useNodeCapability } from "../../nodes/capability";
import { ServerErrorBlock, ServerQueryState, useServerConfirm } from "../server/errors";
import { typedHost } from "../server/friction";
import { MachinePage } from "../server/MachinePage";
import { portRows } from "../server/network/data";
import type { PortRow } from "../server/network/data";
import { SERVER_CAPABILITY, firewallQuery, identityQuery, portsQuery } from "../server/queries";
import type { Firewall, FirewallRule } from "../server/queries";
import { useServerJob } from "../server/serverJob";
import { MissingSoftware } from "../server/software/MissingSoftware";
import { ChangeSourceDialog } from "./ChangeSourceDialog";
import { isProtected, ruleMaker, rulePorts, ruleSubject } from "./firewallRules";
import { HolderName } from "./HolderName";
import { useOpenPort } from "./OpenPortDialog";
import { portText } from "./reach";
import { ThreeFirewalls } from "./ThreeFirewalls";

type Opened = { kind: "enable" } | { kind: "disable" } | { kind: "delete"; rule: FirewallRule } | { kind: "source"; rule: FirewallRule } | null;

/** The route of the one-step change of origin: a node before 4.3 does not have it, and then it is not offered. */
const CHANGE_SOURCE_ROUTE = "POST /api/server/security/firewall/rules/{rule_id}/source";

function Subject({ rule, rows }: { rule: FirewallRule; rows: readonly PortRow[] }) {
  const subject = ruleSubject(rule, rows);
  if (subject.kind === "service") return <span translate="no">{subject.name}</span>;
  if (subject.kind === "holder") return <HolderName holder={subject.holder} />;
  return <EmptyCell />;
}

function Maker({ rule, guard }: { rule: FirewallRule; guard: Firewall["protected_ports"] }) {
  const t = useT();
  const kept = isProtected(rule, guard);
  if (kept !== null && rule.noust) return <span className="text-fg-muted">{t("network.firewall.makerProtected")}</span>;
  const maker = ruleMaker(rule);
  switch (maker.kind) {
    case "stream":
      return (
        <TextLink to="/network" size="ui">
          {t("network.firewall.makerStream")}
        </TextLink>
      );
    case "database":
      return (
        <TextLink to="/databases/$engine/$name/connect" params={{ engine: maker.engine, name: maker.name }} size="ui" translate="no">
          {t("network.firewall.makerDatabase", { name: maker.name })}
        </TextLink>
      );
    case "dns":
      return (
        <TextLink to="/domains/dns/noust" size="ui">
          {t("network.firewall.makerDns")}
        </TextLink>
      );
    case "noust":
      return <span>{maker.note === "" ? t("network.firewall.makerNoust") : t("network.firewall.makerNoustNote", { note: maker.note })}</span>;
    case "other":
      return <span className="text-fg-muted">{maker.note === "" ? t("network.firewall.makerOther") : maker.note}</span>;
  }
}

function Rules({
  firewall,
  rows,
  onDelete,
  onMove,
}: {
  firewall: Firewall;
  rows: readonly PortRow[];
  onDelete: (rule: FirewallRule) => void;
  onMove: (rule: FirewallRule) => void;
}) {
  const t = useT();
  const mayChange = useCan("server.host_access");
  const offered = useNodeCapability(CHANGE_SOURCE_ROUTE).status === "available";
  const columns: Column<FirewallRule>[] = [
    { id: "for", header: t("network.firewall.forColumn"), card: "title", cell: (rule) => <Subject rule={rule} rows={rows} /> },
    {
      id: "port",
      header: t("network.firewall.portColumn"),
      width: "w-36",
      cell: (rule) => (
        <span className="flex items-center gap-2">
          <Mono>{rulePorts(rule)}</Mono>
          <span className="text-12 text-fg-muted">{rule.proto.toUpperCase()}</span>
          {rule.action === "allow" ? null : <Badge>{t("server.firewall.rules.deny")}</Badge>}
        </span>
      ),
    },
    {
      id: "from",
      header: t("network.firewall.fromColumn"),
      hideBelow: "sm",
      cell: (rule) => (rule.source === "any" || rule.source === "" ? t("server.firewall.rules.anywhere") : <Mono>{rule.source}</Mono>),
    },
    { id: "madeBy", header: t("network.firewall.madeByColumn"), hideBelow: "md", card: "meta", cell: (rule) => <Maker rule={rule} guard={firewall.protected_ports} /> },
  ];
  return (
    <DataTable
      columns={columns}
      rows={firewall.firewall.rules}
      getRowId={(rule) => rule.id}
      caption={t("server.firewall.rules.caption")}
      density="compact"
      empty={<EmptyState variant="inline" title={t("server.firewall.rules.none")} />}
      {...(mayChange
        ? {
            rowActions: (rule: FirewallRule) => {
              // The rule that keeps SSH open is never closed here, but where it admits from can be narrowed: the guard
              // of the server says if that would leave somebody outside.
              const closable = rule.known && isProtected(rule, firewall.protected_ports) === null;
              // A server before 4.3 sends no `source_changeable` (the type says it always does, as this build's server
              // does): nothing is offered that its API would not take.
              const movable = offered && rule.source_changeable;
              if (!closable && !movable) return null;
              return (
                <RowMenu name={`${rulePorts(rule)}/${rule.proto}`}>
                  {movable ? (
                    <MenuItem icon={<ArrowRightLeft />} onClick={() => onMove(rule)}>
                      {t("network.firewall.changeSource")}
                    </MenuItem>
                  ) : null}
                  {closable ? (
                    <MenuItem destructive icon={<ICONS.delete />} onClick={() => onDelete(rule)}>
                      {t("network.firewall.closePort")}
                    </MenuItem>
                  ) : null}
                </RowMenu>
              );
            },
          }
        : {})}
    />
  );
}

/** The firewall's page. */
export function FirewallPage() {
  const t = useT();
  const query = useQuery(firewallQuery());
  const ports = useQuery(portsQuery());
  const identity = useQuery(identityQuery());
  const queryClient = useQueryClient();
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  const open = useOpenPort();
  const mayChange = useCan("server.host_access");
  const hostname = identity.data?.hostname.hostname ?? "";
  const [opened, setOpened] = useState<Opened>(null);
  const data = query.data;
  const state = data?.firewall;
  const rows = portRows(ports.data?.claims ?? [], data?.ports ?? []);
  const run = async (call: () => Promise<{ job_id: string }>): Promise<void> => {
    const accepted = await call();
    jobs.track(accepted.job_id, "security");
  };

  const policy = state?.default_incoming ?? "";
  const line =
    state === undefined || state.backend === "none"
      ? undefined
      : /^(deny|reject|drop)/i.test(policy)
        ? t("network.firewall.lineDeny", { backend: state.backend })
        : t("network.firewall.lineOther", { backend: state.backend, policy });

  const primary =
    state === undefined ? undefined : state.active ? (
      <GuardedButton permission="server.host_access" variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => open.open()}>
        {t("network.openPort.title")}
      </GuardedButton>
    ) : state.installed ? (
      <GuardedButton permission="server.host_access" variant="primary" icon={<Power aria-hidden="true" />} onClick={() => setOpened({ kind: "enable" })}>
        {t("network.firewall.turnOn")}
      </GuardedButton>
    ) : undefined;

  const bypass = (data?.ports ?? []).filter((port) => port.verdict === "docker_bypass");
  const exposed = (data?.ports ?? []).filter((port) => port.verdict !== "docker_bypass" && port.risky !== "" && port.reachable);

  return (
    <MachinePage
      title={t("network.firewall.title")}
      {...(line !== undefined ? { description: line } : {})}
      header={{
        ...(state !== undefined && state.backend !== "none"
          ? { status: <StatusPill state={state.active ? "running" : "stopped"} label={state.active ? t("network.firewall.on") : t("network.firewall.off")} size="sm" /> }
          : {}),
        ...(primary !== undefined ? { primaryAction: primary } : {}),
        ...(state?.active === true && mayChange
          ? {
              overflow: (
                <MenuItem icon={<VERB_ICONS.turnOff />} onClick={() => setOpened({ kind: "disable" })}>
                  {t("network.firewall.turnOff")}
                </MenuItem>
              ),
            }
          : {}),
      }}
    >
      <NodeCapabilityGate capability={SERVER_CAPABILITY}>
        <ServerQueryState query={query} label={t("server.firewall.label")} skeleton={<SkeletonSection rows={4} />}>
          {(firewall) => (
            <div className="flex min-w-0 flex-col gap-6">
              <ReadOnlyLine permission="server.host_access" />
              {/* No firewall at all is a piece the server lacks, said where every other missing piece is said: with its install. */}
              {firewall.firewall.backend === "none" ? (
                <MissingSoftware
                  component="firewall"
                  onInstalled={() => void queryClient.invalidateQueries({ queryKey: firewallQuery().queryKey })}
                  fallback={<p className="text-14 text-fg-muted">{t("server.firewall.none")}</p>}
                />
              ) : null}
              {firewall.firewall.error !== "" ? <ServerErrorBlock compact error={{ detail: firewall.firewall.error }} title={t("server.firewall.readFailed")} /> : null}
              {bypass.length > 0 ? (
                <Notice tone="error" title={t("server.firewall.dockerTitle", { count: bypass.length })}>
                  <div className="flex flex-col gap-1.5">
                    <p>{t("server.firewall.dockerDescription")}</p>
                    <p>
                      <Mono>{bypass.map(portText).join(", ")}</Mono>
                    </p>
                  </div>
                </Notice>
              ) : null}
              {exposed.length > 0 ? (
                <Notice tone="error" title={t("server.firewall.exposedTitle", { count: exposed.length })}>
                  <div className="flex flex-col gap-1.5">
                    <p>{t("server.firewall.exposedDescription")}</p>
                    <p>
                      <Mono>{exposed.map((port) => `${port.risky} ${portText(port)}`).join(", ")}</Mono>
                    </p>
                  </div>
                </Notice>
              ) : null}
              {firewall.firewall.warnings.length + firewall.firewall.others.length > 0 ? (
                // What the firewall tool itself said is shown as it said it, not as a third banner.
                <SystemOutput label={t("server.firewall.toolSaid")}>{[...firewall.firewall.warnings, ...firewall.firewall.others].join("\n")}</SystemOutput>
              ) : null}
              {firewall.firewall.backend !== "none" ? (
                <SurfaceSection title={t("server.firewall.rules.title")} icon={OBJECT_ICONS.firewall} description={t("network.firewall.rulesLine")} flush>
                  <Rules firewall={firewall} rows={rows} onDelete={(rule) => setOpened({ kind: "delete", rule })} onMove={(rule) => setOpened({ kind: "source", rule })} />
                </SurfaceSection>
              ) : null}
              <ThreeFirewalls />
            </div>
          )}
        </ServerQueryState>
      </NodeCapabilityGate>

      {open.dialog}
      {opened?.kind === "enable" ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(next) => {
            if (!next) setOpened(null);
          }}
          {...typedHost(hostname)}
          destructive={false}
          size="md"
          title={t("server.firewall.enableTitle")}
          description={t("server.firewall.enableDescription")}
          actionLabel={t("server.firewall.enableAction")}
          onConfirm={() => run(() => request("post", "/api/server/security/firewall/enable"))}
        >
          <p className="text-13 text-fg-muted">{t("server.firewall.reverts")}</p>
        </ConfirmDialog>
      ) : null}
      {opened?.kind === "disable" ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(next) => {
            if (!next) setOpened(null);
          }}
          {...typedHost(hostname)}
          size="md"
          title={t("server.firewall.disableTitle")}
          description={t("server.firewall.disableDescription")}
          actionLabel={t("server.firewall.disableAction")}
          onConfirm={() => run(() => request("post", "/api/server/security/firewall/disable"))}
        >
          <p className="text-13 text-fg-muted">{t("server.firewall.revertsDisable")}</p>
        </ConfirmDialog>
      ) : null}
      {opened?.kind === "source" ? (
        <ChangeSourceDialog rule={opened.rule} guarded={isProtected(opened.rule, data?.protected_ports ?? []) !== null} onClose={() => setOpened(null)} />
      ) : null}
      {opened?.kind === "delete" ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(next) => {
            if (!next) setOpened(null);
          }}
          friction="simple"
          size="md"
          title={t("server.firewall.deleteTitle", { rule: `${rulePorts(opened.rule)}/${opened.rule.proto}` })}
          description={t("server.firewall.deleteDescription")}
          actionLabel={t("server.firewall.deleteAction")}
          onConfirm={() => run(() => request("delete", "/api/server/security/firewall/rules/{rule_id}", { params: { rule_id: opened.rule.id } }))}
        >
          <p className="text-13">
            <Mono>{opened.rule.spec}</Mono>
          </p>
          <p className="text-13 text-fg-muted">{t("server.firewall.reverts")}</p>
        </ConfirmDialog>
      ) : null}
    </MachinePage>
  );
}

