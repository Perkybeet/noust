/**
 * The alert rules, each with where it stands: firing (and for which applications, at what
 * reading, since when), fine, or switched off.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellPlus } from "lucide-react";
import { useState } from "react";

import { alertKeys, alertRulesQuery, removeAlertRule, switchAlertRule } from "../../../api/queries/alerts";
import type { AlertMetric, AlertRule, AlertRuleList } from "../../../api/queries/alerts";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { RuleDrawer } from "./RuleDrawer";
import { appliesTo, formatReading, ruleSentence } from "./rules";

function stateOf(rule: AlertRule): { status: Status; key: "firing" | "ok" | "off" } {
  if (!rule.enabled) return { status: "stopped", key: "off" };
  const firing = (rule.states ?? []).some((state) => state.state === "firing");
  if (!firing) return { status: "running", key: "ok" };
  return { status: rule.severity === "failed" ? "failed" : "warning", key: "firing" };
}

function RuleRow({ rule, metrics }: { rule: AlertRule; metrics: readonly AlertMetric[] }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const metric = metrics.find((candidate) => candidate.id === rule.metric);
  const unit = metric?.unit ?? rule.unit;
  const state = stateOf(rule);
  const firing = rule.enabled ? (rule.states ?? []).filter((entry) => entry.state === "firing") : [];

  const toggle = useMutation({
    mutationFn: (next: boolean) => switchAlertRule(rule.name, next),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: alertKeys.rules }),
    onError: (error) => {
      reportActionError(t("alerts.rules.switchFailed"), error);
    },
  });
  // While the write is in flight the switch shows where it is going.
  const checked = toggle.isPending ? toggle.variables : rule.enabled;

  return (
    <li className="flex min-w-0 flex-col gap-2 px-4 py-3 sm:px-5">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 basis-72 flex-col gap-0.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Mono tone="default" className="text-13 font-medium">
              {rule.name}
            </Mono>
            <StatusPill state={state.status} label={t(`alerts.rules.state.${state.key}`)} size="sm" />
            <span className="text-12 text-fg-muted">{t(`alerts.rules.severity.${rule.severity === "failed" ? "failed" : "warning"}`)}</span>
          </div>
          <p className="min-w-0 text-13 text-pretty text-fg">{ruleSentence(t, rule, unit)}</p>
          <p className="min-w-0 text-12 text-pretty text-fg-muted">{appliesTo(t, rule, metric?.scope ?? rule.scope)}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Switch
            aria-label={t("alerts.rules.switchLabel", { name: rule.name })}
            checked={checked}
            disabled={toggle.isPending}
            onCheckedChange={(next) => {
              toggle.mutate(next);
            }}
          />
          <Button
            size="sm"
            aria-label={t("alerts.rules.editLabel", { name: rule.name })}
            onClick={() => {
              setEditing(true);
            }}
          >
            {t("alerts.rules.edit")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={t("alerts.rules.removeLabel", { name: rule.name })}
            onClick={() => {
              setRemoving(true);
            }}
          >
            {t("alerts.rules.remove")}
          </Button>
        </div>
      </div>
      {firing.length > 0 ? (
        <ul className="flex min-w-0 flex-col gap-0.5 text-12 text-fg-muted">
          {firing.map((entry) => (
            <li key={entry.subject}>
              {t.rich("alerts.rules.firingLine", {
                subject: <Mono key="subject">{entry.subject === "" ? t("alerts.rules.server") : entry.subject}</Mono>,
                value: entry.last_value === null || entry.last_value === undefined ? "-" : formatReading(unit, entry.last_value, t.locale),
                since: <RelativeTime key="since" value={entry.since} />,
              })}
            </li>
          ))}
        </ul>
      ) : null}
      <RuleDrawer rule={rule} metrics={metrics} open={editing} onOpenChange={setEditing} />
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("alerts.rules.removeTitle", { name: rule.name })}
        description={t("alerts.rules.removeDescription")}
        actionLabel={t("alerts.rules.remove")}
        destructive
        onConfirm={async () => {
          await removeAlertRule(rule.name);
          await queryClient.invalidateQueries({ queryKey: alertKeys.rules });
        }}
      />
    </li>
  );
}

function RulesSkeleton() {
  return (
    <Card padding="none">
      <ul aria-hidden="true" className="flex flex-col divide-y divide-border">
        {[0, 1].map((row) => (
          <li key={row} className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
            <div className="flex min-w-0 flex-col gap-1.5">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3.5 w-72 max-w-full" />
            </div>
            <Skeleton className="h-control-sm w-24" />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Rules({ data, onCreate }: { data: AlertRuleList; onCreate: () => void }) {
  const t = useT();
  return (
    <>
      <p role="status" className="text-13 text-fg-muted">
        {data.firing === 0 ? t("alerts.rules.allClear") : t("alerts.rules.firingCount", { count: data.firing })}
      </p>
      {data.items.length === 0 ? (
        <EmptyState
          variant="firstUse"
          level={3}
          icon={<BellPlus />}
          title={t("alerts.rules.emptyTitle")}
          description={t("alerts.rules.emptyDescription")}
          action={
            <Button variant="primary" onClick={onCreate}>
              {t("alerts.rules.new")}
            </Button>
          }
        />
      ) : (
        <Card padding="none">
          <ul aria-label={t("alerts.rules.listLabel")} className="flex min-w-0 flex-col divide-y divide-border">
            {data.items.map((rule) => (
              <RuleRow key={rule.name} rule={rule} metrics={data.metrics} />
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

/** The rules section: its title, the way to add one, and the list. */
export function RuleList() {
  const t = useT();
  const query = useQuery(alertRulesQuery());
  const [creating, setCreating] = useState(false);
  return (
    <Section
      title={t("alerts.rules.title")}
      description={t("alerts.rules.description")}
      actions={
        <Button
          variant="primary"
          disabled={query.data === undefined}
          onClick={() => {
            setCreating(true);
          }}
        >
          {t("alerts.rules.new")}
        </Button>
      }
    >
      <QueryState query={query} label={t("alerts.loadingLabel")} skeleton={<RulesSkeleton />}>
        {(data) => (
          <>
            <Rules
              data={data}
              onCreate={() => {
                setCreating(true);
              }}
            />
            <RuleDrawer metrics={data.metrics} open={creating} onOpenChange={setCreating} />
          </>
        )}
      </QueryState>
    </Section>
  );
}
