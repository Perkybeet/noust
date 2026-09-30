/**
 * The firewall against what actually listens: every port that answers, with what the firewall
 * does about it; the rules; the ports the anti-lockout guard always keeps open; and, loudly,
 * the two ways a port is open without anybody meaning it: Docker publishing around the
 * firewall, and a database answering the internet.
 *
 * Every change undoes itself unless it is confirmed from a new SSH login (PendingChanges), and
 * the guard refuses any change that would close SSH or a public console.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Menu, MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock } from "../errors";
import { firewallQuery, identityQuery } from "../queries";
import type { Firewall, FirewallRule, ListeningPort } from "../queries";
import { useServerJob } from "../serverJob";

/** A port that is open by accident: a database or internal service answering everyone, or Docker around the firewall. */
export function isExposed(port: Pick<ListeningPort, "verdict" | "risky" | "reachable">): boolean {
  return port.verdict === "docker_bypass" || (port.risky !== "" && port.reachable);
}

/** Exposed first, then what answers everyone, then the rest by port. */
export function sortPorts(ports: readonly ListeningPort[]): ListeningPort[] {
  const rank = (port: ListeningPort): number => (isExposed(port) ? 0 : port.reachable ? 1 : 2);
  return [...ports].sort((a, b) => rank(a) - rank(b) || a.port - b.port || a.proto.localeCompare(b.proto));
}

/** What the firewall does about a port: coloured only when it is a problem. */
function verdictView(t: T, port: ListeningPort): { state: Status | null; label: string } {
  if (port.verdict === "docker_bypass") return { state: "failed", label: t("server.firewall.verdict.dockerBypass") };
  if (port.risky !== "" && port.reachable) return { state: "failed", label: t("server.firewall.verdict.exposed") };
  switch (port.verdict) {
    case "local":
      return { state: null, label: t("server.firewall.verdict.local") };
    case "blocked":
      return { state: null, label: t("server.firewall.verdict.blocked") };
    case "open":
      return { state: null, label: t("server.firewall.verdict.open") };
    case "open_to":
      return { state: null, label: t("server.firewall.verdict.openTo", { sources: port.sources.join(", ") }) };
    case "no_firewall":
      return { state: "warning", label: t("server.firewall.verdict.noFirewall") };
    default:
      return { state: null, label: port.verdict };
  }
}

function portText(port: Pick<ListeningPort, "port" | "proto">): string {
  return `${String(port.port)}/${port.proto}`;
}

function rulePorts(rule: FirewallRule): string {
  if (rule.ports.length === 0) return "*";
  return rule.ports.map(([from, to]) => (from === to ? String(from) : `${String(from)}:${String(to)}`)).join(", ");
}

function Ports({ firewall }: { firewall: Firewall }) {
  const t = useT();
  const columns: Column<ListeningPort>[] = [
    {
      id: "port",
      header: t("server.firewall.ports.port"),
      card: "title",
      cell: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          <Mono>{portText(row)}</Mono>
          {row.risky !== "" ? <Badge>{row.risky}</Badge> : null}
        </span>
      ),
      sortValue: (row) => row.port,
    },
    {
      id: "verdict",
      header: t("server.firewall.ports.verdict"),
      width: "w-56",
      card: "status",
      cell: (row) => {
        const view = verdictView(t, row);
        return view.state !== null ? <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" /> : <span className="text-13 text-fg-muted">{view.label}</span>;
      },
    },
    { id: "address", header: t("server.firewall.ports.address"), hideBelow: "sm", cell: (row) => <Mono tone="muted">{row.address}</Mono> },
    {
      id: "process",
      header: t("server.firewall.ports.process"),
      hideBelow: "md",
      cell: (row) =>
        row.docker ? (
          <Mono tone="muted">{row.docker.project ? `${row.docker.project} · ${row.docker.container}` : row.docker.container}</Mono>
        ) : row.process ? (
          <Mono tone="muted">{row.process}</Mono>
        ) : (
          <EmptyCell reason={t("server.firewall.ports.noProcess")} />
        ),
    },
  ];
  return (
    <Card level={2} title={t("server.firewall.ports.title")} description={t("server.firewall.ports.description")} padding="none">
      {firewall.ports_error ? (
        <div className="px-5 pb-4">
          <ServerErrorBlock compact error={{ detail: firewall.ports_error }} title={t("server.firewall.ports.failed")} />
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={sortPorts(firewall.ports)}
          getRowId={(row) => `${row.proto}:${row.address}:${String(row.port)}`}
          caption={t("server.firewall.ports.caption")}
          mobile="cards"
          density="compact"
          empty={<EmptyState variant="inline" title={t("server.firewall.ports.none")} />}
        />
      )}
    </Card>
  );
}

function Rules({ firewall, onDelete }: { firewall: Firewall; onDelete: (rule: FirewallRule) => void }) {
  const t = useT();
  const columns: Column<FirewallRule>[] = [
    {
      id: "ports",
      header: t("server.firewall.rules.ports"),
      card: "title",
      cell: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          <Mono>{`${rulePorts(row)}/${row.proto}`}</Mono>
          {row.service ? <Badge>{row.service}</Badge> : null}
          {row.noust ? <Badge>{t("server.firewall.rules.noust")}</Badge> : null}
        </span>
      ),
    },
    { id: "action", header: t("server.firewall.rules.action"), cell: (row) => (row.action === "allow" ? t("server.firewall.rules.allow") : row.action === "deny" ? t("server.firewall.rules.deny") : row.action) },
    { id: "source", header: t("server.firewall.rules.source"), cell: (row) => (row.source === "any" || row.source === "" ? t("server.firewall.rules.anywhere") : <Mono>{row.source}</Mono>) },
    { id: "comment", header: t("server.firewall.rules.comment"), hideBelow: "md", cell: (row) => (row.comment !== "" ? <span className="text-fg-muted">{row.comment}</span> : <EmptyCell />) },
  ];
  return (
    <Card level={2} title={t("server.firewall.rules.title")} padding="none">
      <DataTable
        columns={columns}
        rows={firewall.firewall.rules}
        getRowId={(row) => row.id}
        caption={t("server.firewall.rules.caption")}
        mobile="cards"
        density="compact"
        empty={<EmptyState variant="inline" title={t("server.firewall.rules.none")} />}
        rowActions={(row) =>
          row.known ? (
            <Menu align="end" trigger={<IconButton label={t("server.firewall.rules.actionsFor", { rule: `${rulePorts(row)}/${row.proto}` })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
              <MenuItem destructive icon={<ICONS.delete />} onClick={() => onDelete(row)}>
                {t("server.firewall.rules.delete")}
              </MenuItem>
            </Menu>
          ) : null
        }
      />
    </Card>
  );
}

type Opened = { kind: "enable" } | { kind: "disable" } | { kind: "add" } | { kind: "delete"; rule: FirewallRule } | null;

function Protected({ firewall }: { firewall: Firewall }) {
  const t = useT();
  if (firewall.protected_ports.length === 0) return null;
  return (
    <p className="text-13 text-fg-muted">
      {t.rich("server.firewall.protected", {
        ports: <Mono>{firewall.protected_ports.map((port) => String(port.port)).join(", ")}</Mono>,
      })}{" "}
      {firewall.protected_ports.map((port) => port.reason).join(" ")}
    </p>
  );
}

/** The firewall view of the Security tab. */
export function FirewallView() {
  const t = useT();
  const jobs = useServerJob();
  const query = useQuery(firewallQuery());
  const identity = useQuery(identityQuery());
  const hostname = identity.data?.hostname.hostname ?? "";
  const [opened, setOpened] = useState<Opened>(null);
  const [rule, setRule] = useState({ action: "allow", port: "", proto: "tcp", source: "any", comment: "" });

  if (query.isError && query.data === undefined) {
    return <ServerErrorBlock error={query.error} title={t("server.firewall.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;
  }
  if (query.data === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-4">
        <span className="sr-only">{t("server.firewall.loading")}</span>
        <Skeleton className="h-5 w-72" />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }
  const data = query.data;
  const state = data.firewall;
  const bypass = data.ports.filter((port) => port.verdict === "docker_bypass");
  const exposed = data.ports.filter((port) => port.verdict !== "docker_bypass" && port.risky !== "" && port.reachable);
  const run = async (call: () => Promise<{ job_id: string }>): Promise<void> => {
    const accepted = await call();
    jobs.track(accepted.job_id, "security");
  };
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-14 text-fg-muted">
          {state.backend === "none" ? (
            t("server.firewall.none")
          ) : (
            <>
              <Mono tone="default">{state.backend}</Mono>
              <span>{state.active ? t("server.firewall.active", { policy: state.default_incoming }) : t("server.firewall.inactive")}</span>
            </>
          )}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {state.active ? (
            <>
              <Button onClick={() => setOpened({ kind: "disable" })}>{t("server.firewall.disable")}</Button>
              <Button icon={<ICONS.add aria-hidden="true" />} onClick={() => setOpened({ kind: "add" })}>
                {t("server.firewall.addRule")}
              </Button>
            </>
          ) : state.installed ? (
            <Button variant="primary" onClick={() => setOpened({ kind: "enable" })}>
              {t("server.firewall.enable")}
            </Button>
          ) : null}
        </div>
      </div>
      {state.error !== "" ? <ServerErrorBlock compact error={{ detail: state.error }} title={t("server.firewall.readFailed")} /> : null}
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
      {[...state.warnings, ...state.others].map((warning) => (
        <Notice key={warning} tone="warning">
          {warning}
        </Notice>
      ))}
      <Ports firewall={data} />
      {state.backend !== "none" ? <Rules firewall={data} onDelete={(target) => setOpened({ kind: "delete", rule: target })} /> : null}
      <Protected firewall={data} />
      <CommandHint command="noust server security firewall status" label={t("server.fromTerminal")} />

      {opened?.kind === "enable" ? (
        <ActionDialog
          title={t("server.firewall.enableTitle")}
          description={t("server.firewall.enableDescription")}
          actionLabel={t("server.firewall.enableAction")}
          confirmText={hostname !== "" ? hostname : undefined}
          onClose={() => setOpened(null)}
          onConfirm={() => run(() => request("post", "/api/server/security/firewall/enable"))}
        >
          <Protected firewall={data} />
          <Notice>{t("server.firewall.reverts")}</Notice>
        </ActionDialog>
      ) : null}
      {opened?.kind === "disable" ? (
        <ActionDialog
          title={t("server.firewall.disableTitle")}
          description={t("server.firewall.disableDescription")}
          actionLabel={t("server.firewall.disableAction")}
          destructive
          confirmText={hostname !== "" ? hostname : undefined}
          onClose={() => setOpened(null)}
          onConfirm={() => run(() => request("post", "/api/server/security/firewall/disable"))}
        >
          <Notice>{t("server.firewall.revertsDisable")}</Notice>
        </ActionDialog>
      ) : null}
      {opened?.kind === "delete" ? (
        <ActionDialog
          title={t("server.firewall.deleteTitle", { rule: `${rulePorts(opened.rule)}/${opened.rule.proto}` })}
          description={t("server.firewall.deleteDescription")}
          actionLabel={t("server.firewall.deleteAction")}
          destructive
          onClose={() => setOpened(null)}
          onConfirm={() => run(() => request("delete", "/api/server/security/firewall/rules/{rule_id}", { params: { rule_id: opened.rule.id } }))}
        >
          <p className="text-13">
            <Mono>{opened.rule.spec}</Mono>
          </p>
          <Notice>{t("server.firewall.reverts")}</Notice>
        </ActionDialog>
      ) : null}
      {opened?.kind === "add" ? (
        <ActionDialog
          title={t("server.firewall.addTitle")}
          description={t("server.firewall.addDescription")}
          actionLabel={t("server.firewall.addAction")}
          onClose={() => setOpened(null)}
          onConfirm={() =>
            run(() =>
              request("post", "/api/server/security/firewall/rules", {
                body: { action: rule.action, port: Number(rule.port), proto: rule.proto, source: rule.source.trim() === "" ? "any" : rule.source.trim(), comment: rule.comment },
              }),
            )
          }
        >
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label={t("server.firewall.form.action")} nativeLabel={false}>
              <Select
                value={rule.action}
                onValueChange={(action) => setRule((current) => ({ ...current, action }))}
                options={[
                  { value: "allow", label: t("server.firewall.rules.allow") },
                  { value: "deny", label: t("server.firewall.rules.deny") },
                ]}
              />
            </Field>
            <Field label={t("server.firewall.form.protocol")} nativeLabel={false}>
              <Select
                value={rule.proto}
                mono
                onValueChange={(proto) => setRule((current) => ({ ...current, proto }))}
                options={[
                  { value: "tcp", label: "tcp" },
                  { value: "udp", label: "udp" },
                ]}
              />
            </Field>
            <Field label={t("server.firewall.form.port")}>
              <Input mono inputMode="numeric" value={rule.port} onValueChange={(port: string) => setRule((current) => ({ ...current, port }))} autoComplete="off" />
            </Field>
            <Field label={t("server.firewall.form.source")} description={t("server.firewall.form.sourceHelp")}>
              <Input mono value={rule.source} onValueChange={(source: string) => setRule((current) => ({ ...current, source }))} autoComplete="off" spellCheck={false} />
            </Field>
          </div>
          <Field label={t("server.firewall.form.comment")} optional>
            <Input value={rule.comment} onValueChange={(comment: string) => setRule((current) => ({ ...current, comment }))} autoComplete="off" />
          </Field>
          <Notice>{t("server.firewall.reverts")}</Notice>
        </ActionDialog>
      ) : null}
    </div>
  );
}
