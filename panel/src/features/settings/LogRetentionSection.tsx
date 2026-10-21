/**
 * How long the journal keeps the logs, and how much disk it may take. Applications' lines are in
 * the journal, so this is also how far back a log search can look. Noust writes one journald
 * drop-in and restarts journald, so a change asks for sudo mode; a file of that name that Noust
 * did not write is never overwritten.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, RotateCcw } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { clearLogRetention, logKeys, logRetentionQuery, saveLogRetention } from "../../api/queries/logSearch";
import type { LogRetention } from "../../api/queries/logSearch";
import { QueryState } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Drawer } from "../../components/ui/Drawer";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { SkeletonCard } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { splitErrors } from "./formErrors";
import { FormFailure } from "./SettingsForm";

/** The server's own ranges (noust.managers.log_retention). */
export const MIN_DAYS = 1;
export const MAX_DAYS = 3650;
export const MIN_MB = 16;
export const MAX_MB = 1_048_576;

const FIELDS = ["days", "max_use_mb"] as const;

interface RetentionForm {
  days: string;
  size: string;
  persistent: boolean;
}

function formFrom(current: LogRetention): RetentionForm {
  return {
    days: current.days === null || current.days === undefined ? "" : String(current.days),
    size: current.max_use_mb === null || current.max_use_mb === undefined ? "" : String(current.max_use_mb),
    persistent: current.persistent,
  };
}

/** The PUT body: only what is filled in, so what is left empty stays as it is. */
export function retentionBody(form: RetentionForm): { days?: number; max_use_mb?: number; persistent: boolean } {
  const days = Number.parseInt(form.days.trim(), 10);
  const size = Number.parseInt(form.size.trim(), 10);
  return {
    ...(Number.isFinite(days) ? { days } : {}),
    ...(Number.isFinite(size) ? { max_use_mb: size } : {}),
    persistent: form.persistent,
  };
}

function RetentionDrawer({ current, open, onOpenChange }: { current: LogRetention; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [form, setForm] = useState<RetentionForm>(() => formFrom(current));
  const [edited, setEdited] = useState<ReadonlySet<string>>(new Set());
  // Pressing Save with nothing written says so beside the first box, rather than leaving the button dead.
  const [nothing, setNothing] = useState(false);
  const daysRef = useRef<HTMLInputElement>(null);
  const save = useMutation({
    mutationFn: () => saveLogRetention(retentionBody(form)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: logKeys.retention });
      onOpenChange(false);
    },
    onMutate: () => {
      setEdited(new Set());
    },
  });
  const empty = form.days.trim() === "" && form.size.trim() === "";
  const split = splitErrors(save.error, FIELDS);
  const message = (name: (typeof FIELDS)[number]): string | undefined => {
    if (name === "days" && nothing && empty) return t("logs.retention.nothingToSave");
    return edited.has(name) ? undefined : split.fields[name];
  };
  const set = (patch: Partial<RetentionForm>, touched: readonly string[]): void => {
    setNothing(false);
    setForm((previous) => ({ ...previous, ...patch }));
    setEdited((previous) => new Set([...previous, ...touched]));
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    if (empty) {
      setNothing(true);
      daysRef.current?.focus();
      return;
    }
    save.mutate();
  };
  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setForm(formFrom(current));
          setEdited(new Set());
          setNothing(false);
          save.reset();
        }
        onOpenChange(next);
      }}
      title={t("logs.retention.drawerTitle")}
      description={t("logs.retention.drawerDescription")}
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
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("logs.retention.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-5">
        <FormFailure error={split.form} title={t("logs.retention.saveFailed")} />
        <Field label={t("logs.retention.daysLabel")} description={t("logs.retention.daysDescription", { min: MIN_DAYS, max: MAX_DAYS })} error={message("days")}>
          <Input
            ref={daysRef}
            mono
            inputMode="numeric"
            value={form.days}
            onValueChange={(days: string) => {
              set({ days }, ["days"]);
            }}
          />
        </Field>
        <Field label={t("logs.retention.sizeLabel")} description={t("logs.retention.sizeDescription", { min: MIN_MB, max: MAX_MB })} error={message("max_use_mb")}>
          <Input
            mono
            inputMode="numeric"
            value={form.size}
            onValueChange={(size: string) => {
              set({ size }, ["max_use_mb"]);
            }}
          />
        </Field>
        <Checkbox
          label={t("logs.retention.persistentLabel")}
          description={t("logs.retention.persistentDescription")}
          checked={form.persistent}
          onCheckedChange={(persistent) => {
            set({ persistent }, []);
          }}
        />
      </form>
    </Drawer>
  );
}

function Retention({ current }: { current: LogRetention }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [resetting, setResetting] = useState(false);
  const set = current.configured;
  return (
    <Card>
      <div className="flex min-w-0 flex-col gap-4">
        {current.foreign ? (
          <Notice tone="warning" title={t("logs.retention.foreignTitle")}>
            {t("logs.retention.foreignBody")}
          </Notice>
        ) : null}
        <p className="text-13 text-pretty text-fg">
          {set
            ? t("logs.retention.state", {
                days: current.days === null || current.days === undefined ? t("logs.retention.noLimit") : t("logs.retention.days", { count: current.days }),
                size: current.max_use_mb === null || current.max_use_mb === undefined ? t("logs.retention.noLimit") : formatBytes(current.max_use_mb * 1024 * 1024, t.locale),
              })
            : t("logs.retention.defaults")}
        </p>
        {current.disk_usage !== "" ? (
          <p className="text-12 text-fg-muted">
            {t.rich("logs.retention.usage", { usage: <Mono key="usage">{current.disk_usage}</Mono> })}
          </p>
        ) : null}
        {current.notes.length > 0 ? (
          <ul className="flex flex-col gap-1 text-12 text-fg-muted">
            {current.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button icon={<Pencil aria-hidden="true" />}
            variant="secondary"
            disabled={current.foreign}
            onClick={() => {
              setEditing(true);
            }}
          >
            {t("logs.retention.change")}
          </Button>
          {set ? (
            <Button icon={<RotateCcw aria-hidden="true" />}
              variant="ghost"
              onClick={() => {
                setResetting(true);
              }}
            >
              {t("logs.retention.reset")}
            </Button>
          ) : null}
        </div>
      </div>
      {editing ? <RetentionDrawer current={current} open onOpenChange={setEditing} /> : null}
      <ConfirmDialog
        friction="simple"
        open={resetting}
        onOpenChange={setResetting}
        title={t("logs.retention.resetTitle")}
        description={t("logs.retention.resetDescription")}
        actionLabel={t("logs.retention.reset")}
        onConfirm={async () => {
          await clearLogRetention();
          await queryClient.invalidateQueries({ queryKey: logKeys.retention });
        }}
      />
    </Card>
  );
}

/** How long the journal keeps the logs: what is set, what the journal takes, and the way to change it. */
export function LogRetentionSection() {
  const t = useT();
  const query = useQuery(logRetentionQuery());
  return (
    <Section title={t("logs.retention.title")} description={t("logs.retention.description")}>
      <QueryState query={query} label={t("logs.retention.noun")} skeleton={<SkeletonCard lines={2} />}>
        {(current) => <Retention current={current} />}
      </QueryState>
    </Section>
  );
}
