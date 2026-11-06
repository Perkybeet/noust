/**
 * Blocks: fail2ban, which blocks the addresses that keep guessing passwords. The question people come with is "someone
 * I trust cannot get in", so what is blocked now comes first, with a search by address and a button that lifts each
 * block; below it, what each jail watches for and how much it has done. Whether it is installed is the registry's to
 * say, in the notice every missing piece uses.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { request } from "../../../api/client";
import { FilterBar } from "../../../components/page/FilterBar";
import { Can, ReadOnlyLine } from "../../../components/page/permissions";
import { StatTile } from "../../../components/page/StatTile";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Setting } from "../../../components/ui/Setting";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ServerErrorBlock, explainServerError, useServerConfirm, ServerQueryState } from "../errors";
import { MissingSoftware } from "../software/MissingSoftware";
import { fail2banQuery } from "../queries";
import type { Fail2ban } from "../queries";
import { useServerJob } from "../serverJob";

type Jail = Fail2ban["jails"][number];

interface Block {
  address: string;
  jail: string;
}

/** What a jail watches, in the words of the thing it protects; a jail Noust has no word for keeps its own name. */
export function jailName(t: T, jail: string): string {
  if (jail === "sshd" || jail.startsWith("sshd-")) return t("server.bans.jails.ssh");
  if (/^(?:nginx|apache|botsearch|http)/.test(jail)) return t("server.bans.jails.web");
  return jail;
}

/** Every address blocked now with the jail that blocked it, the same address once per jail. */
export function blocksOf(jails: readonly Jail[]): Block[] {
  return jails.flatMap((jail) => jail.banned.map((address) => ({ address, jail: jail.name })));
}

/** The blocks that match what was typed: part of an address, or of the name of what blocked it. */
export function matchBlocks(blocks: readonly Block[], query: string, t: T): Block[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return [...blocks];
  return blocks.filter((block) => block.address.toLowerCase().includes(needle) || jailName(t, block.jail).toLowerCase().includes(needle));
}

/** Lifts one block: what a person does when somebody they trust was blocked. */
function useUnblock() {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  return useMutation({
    mutationFn: (block: Block) => request("post", "/api/server/security/fail2ban/unban", { body: { address: block.address, jail: block.jail } }),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "security"),
    onError: (error) => reportActionError(t("server.bans.unbanFailed"), explainServerError(t, error, node)),
  });
}

function BlockedNow({ state }: { state: Fail2ban }) {
  const t = useT();
  const [query, setQuery] = useState("");
  const unblock = useUnblock();
  const blocks = useMemo(() => blocksOf(state.jails), [state.jails]);
  const shown = useMemo(() => matchBlocks(blocks, query, t), [blocks, query, t]);
  const columns: Column<Block>[] = [
    { id: "address", header: t("server.bans.address"), card: "title", sortValue: (row) => row.address, cell: (row) => <Mono>{row.address}</Mono> },
    { id: "by", header: t("server.bans.by"), hideBelow: "sm", sortValue: (row) => jailName(t, row.jail), cell: (row) => <span className="text-13 text-fg">{jailName(t, row.jail)}</span> },
  ];
  return (
    <SurfaceSection
      title={t("server.bans.nowTitle")}
      icon={OBJECT_ICONS.protection}
      description={t("server.bans.nowDescription")}
      flush={blocks.length > 0}
      actions={
        blocks.length > 0 ? (
          <FilterBar label={t("server.bans.filterLabel")} search={{ value: query, onChange: setQuery, label: t("server.bans.searchLabel"), placeholder: t("server.bans.searchPlaceholder") }} />
        ) : undefined
      }
    >
      {blocks.length === 0 ? (
        <EmptyState variant="inline" title={t("server.bans.nobodyBlocked")} />
      ) : (
        <DataTable
          embedded
          columns={columns}
          rows={shown}
          getRowId={(row) => `${row.jail}:${row.address}`}
          caption={t("server.bans.nowCaption")}
          density="compact"
          pagination="blocks"
          rowActions={(row) => (
            <Can permission="server.manage">
              <Button size="sm" variant="ghost" disabled={unblock.isPending} aria-label={t("server.bans.unbanAddress", { address: row.address })} onClick={() => unblock.mutate(row)}>
                {t("server.bans.unban")}
              </Button>
            </Can>
          )}
          empty={<EmptyState variant="inline" title={t("server.bans.noMatch")} />}
        />
      )}
    </SurfaceSection>
  );
}

/** What each jail watches and what it has done: one tile each, with the blocks now and since it started. */
function Jails({ state }: { state: Fail2ban }) {
  const t = useT();
  if (state.jails.length === 0) return <EmptyState variant="inline" title={t("server.bans.noJails")} />;
  return (
    <SurfaceSection title={t("server.bans.whenTitle")} icon={OBJECT_ICONS.firewall} description={t("server.bans.whenDescription")}>
      <ul aria-label={t("server.bans.jailsCaption")} className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {state.jails.map((jail) => (
          <li key={jail.name} className="min-w-0">
            <StatTile
              label={jailName(t, jail.name)}
              value={t("server.bans.blockedNow", { count: jail.currently_banned })}
              detail={t("server.bans.jailDetail", { total: formatCount(jail.total_banned, t.locale), failed: formatCount(jail.currently_failed, t.locale) })}
              detailLines={2}
              className="h-full"
            />
          </li>
        ))}
      </ul>
    </SurfaceSection>
  );
}

/** Watching the web server's logs as well: addresses that look for holes (a `.env`, a login page that is not there) are blocked too. */
function WebWatch({ state }: { state: Fail2ban }) {
  const t = useT();
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  const [asking, setAsking] = useState(false);
  if (!state.running || !state.web_available) return null;
  return (
    <SurfaceSection title={t("server.bans.web.title")} icon={OBJECT_ICONS.log}>
      <Can permission="server.manage">
        <Setting label={t("server.bans.web.label")} help={t("server.bans.web.help")} checked={state.web_jails} onCheckedChange={() => setAsking(true)} />
      </Can>
      {asking ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(open) => {
            if (!open) setAsking(false);
          }}
          friction="simple"
          destructive={false}
          size="md"
          title={state.web_jails ? t("server.bans.web.stopTitle") : t("server.bans.web.startTitle")}
          description={state.web_jails ? t("server.bans.web.stopDescription") : t("server.bans.web.startDescription")}
          actionLabel={state.web_jails ? t("server.bans.web.stop") : t("server.bans.web.start")}
          onConfirm={async () => {
            // design-exception: write-without-permission opened from a switch drawn only for server.manage
            const accepted = await request("post", "/api/server/security/fail2ban/web", { body: { enable: !state.web_jails } });
            jobs.track(accepted.job_id, "security");
          }}
        />
      ) : null}
    </SurfaceSection>
  );
}

function Bans({ state }: { state: Fail2ban }) {
  const t = useT();
  if (!state.installed) {
    // The registry says what is missing and installs it; where it cannot (a system Noust cannot install on), the page's own words.
    return (
      <div className="flex min-w-0 flex-col gap-4">
        <MissingSoftware
          component="fail2ban"
          fallback={
            <div className="flex flex-col gap-1 text-13 text-fg-muted">
              <p className="font-medium text-fg">{t("server.bans.whyTitle")}</p>
              <p>{t("server.bans.why")}</p>
              {!state.install_supported && state.install_hint !== "" ? <p>{state.install_hint}</p> : null}
            </div>
          }
        />
        {state.substitutes.length > 0 ? <p className="text-13 text-fg-muted">{t.rich("server.bans.substitutes", { tools: <Mono>{state.substitutes.join(", ")}</Mono> })}</p> : null}
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ReadOnlyLine permission="server.manage" />
      {state.error !== "" ? <ServerErrorBlock compact error={{ detail: state.error }} title={t("server.bans.readFailed")} /> : null}
      <BlockedNow state={state} />
      <Jails state={state} />
      <WebWatch state={state} />
    </div>
  );
}

/** The Blocks section. */
export function BansView() {
  const t = useT();
  const query = useQuery(fail2banQuery());
  return (
    <ServerQueryState query={query} label={t("server.bans.label")} skeleton={<SkeletonCard lines={4} />}>
      {(state) => <Bans state={state} />}
    </ServerQueryState>
  );
}
