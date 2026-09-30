/**
 * Brute force: fail2ban, which bans the addresses that keep guessing passwords. Whether it runs,
 * its jails and who they ban now, lifting a ban, and installing it with an sshd jail that never
 * bans the address the console is used from. On RHEL rebuilds it comes from EPEL, a third-party
 * repository, which is a separate yes.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock, explainServerError } from "../errors";
import { fail2banQuery } from "../queries";
import type { Fail2ban } from "../queries";
import { useServerJob } from "../serverJob";

type Jail = Fail2ban["jails"][number];

function Banned({ jail }: { jail: Jail }) {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const unban = useMutation({
    mutationFn: (address: string) => request("post", "/api/server/security/fail2ban/unban", { body: { address, jail: jail.name } }),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "security"),
    onError: (error) => reportActionError(t("server.bans.unbanFailed"), explainServerError(t, error, node)),
  });
  if (jail.banned.length === 0) return <span className="text-13 text-fg-muted">{t("server.bans.nobodyBanned")}</span>;
  return (
    <ul aria-label={t("server.bans.bannedIn", { jail: jail.name })} className="flex flex-wrap gap-2">
      {jail.banned.map((address) => (
        <li key={address} className="flex items-center gap-1 rounded-control border border-border bg-bg-sunken py-0.5 pr-0.5 pl-2">
          <Mono>{address}</Mono>
          <Button size="sm" variant="ghost" disabled={unban.isPending} onClick={() => unban.mutate(address)} aria-label={t("server.bans.unbanAddress", { address })}>
            {t("server.bans.unban")}
          </Button>
        </li>
      ))}
    </ul>
  );
}

function Jails({ state }: { state: Fail2ban }) {
  const t = useT();
  const columns: Column<Jail>[] = [
    { id: "name", header: t("server.bans.jail"), card: "title", cell: (row) => <Mono>{row.name}</Mono> },
    { id: "failed", header: t("server.bans.failedNow"), align: "end", mono: true, cell: (row) => formatCount(row.currently_failed, t.locale) },
    { id: "banned", header: t("server.bans.bannedNow"), align: "end", mono: true, cell: (row) => formatCount(row.currently_banned, t.locale) },
    { id: "total", header: t("server.bans.bannedTotal"), align: "end", hideBelow: "sm", mono: true, cell: (row) => formatCount(row.total_banned, t.locale) },
    { id: "reads", header: t("server.bans.reads"), hideBelow: "lg", cell: (row) => <Mono tone="muted">{row.reads}</Mono> },
  ];
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <Card level={2} title={t("server.bans.jailsTitle")} padding="none">
        <DataTable
          columns={columns}
          rows={state.jails}
          getRowId={(row) => row.name}
          caption={t("server.bans.jailsCaption")}
          mobile="cards"
          density="compact"
          empty={<EmptyState variant="inline" title={t("server.bans.noJails")} />}
        />
      </Card>
      {state.jails.length > 0 ? (
        <Card level={2} title={t("server.bans.bannedTitle")} padding="sm">
          <div className="flex flex-col gap-4">
            {state.jails.map((jail) => (
              <div key={jail.name} className="flex min-w-0 flex-col gap-2">
                <Mono tone="muted">{jail.name}</Mono>
                <Banned jail={jail} />
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}

/** The brute-force view of the Security tab. */
export function BansView() {
  const t = useT();
  const jobs = useServerJob();
  const query = useQuery(fail2banQuery());
  const [installing, setInstalling] = useState(false);
  const [epel, setEpel] = useState(false);
  if (query.isError && query.data === undefined) {
    return <ServerErrorBlock error={query.error} title={t("server.bans.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;
  }
  if (query.data === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-4">
        <span className="sr-only">{t("server.bans.loading")}</span>
        <Skeleton className="h-40 w-full rounded-card" />
      </div>
    );
  }
  const state = query.data;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-14 text-fg-muted">
          {state.installed ? (
            <StatusPill state={state.running ? "running" : "stopped"} label={state.running ? t("server.bans.running") : t("server.bans.stopped")} appearance="inline" />
          ) : (
            <span>{t("server.bans.notInstalled")}</span>
          )}
        </div>
        {!state.installed && state.install_supported ? (
          <Button variant="primary" disabled={jobs.busy} onClick={() => setInstalling(true)}>
            {t("server.bans.install")}
          </Button>
        ) : null}
      </div>
      {state.error !== "" ? <ServerErrorBlock compact error={{ detail: state.error }} title={t("server.bans.readFailed")} /> : null}
      {state.substitutes.length > 0 ? <Notice>{t.rich("server.bans.substitutes", { tools: <Mono>{state.substitutes.join(", ")}</Mono> })}</Notice> : null}
      {!state.installed ? (
        <Notice title={t("server.bans.whyTitle")}>
          <div className="flex flex-col gap-1.5">
            <p>{t("server.bans.why")}</p>
            {!state.install_supported && state.install_hint !== "" ? <p>{state.install_hint}</p> : null}
          </div>
        </Notice>
      ) : (
        <Jails state={state} />
      )}
      <CommandHint command="noust server security fail2ban status" label={t("server.fromTerminal")} />
      {installing ? (
        <ActionDialog
          title={t("server.bans.installTitle")}
          description={t("server.bans.installDescription")}
          actionLabel={t("server.bans.installAction")}
          onClose={() => setInstalling(false)}
          onConfirm={async () => {
            const accepted = await request("post", "/api/server/security/fail2ban/install", { body: { epel } });
            jobs.track(accepted.job_id, "security");
          }}
        >
          {state.needs_epel ? <Checkbox label={t("server.fix.epelLabel")} description={t("server.fix.epelDescription")} checked={epel} onCheckedChange={setEpel} /> : null}
          {state.install_hint !== "" ? <p className="text-13 text-fg-muted">{state.install_hint}</p> : null}
        </ActionDialog>
      ) : null}
    </div>
  );
}
