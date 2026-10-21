/**
 * What became of each notification: sent, waiting for another try, held for quiet hours, or given
 * up on. A delivery that fails is kept with the destination's own words, so "the Slack message
 * never arrived" has an answer on this page and not in a log somewhere.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, RotateCw } from "lucide-react";

import { notificationDeliveriesQuery, notificationKeys, retryNotificationDelivery } from "../../api/queries/notifications";
import type { NotificationDelivery } from "../../api/queries/notifications";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { EVENT_KINDS, eventSpec } from "./notifications";

const STATES: Readonly<Record<string, { status: Status; key: "sent" | "retrying" | "held" | "failed" }>> = {
  sent: { status: "running", key: "sent" },
  retrying: { status: "deploying", key: "retrying" },
  held: { status: "queued", key: "held" },
  failed: { status: "failed", key: "failed" },
};

/** The event a delivery carried, in words; a digest of held messages is not one of the events. */
function kindLabel(t: T, kind: string): string {
  if (kind === "digest") return t("notifyRouting.deliveries.digest");
  return EVENT_KINDS.includes(kind) ? eventSpec(t, kind).label : kind;
}

function Delivery({ delivery }: { delivery: NotificationDelivery }) {
  const t = useT();
  const queryClient = useQueryClient();
  const state = STATES[delivery.state] ?? STATES["failed"];
  const retry = useMutation({
    mutationFn: () => retryNotificationDelivery(delivery.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationKeys.deliveries }),
    onError: (error) => {
      reportActionError(t("notifyRouting.deliveries.retryFailed"), error);
    },
  });
  const heldCause = delivery.held_for === "flood" || delivery.held_for === "summary" ? delivery.held_for : delivery.state === "held" ? "quiet" : undefined;
  const failing = delivery.last_error !== null && delivery.last_error !== undefined && delivery.last_error !== "";
  return (
    <li className="flex min-w-0 flex-col gap-2 px-4 py-2.5 sm:px-5">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 basis-64 flex-col gap-0.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {state !== undefined ? <StatusPill state={state.status} label={t(`notifyRouting.deliveries.state.${state.key}`)} size="sm" /> : null}
            {delivery.state === "held" && heldCause !== undefined ? <span className="text-12 text-fg-muted">{t(`notifyRouting.deliveries.heldFor.${heldCause}`)}</span> : null}
            <span className="min-w-0 text-13 font-medium text-fg">{kindLabel(t, delivery.kind)}</span>
            <span className="text-12 text-fg-muted">{t("notifyRouting.deliveries.to", { channel: delivery.channel })}</span>
          </div>
          <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-12 text-fg-muted">
            <span className="min-w-0 truncate" translate="no">
              {delivery.subject}
            </span>
            <RelativeTime value={delivery.created_at} />
            {delivery.attempts > 1 ? <span>{t("notifyRouting.deliveries.attempts", { count: delivery.attempts })}</span> : null}
            {delivery.next_attempt_at !== null && delivery.next_attempt_at !== undefined && (delivery.state === "retrying" || delivery.state === "held") ? (
              <span>
                {t.rich(delivery.state === "held" ? "notifyRouting.deliveries.heldUntil" : "notifyRouting.deliveries.nextTry", {
                  when: <RelativeTime key="when" value={delivery.next_attempt_at} />,
                })}
              </span>
            ) : null}
          </p>
        </div>
        {delivery.can_retry ? (
          <Button
            size="sm"
            icon={<RotateCw aria-hidden="true" />}
            loading={retry.isPending}
            aria-label={t("notifyRouting.deliveries.retryLabel", { subject: delivery.subject, channel: delivery.channel })}
            onClick={() => {
              retry.mutate();
            }}
          >
            {t("notifyRouting.deliveries.retry")}
          </Button>
        ) : null}
      </div>
      {failing && delivery.state !== "sent" ? (
        <SystemOutput
          label={t("notifyRouting.deliveries.lastError", { channel: delivery.channel })}
          maxHeight="max-h-32"
          className="rounded-control border border-border bg-bg-sunken px-3 py-2"
        >
          {delivery.last_error ?? ""}
        </SystemOutput>
      ) : null}
    </li>
  );
}

/** One line on how the record stands: what waits, what is held, what was given up on. */
function Standing({ counts, total }: { counts: Readonly<Record<string, number>>; total: number }) {
  const t = useT();
  const parts = (["failed", "retrying", "held"] as const).flatMap((state) => {
    const count = counts[state] ?? 0;
    return count > 0 ? [t(`notifyRouting.deliveries.counts.${state}`, { count })] : [];
  });
  if (parts.length === 0) {
    return <p className="text-13 text-fg-muted">{total === 0 ? t("notifyRouting.deliveries.empty") : t("notifyRouting.deliveries.allWell")}</p>;
  }
  return <p className="text-13 font-medium text-fg">{parts.join(" - ")}</p>;
}

export function DeliveryLog() {
  const t = useT();
  const deliveries = useQuery(notificationDeliveriesQuery());

  // A server whose Noust predates the record has nothing to show here.
  // design-exception: hand-error-ternary an older server has no such record: that is not a failure to load
  if (deliveries.isError && deliveries.data === undefined) return null;
  const items = deliveries.data?.items ?? [];

  return (
    <Section icon={History} title={t("notifyRouting.deliveries.title")} description={t("notifyRouting.deliveries.description")}>
      {deliveries.data === undefined ? (
        <LoadingRegion label={t("notifyRouting.deliveries.loading")}>
          <Card padding="none">
            <div aria-hidden="true" className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
              <Skeleton className="h-4 w-72 max-w-full" />
              <Skeleton className="h-control-sm w-20" />
            </div>
          </Card>
        </LoadingRegion>
      ) : (
        <>
          <Standing counts={deliveries.data.counts} total={items.length} />
          {items.length > 0 ? (
            <Card padding="none">
              <ul aria-label={t("notifyRouting.deliveries.listLabel")} className="flex min-w-0 flex-col divide-y divide-border">
                {items.map((delivery) => (
                  <Delivery key={delivery.id} delivery={delivery} />
                ))}
              </ul>
            </Card>
          ) : null}
        </>
      )}
    </Section>
  );
}
