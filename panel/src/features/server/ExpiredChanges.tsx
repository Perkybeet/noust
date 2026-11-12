/**
 * A change to how the server is reached (sshd, the firewall) that its timer undid because nobody confirmed it in time.
 *
 * The banner of a pending change is gone the moment the timer fires, and nothing else said why: the operator who looked
 * away found the rule they had added missing, and Activity called the undoing "Firewall changed". This says it, on the
 * Server page where the change was waiting, for a day and until it is put away (item 138).
 */

import { useState } from "react";

import { useNow } from "../../components/page/clock";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import type { PendingChange } from "./queries";

/** Where the changes the person has put away are remembered, in this browser. */
export const DISMISSED_KEY = "noust.server.expired.dismissed";

/** How long an undone change is still told: the operator who was away for the night should find it in the morning. */
const TOLD_FOR_S = 24 * 3_600;

/** The most put-away ids kept: the list is for the changes of the last day, so a few hundred is more than there will be. */
const KEPT = 200;

function readDismissed(): string[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(DISMISSED_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    // A browser that refuses storage, or something else wrote the key: nothing is put away.
    return [];
  }
}

/**
 * The changes to tell of: the ones the timer undid (a change the operator undid by hand is theirs and needs no telling) in
 * the last day, newest first, that nobody put away.
 */
export function recentlyExpired(changes: readonly PendingChange[], now: number, dismissed: readonly string[]): PendingChange[] {
  return changes
    .filter((change) => change.status === "expired" && change.resolved_by !== null && change.resolved_by !== undefined && ["timer", "overdue"].includes(change.resolved_by))
    .filter((change) => change.resolved_at !== null && change.resolved_at !== undefined && now / 1000 - change.resolved_at <= TOLD_FOR_S)
    .filter((change) => !dismissed.includes(change.id))
    .sort((a, b) => (b.resolved_at ?? 0) - (a.resolved_at ?? 0));
}

function ExpiredChange({ change, onDismiss }: { change: PendingChange; onDismiss: () => void }) {
  const t = useT();
  const firewall = change.kind.startsWith("firewall");
  return (
    <Notice
      variant="banner"
      tone="warning"
      title={t(firewall ? "server.expired.titleFirewall" : "server.expired.titleSsh")}
      onDismiss={onDismiss}
    >
      <span className="flex min-w-0 flex-col gap-1">
        {/* The change's own name, as Noust recorded it. */}
        <span>
          <Mono tone="muted">{change.title}</Mono>
        </span>
        <span>
          {change.resolved_at !== null && change.resolved_at !== undefined
            ? t.rich("server.expired.when", { time: <RelativeTime key="time" value={new Date(change.resolved_at * 1000).toISOString()} /> })
            : null}{" "}
          {t("server.expired.hint")}
        </span>
      </span>
    </Notice>
  );
}

/** The changes to tell of now, and how to put one away: for the page that draws them, which also decides whether it has a banner at all. */
export function useExpiredChanges(changes: readonly PendingChange[]): { shown: PendingChange[]; putAway: (id: string) => void } {
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);
  // A change falls out of its day without anybody doing anything: the minute's tick is what notices.
  const now = useNow(() => 60_000);
  const putAway = (id: string): void => {
    const next = [id, ...dismissed.filter((kept) => kept !== id)].slice(0, KEPT);
    setDismissed(next);
    try {
      window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(next));
    } catch {
      // Not remembered across a reload; it is put away for this visit all the same.
    }
  };
  return { shown: recentlyExpired(changes, now, dismissed), putAway };
}

/** One notice for each change the timer undid lately and nobody has put away. */
export function ExpiredChanges({ shown, putAway }: ReturnType<typeof useExpiredChanges>) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {shown.map((change) => (
        <ExpiredChange
          key={change.id}
          change={change}
          onDismiss={() => {
            putAway(change.id);
          }}
        />
      ))}
    </div>
  );
}
