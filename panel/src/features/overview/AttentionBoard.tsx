import { Link } from "@tanstack/react-router";
import { useState } from "react";

import type { OverviewAttentionItem } from "../../api/queries/overview";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { useAnnounceChange } from "../../components/page/useAnnounceChange";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { textLinkClassName } from "../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { AttentionActionButton } from "../inbox/AttentionActionButton";
import { validUntilText, reasonText } from "./attentionWords";
import { attentionTarget } from "./attentionTarget";
import type { AttentionTarget } from "./attentionTarget";
import type { HomeAttention } from "./homeData";
import { sinceOf } from "./homeData";

/** How many cards show before "Show all": three rows of two, which is as much as a person reads before choosing. */
export const ATTENTION_SHOWN = 6;

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/**
 * Where the name of a card leads: an application's own page, so that the card's button can be the deployment's log or
 * the diagnosis without the name and the button opening the same screen; anything else, where it is dealt with.
 */
function nameTarget(item: OverviewAttentionItem): AttentionTarget | null {
  const domain = text(item.subject["domain"]);
  if (item.subject["kind"] === "app" && domain !== null) return { to: "/apps/$domain", params: { domain } };
  return attentionTarget(item);
}

/** What names the item: an application or a certificate's domain is a name, a unit or a rule a system value. */
function Subject({ t, item, node }: { t: T; item: OverviewAttentionItem; node: string | null | undefined }) {
  const kind = item.subject["kind"];
  const system = kind === "unit" || kind === "alert" || kind === "probe" || kind === "recipe";
  const pid = item.subject["pid"];
  const label = system ? (
    <Mono>{item.title}</Mono>
  ) : (
    <>
      <span translate="no">{item.title}</span>
      {typeof pid === "number" ? (
        <Mono tone="faint" className="ml-1.5 font-normal">
          {t("overview.attention.pid", { pid: String(pid) })}
        </Mono>
      ) : null}
    </>
  );
  const target = nameTarget(item);
  if (target === null) return <span className="min-w-0 text-13 font-medium break-words text-fg">{label}</span>;
  return (
    <Link
      to={target.to as never}
      {...(target.params !== undefined ? { params: target.params as never } : {})}
      {...(target.search !== undefined || node !== undefined ? { search: { ...target.search, node: node ?? undefined } as never } : {})}
      className={textLinkClassName("ui", "min-w-0 break-words", "row")}
    >
      {label}
    </Link>
  );
}

/**
 * One thing that needs the operator, as a card tinted only by its state: what it is about (a link to
 * where it is dealt with), what is wrong in words, the system's own output when there is one, and where
 * and since when. The one button is the fix or the screen that fixes it, the inbox's (`attentionAction`).
 */
function Entry({ t, entry, showServer }: { t: T; entry: HomeAttention; showServer: boolean }) {
  const { item, server } = entry;
  const fail = item.severity === "fail";
  const state = fail ? "failed" : "warning";
  const since = sinceOf(item);
  // What opens a screen opens it on the server the item is on; what is the console's own opens its own.
  const node = server === null ? undefined : server.node;
  return (
    <Card as="li" tone={fail ? "fail" : "warn"} padding="sm">
      <div className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-3">
        <div className="flex min-w-0 flex-1 basis-56 items-start gap-3">
          <span className={cx("flex h-5 shrink-0 items-center", stateTextClass(state))}>
            <StatusGlyph state={state} size={16} />
            <span className="sr-only">{fail ? t("overview.attention.srFailure") : t("overview.attention.srWarning")}</span>
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <Subject t={t} item={item} node={node} />
            <ul className="flex min-w-0 flex-col gap-0.5">
              {item.reasons.map((reason, index) => {
                const validUntil = text(reason.params?.["valid_until"]);
                const detail = reason.detail ?? (validUntil !== null ? validUntilText(t, validUntil) : null);
                return (
                  <li key={`${reason.code}-${String(index)}`} className="flex min-w-0 flex-col">
                    <span className="text-13 text-fg">{reasonText(t, reason)}</span>
                    {detail !== null ? (
                      // The system's words wrap rather than being cut, on a phone too.
                      <Mono tone="muted" className="line-clamp-2 text-12 break-words whitespace-pre-wrap">
                        {detail}
                      </Mono>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {(showServer && server !== null) || since !== null ? (
              <p className="mt-0.5 flex min-w-0 flex-wrap items-baseline gap-x-3 text-12 text-fg-muted">
                {showServer && server !== null ? <Mono tone="muted">{server.name}</Mono> : null}
                {since !== null ? <RelativeTime value={since} /> : null}
              </p>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center pl-8 sm:pl-0">
          <AttentionActionButton item={item} node={node} />
        </div>
      </div>
    </Card>
  );
}

export interface AttentionBoardProps {
  /** What needs attention, worst first; undefined while it is read. */
  entries: readonly HomeAttention[] | undefined;
  /** How many there are in all: a server may have cut its own list. */
  total: number;
  /** Name the server on each card: only where there is more than one to tell apart. */
  showServer: boolean;
}

/**
 * What needs an operator, worst first, as cards: the same list the inbox and the sidebar's counters
 * are made of, for one server or for all of them. Six at most until asked for all; when nothing needs
 * anyone, one calm line that says what was checked. Only what is wrong is tinted.
 */
export function AttentionBoard({ entries, total, showServer }: AttentionBoardProps) {
  const t = useT();
  const [all, setAll] = useState(false);
  const count = entries === undefined ? null : Math.max(total, entries.length);
  useAnnounceChange(
    count === null ? null : String(count),
    count === null ? null : count === 0 ? t("overview.attention.announceEmpty") : t("overview.attention.announceCount", { count }),
    entries?.some((entry) => entry.item.severity === "fail") === true ? "assertive" : "polite",
  );
  const shown = entries === undefined ? [] : all ? entries : entries.slice(0, ATTENTION_SHOWN);
  const more = entries !== undefined && entries.length > ATTENTION_SHOWN;
  return (
    <Section
      title={t("overview.attention.title")}
      icon={OBJECT_ICONS.alert}
      loading={entries === undefined}
      {...(count !== null && count > 0 ? { badge: <Badge><span className="sr-only">{t("overview.attention.itemsSr")}</span>{count}</Badge> } : {})}
      {...(more
        ? {
            actions: (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setAll((value) => !value);
                }}
              >
                {all ? t("overview.attention.showFewer") : t("overview.attention.showAll", { count: entries.length })}
              </Button>
            ),
          }
        : {})}
    >
      {entries === undefined ? (
        <LoadingRegion label={t("overview.attention.checking")} className="grid gap-3 lg:grid-cols-2">
          {[0, 1].map((card) => (
            <Skeleton key={card} className="h-16 rounded-card" />
          ))}
        </LoadingRegion>
      ) : entries.length === 0 ? (
        <p className="flex items-start gap-3 text-13 text-pretty text-fg-muted">
          <span className="flex h-5 shrink-0 items-center text-ok">
            <StatusGlyph state="running" size={14} />
          </span>
          <span>
            <span className="font-medium text-fg">{t("overview.attention.emptyTitle")}</span> {t("overview.attention.emptyDescription")}
          </span>
        </p>
      ) : (
        <ul className="grid min-w-0 gap-3 lg:grid-cols-2">
          {shown.map((entry) => (
            <Entry key={entry.key} t={t} entry={entry} showServer={showServer} />
          ))}
        </ul>
      )}
    </Section>
  );
}
