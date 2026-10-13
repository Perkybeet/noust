/**
 * One alert rule in a drawer: what to measure, the limit, how long it must hold, how bad it is
 * and how often to say so. A refusal names its field (the server owns what a rule may be) and is
 * shown beside it, verbatim.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { alertKeys, saveAlertRule } from "../../../api/queries/alerts";
import type { AlertMetric, AlertRule } from "../../../api/queries/alerts";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";
import { FormFailure } from "../SettingsForm";
import { COMPARATORS, COMPARATOR_KEYS, RULE_FIELDS, emptyRule, limitHint, metricLabel, ruleBody, ruleFormFrom } from "./rules";
import type { Comparator, RuleForm, RuleSeverity } from "./rules";

export interface RuleDrawerProps {
  /** The rule being edited; absent for a new one. */
  rule?: AlertRule;
  metrics: readonly AlertMetric[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RuleDrawer({ rule, metrics, open, onOpenChange }: RuleDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const first = metrics[0]?.id ?? "cpu";
  const initial = (): RuleForm => (rule === undefined ? emptyRule(first) : ruleFormFrom(rule, metrics));
  const [form, setForm] = useState<RuleForm>(initial);
  const [edited, setEdited] = useState<ReadonlySet<string>>(new Set());

  const metric = metrics.find((candidate) => candidate.id === form.metric);
  const onApp = metric?.scope === "app";
  const unit = metric?.unit ?? "number";
  const set = <K extends keyof RuleForm>(key: K, value: RuleForm[K], touched: readonly string[] = [key]): void => {
    setForm((current) => ({ ...current, [key]: value }));
    // A message is about the value that was sent: it goes away when the value is edited.
    setEdited((current) => new Set([...current, ...touched]));
  };

  const save = useMutation({
    mutationFn: () => saveAlertRule(form.name.trim(), ruleBody(form, rule?.enabled ?? true)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: alertKeys.rules });
      onOpenChange(false);
    },
    onMutate: () => {
      setEdited(new Set());
    },
  });
  const split = splitErrors(save.error, RULE_FIELDS);
  const message = (...names: string[]): string | undefined => {
    if (names.some((name) => edited.has(name))) return undefined;
    return names.map((name) => split.fields[name as (typeof RULE_FIELDS)[number]]).find((text) => text !== undefined);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!save.isPending && form.name.trim() !== "") save.mutate();
  };
  const reset = (): void => {
    setForm(initial());
    setEdited(new Set());
    save.reset();
  };

  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
      title={rule === undefined ? t("alerts.form.newTitle") : t("alerts.form.editTitle", { name: rule.name })}
      description={t("alerts.form.description")}
      footer={
        <>
          <Button
            variant="ghost"
            disabled={save.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" disabled={form.name.trim() === "" || form.limit.trim() === ""} loading={save.isPending}>
            {t("alerts.form.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-5">
        <FormFailure error={split.form} title={t("alerts.form.saveErrorTitle")} />
        <Field label={t("alerts.form.nameLabel")} description={t("alerts.form.nameDescription")} error={message("name")}>
          <Input
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="cpu-high"
            // A rule is its name: changing it would be another rule, so an existing one keeps it.
            readOnly={rule !== undefined}
            value={form.name}
            onValueChange={(next: string) => {
              set("name", next);
            }}
          />
        </Field>
        <Field label={t("alerts.form.metricLabel")} nativeLabel={false} error={message("metric")}>
          <Select
            value={form.metric}
            onValueChange={(next) => {
              set("metric", next);
            }}
            options={metrics.map((candidate) => ({
              value: candidate.id,
              label: metricLabel(t, candidate.id),
              hint: candidate.scope === "app" ? t("alerts.form.metricHintApp") : t("alerts.form.metricHintServer"),
            }))}
          />
        </Field>
        {onApp ? (
          <Field label={t("alerts.form.appLabel")} optional description={t("alerts.form.appDescription")} error={message("app_domain")}>
            <Input
              mono
              autoComplete="off"
              spellCheck={false}
              placeholder="shop.example.com"
              value={form.app}
              onValueChange={(next: string) => {
                set("app", next);
              }}
            />
          </Field>
        ) : null}
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          <Field label={t("alerts.form.comparatorLabel")} nativeLabel={false} error={message("comparator")}>
            <Select<Comparator>
              value={form.comparator}
              onValueChange={(next) => {
                set("comparator", next);
              }}
              options={COMPARATORS.map((comparator) => ({ value: comparator, label: t(`alerts.form.comparator.${COMPARATOR_KEYS[comparator]}`) }))}
            />
          </Field>
          <Field label={t("alerts.form.limitLabel")} description={limitHint(t, unit)} error={message("threshold")}>
            <Input
              mono
              autoComplete="off"
              spellCheck={false}
              placeholder={unit === "percent" ? "90" : unit === "bytes" ? "512MB" : unit === "bytes_s" ? "2MB/s" : "100"}
              value={form.limit}
              onValueChange={(next: string) => {
                set("limit", next, ["threshold"]);
              }}
            />
          </Field>
        </div>
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          <Field label={t("alerts.form.durationLabel")} description={t("alerts.form.durationDescription")} error={message("duration", "duration_s")}>
            <Input
              mono
              inputMode="numeric"
              value={form.minutes}
              onValueChange={(next: string) => {
                set("minutes", next, ["duration", "duration_s"]);
              }}
            />
          </Field>
          <Field label={t("alerts.form.severityLabel")} nativeLabel={false} description={t("alerts.form.severityDescription")} error={message("severity")}>
            <Select<RuleSeverity>
              value={form.severity}
              onValueChange={(next) => {
                set("severity", next);
              }}
              options={[
                { value: "warning", label: t("alerts.rules.severity.warning") },
                { value: "failed", label: t("alerts.rules.severity.failed") },
              ]}
            />
          </Field>
        </div>
        <Disclosure label={t("alerts.form.more")} defaultOpen={rule !== undefined && (rule.remind || rule.note !== "" || rule.hysteresis > 0)}>
          <div className="flex min-w-0 flex-col gap-5">
            <Field label={t("alerts.form.cooldownLabel")} description={t("alerts.form.cooldownDescription")} error={message("cooldown", "cooldown_s")}>
              <Input
                mono
                inputMode="numeric"
                value={form.cooldownMinutes}
                onValueChange={(next: string) => {
                  set("cooldownMinutes", next, ["cooldown", "cooldown_s"]);
                }}
              />
            </Field>
            <Checkbox
              label={t("alerts.form.remindLabel")}
              description={t("alerts.form.remindDescription")}
              checked={form.remind}
              onCheckedChange={(next) => {
                set("remind", next);
              }}
            />
            <Field label={t("alerts.form.hysteresisLabel")} optional description={t("alerts.form.hysteresisDescription")} error={message("hysteresis")}>
              <Input
                mono
                inputMode="decimal"
                value={form.hysteresis}
                onValueChange={(next: string) => {
                  set("hysteresis", next);
                }}
              />
            </Field>
            <Field label={t("alerts.form.noteLabel")} optional description={t("alerts.form.noteDescription")} error={message("note")}>
              <Input
                value={form.note}
                maxLength={500}
                onValueChange={(next: string) => {
                  set("note", next);
                }}
              />
            </Field>
          </div>
        </Disclosure>
      </form>
    </Drawer>
  );
}
