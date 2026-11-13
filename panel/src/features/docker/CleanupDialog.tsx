/**
 * Cleaning Docker up, as a plan: what would go, whose it is and how much it holds, read before anything is removed (the
 * server's token names this exact reading, so what is applied is what was shown; if the machine changed in between, nothing is
 * removed and the plan is read again). What is safe is ticked and the rest is not: a stopped container nobody made, a volume
 * (which asks for its name, because what is lost is the data and not the name). What an application keeps to go back to is
 * said as kept, never offered as unused.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { request } from "../../api/client";
import { dockerCleanupPlanQuery } from "../../api/queries/docker";
import type { DockerPlanItem } from "../../api/queries/docker";
import { QueryState } from "../../components/page/QueryState";
import { Subsection } from "../../components/page/Subsection";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import type { Accepted } from "../databases/jobs";
import { OwnerCell } from "./pieces";

/** The kinds a plan has, in the order they are drawn: the safest first, the data last. */
const KINDS = ["image", "cache", "container", "volume", "network"] as const;

/** What the plan would free if everything ticked went: the sum of what each ticked item holds. */
export function tickedBytes(items: readonly DockerPlanItem[], ticked: ReadonlySet<string>): number {
  return items.filter((item) => ticked.has(item.id)).reduce((total, item) => total + (item.size_bytes ?? 0), 0);
}

/** The names that still have to be typed: the ticked volumes the server wants a name for, whose typed text is not their name. */
export function missingNames(items: readonly DockerPlanItem[], ticked: ReadonlySet<string>, typed: Readonly<Record<string, string>>): string[] {
  return items.filter((item) => ticked.has(item.id) && item.type_name && (typed[item.id] ?? "").trim() !== item.name).map((item) => item.name);
}

function whyText(t: T, item: DockerPlanItem): string {
  switch (item.why) {
    case "dangling":
      return t("docker.cleanup.why.dangling");
    case "unused":
      return item.kind === "volume" ? t("docker.cleanup.why.unusedVolume") : item.kind === "network" ? t("docker.cleanup.why.unusedNetwork") : t("docker.cleanup.why.unused");
    case "stopped":
      return t("docker.cleanup.why.stopped");
    case "old_cache":
      return t("docker.cleanup.why.oldCache");
    default:
      return item.why;
  }
}

function Row({ item, checked, onChange, typed, onType }: { item: DockerPlanItem; checked: boolean; onChange: (checked: boolean) => void; typed: string; onType: (text: string) => void }) {
  const t = useT();
  const size = item.size_bytes !== null && item.size_bytes !== undefined ? formatBytes(item.size_bytes, t.locale) : null;
  const label = item.kind === "cache" ? t("docker.cleanup.cacheName") : item.name;
  return (
    <RowList.Item>
     <div className="flex min-w-0 flex-col gap-2">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <Checkbox
          checked={checked}
          onCheckedChange={onChange}
          label={item.kind === "cache" || item.anonymous ? label : <Mono>{label}</Mono>}
          description={
            <span className="flex flex-col gap-1">
              <span>{whyText(t, item)}</span>
              {item.kind !== "cache" ? <OwnerCell owner={item.owner} /> : null}
            </span>
          }
        />
        {size !== null ? <Mono tone="muted">{size}</Mono> : null}
      </div>
      {checked && item.type_name ? (
        <Field label={t("docker.cleanup.typeName", { name: item.name })}>
          <Input value={typed} onChange={(event) => onType(event.target.value)} autoComplete="off" spellCheck={false} mono />
        </Field>
      ) : null}
     </div>
    </RowList.Item>
  );
}

export interface CleanupDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the job the server queued, so the page that asked follows it. */
  onQueued: (accepted: Accepted) => void;
}

export function CleanupDialog({ open, onOpenChange, onQueued }: CleanupDialogProps) {
  const t = useT();
  const { node } = useNode();
  const plan = useQuery({ ...dockerCleanupPlanQuery(), enabled: open });
  const data = plan.data;
  const items = useMemo(() => data?.items ?? [], [data]);
  // What the server ticked by default is where the operator starts. What was chosen belongs to the plan it was chosen in (its token):
  // a plan read again starts over from its own defaults, so nothing chosen on an old reading is applied to a new one.
  const token = data?.token;
  const defaults = useMemo(() => new Set(items.filter((item) => item.tick).map((item) => item.id)), [items]);
  const [chosen, setChosen] = useState<{ token: string; ticked: ReadonlySet<string>; typed: Readonly<Record<string, string>> } | null>(null);
  const mine = chosen !== null && chosen.token === token ? chosen : null;
  const ticked = mine?.ticked ?? defaults;
  const typed = mine?.typed ?? {};
  const change = (next: { ticked?: ReadonlySet<string>; typed?: Readonly<Record<string, string>> }): void => {
    if (token === undefined) return;
    setChosen({ token, ticked: next.ticked ?? ticked, typed: next.typed ?? typed });
  };
  const toggle = (id: string, on: boolean): void => {
    const next = new Set(ticked);
    if (on) next.add(id);
    else next.delete(id);
    change({ ticked: next });
  };
  const missing = missingNames(items, ticked, typed);
  const freed = tickedBytes(items, ticked);
  const groups = KINDS.map((kind) => ({ kind, rows: items.filter((item) => item.kind === kind) })).filter((group) => group.rows.length > 0);
  const nothing = data !== undefined && (!data.available || items.length === 0);
  return (
    <ConfirmDialog
      friction="simple"
      destructive
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={t("docker.cleanup.title")}
      description={nothing ? t("docker.cleanup.descriptionNothing") : t("docker.cleanup.description")}
      actionLabel={t("docker.cleanup.action", { size: formatBytes(freed, t.locale) })}
      ready={data !== undefined && !nothing && ticked.size > 0 && missing.length === 0}
      {...(node !== null ? { server: node } : {})}
      onConfirm={async () => {
        if (token === undefined) return;
        const accepted = await request("post", "/api/docker/cleanup", {
          body: { token, select: [...ticked], confirm_names: Object.fromEntries(items.filter((item) => ticked.has(item.id) && item.type_name).map((item) => [item.name, (typed[item.id] ?? "").trim()])) },
        });
        onQueued(accepted);
      }}
    >
      <QueryState
        query={plan}
        sentences={{ loading: t("docker.cleanup.loading"), failed: t("docker.cleanup.failed"), refreshFailed: t("docker.cleanup.refreshFailed") }}
        skeleton={<Skeleton className="h-40 w-full" />}
      >
        {() => (
          <div className="flex min-w-0 flex-col gap-5">
            {groups.map((group) => (
              <Subsection key={group.kind} title={t(`docker.cleanup.groups.${group.kind}`)}>
                <RowList aria-label={t(`docker.cleanup.groups.${group.kind}`)}>
                  {group.rows.map((item) => (
                    <Row key={item.id} item={item} checked={ticked.has(item.id)} onChange={(on) => toggle(item.id, on)} typed={typed[item.id] ?? ""} onType={(text) => change({ typed: { ...typed, [item.id]: text } })} />
                  ))}
                </RowList>
              </Subsection>
            ))}
            {(data?.kept ?? []).length > 0 ? (
              <Disclosure label={t("docker.cleanup.kept", { count: data?.kept?.length ?? 0 })}>
                <ul className="flex flex-col gap-2 text-13">
                  {(data?.kept ?? []).map((kept) => (
                    <li key={kept.name} className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3">
                      <span className="flex min-w-0 items-baseline gap-2">
                        <Mono>{kept.name}</Mono>
                        <span className="text-fg-muted">{kept.why === "rollback" ? t("docker.cleanup.keptRollback") : t("docker.cleanup.keptNoust")}</span>
                      </span>
                      {kept.size_bytes !== null && kept.size_bytes !== undefined ? <Mono tone="muted">{formatBytes(kept.size_bytes, t.locale)}</Mono> : null}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            ) : null}
          </div>
        )}
      </QueryState>
    </ConfirmDialog>
  );
}
