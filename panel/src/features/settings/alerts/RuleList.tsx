/**
 * The alert rules, each with where it stands: firing (and for which applications, at what
 * reading, since when, and whether somebody has acknowledged it), fine, or switched off.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, Pencil } from "lucide-react";
import { useState } from "react";

import { acknowledgeAlert, alertKeys, alertRulesQuery, handBackAlert, removeAlertRule, switchAlertRule } from "../../../api/queries/alerts";
import type { AlertMetric, AlertRule, AlertRuleList } from "../../../api/queries/alerts";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { SkeletonRows } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { SettingsPrimaryAction } from "../SettingsPrimaryAction";
import { acknowledgement } from "./data";
import { RuleDrawer } from "./RuleDrawer";
import { appliesTo, formatReading, ruleSentence } from "./rules";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

function stateOf(rule: AlertRule): { status: Status; key: "firing" | "ok" | "off" } {
  if (!rule.enabled) return { status: "stopped", key: "off" };
  const firing = (rule.states ?? []).some((state) => state.state === "firing");
  if (!firing) return { status: "running", key: "ok" };
  return { status: rule.severity === "failed" ? "failed" : "warning", key: "firing" };
}

/**
 * One firing subject of a rule: what it reads and since when, and whether somebody has
 * acknowledged it. Acknowledging stops the rule's reminders and tells the channels who is on it;
 * it never resolves the alert, and reopening it makes the reminders speak again.
 */
function FiringLine({ rule, entry, unit }: { rule: AlertRule; entry: NonNullable<AlertRule["states"]>[number]; unit: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const subject = entry.subject === "" ? t("alerts.rules.server") : entry.subject;
  const acknowledged = acknowledgement(entry);
  const change = useMutation({
    mutationFn: () => (acknowledged !== null ? handBackAlert(rule.name, entry.subject) : acknowledgeAlert(rule.name, entry.subject)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: alertKeys.rules }),
    onError: (error) => {
      reportActionError(t(acknowledged !== null ? "alerts.rules.ack.reopenFailed" : "alerts.rules.ack.failed"), error);
    },
  });
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
      <span className="min-w-0">
        {t.rich("alerts.rules.firingLine", {
          subject: <Mono key="subject">{subject}</Mono>,
          value: entry.last_value === null || entry.last_value === undefined ? "-" : formatReading(unit, entry.last_value, t.locale),
          since: <RelativeTime key="since" value={entry.since} />,
        })}
      </span>
      {acknowledged !== null ? (
        <Badge>
          {t.rich("alerts.rules.ack.acknowledged", {
            who: <Mono key="who">{acknowledged.by}</Mono>,
            when: <RelativeTime key="when" value={acknowledged.at} />,
          })}
        </Badge>
      ) : null}
      <Button
        size="sm"
        variant={acknowledged !== null ? "ghost" : "secondary"}
        icon={acknowledged !== null ? <OBJECT_ICONS.rollback aria-hidden="true" /> : <CheckCheck aria-hidden="true" />}
        loading={change.isPending}
        aria-label={t(acknowledged !== null ? "alerts.rules.ack.reopenLabel" : "alerts.rules.ack.acknowledgeLabel", { subject })}
        onClick={() => {
          change.mutate();
        }}
      >
        {t(acknowledged !== null ? "alerts.rules.ack.reopen" : "alerts.rules.ack.acknowledge")}
      </Button>
    </li>
  );
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
    <RowList.Item
      leading={
        <Switch
          aria-label={t("alerts.rules.switchLabel", { name: rule.name })}
          checked={checked}
          busy={toggle.isPending}
          onCheckedChange={(next) => {
            toggle.mutate(next);
          }}
        />
      }
      actions={
        <RowMenu name={rule.name}>
          <MenuItem
            icon={<Pencil />}
            onClick={() => {
              setEditing(true);
            }}
          >
            {t("common.words.edit")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem
            icon={<ICONS.delete />}
            destructive
            onClick={() => {
              setRemoving(true);
            }}
          >
            {t("common.words.remove")}
          </MenuItem>
        </RowMenu>
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
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
        {firing.length > 0 ? (
          <ul className="flex min-w-0 flex-col gap-1 text-12 text-fg-muted">
            {firing.map((entry) => (
              <FiringLine key={entry.subject} rule={rule} entry={entry} unit={unit} />
            ))}
          </ul>
        ) : null}
      </div>
      <RuleDrawer rule={rule} metrics={metrics} open={editing} onOpenChange={setEditing} />
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("common.words.removeNamedQuestion", { name: rule.name })}
        description={t("alerts.rules.removeDescription")}
        actionLabel={t("common.words.remove")}
        destructive
        onConfirm={async () => {
          await removeAlertRule(rule.name);
          await queryClient.invalidateQueries({ queryKey: alertKeys.rules });
        }}
      />
    </RowList.Item>
  );
}

function Rules({ data, onCreate }: { data: AlertRuleList; onCreate: () => void }) {
  const t = useT();
  return (
    <>
      {data.items.length === 0 ? null : (
        <p role="status" className="text-13 text-fg-muted">
          {data.firing === 0 ? t("alerts.rules.allClear") : t("alerts.rules.firingCount", { count: data.firing })}
        </p>
      )}
      {data.items.length === 0 ? (
        <EmptyState
          variant="inline"
          title={t("alerts.rules.emptyTitle")}
          description={t("alerts.rules.emptyDescription")}
          // With no rule the header has no button: the empty state's is the one way to add the first.
          action={
            <Button size="sm" variant="secondary" icon={<ICONS.add aria-hidden="true" />} onClick={onCreate}>
              {t("alerts.rules.new")}
            </Button>
          }
        />
      ) : (
        <RowList aria-label={t("alerts.rules.listLabel")}>
          {data.items.map((rule) => (
            <RuleRow key={rule.name} rule={rule} metrics={data.metrics} />
          ))}
        </RowList>
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
    <Section title={t("alerts.rules.title")} description={t("alerts.rules.description")}>
      {/* The page's one primary, in its header, where the save bar's Save takes it over while there are changes (FM-2). */}
      {query.data === undefined || query.data.items.length === 0 ? null : (
        <SettingsPrimaryAction>
          <Button
            variant="primary"
            icon={<ICONS.add aria-hidden="true" />}
            onClick={() => {
              setCreating(true);
            }}
          >
            {t("alerts.rules.new")}
          </Button>
        </SettingsPrimaryAction>
      )}
      <QueryState query={query} label={t("alerts.loadingLabel")} skeleton={<SkeletonRows rows={2} />}>
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
