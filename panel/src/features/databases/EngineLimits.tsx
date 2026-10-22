import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../api/client";
import { databaseKeys, engineLimitsQuery } from "../../api/queries/databases";
import type { EngineLimits as EngineLimitsData, EngineLimitsOutcome } from "../../api/queries/databases";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { useNode } from "../../nodes/useNode";

type Key = "memory_mb" | "cpus" | "swap_mb" | "tasks";

const KEYS: readonly Key[] = ["memory_mb", "cpus", "swap_mb", "tasks"];

type Draft = Readonly<Record<Key, string>>;

const MB = 1024 * 1024;

/** What the form holds for a limit: what Noust's drop-in sets, empty when it sets none. */
function stored(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

/** The form as Noust's drop-in has it now. */
export function limitsDraft(data: EngineLimitsData): Draft {
  return {
    memory_mb: stored(data.configured.memory_mb),
    cpus: stored(data.configured.cpus),
    swap_mb: stored(data.configured.swap_mb),
    tasks: stored(data.configured.tasks),
  };
}

/** The text of a value in the form, or null when it is not a number the limit accepts. */
function parse(key: Key, typed: string): number | null {
  const number = Number(typed.trim());
  if (typed.trim() === "" || !Number.isFinite(number) || number < 0) return null;
  if (key === "cpus") return number > 0 ? number : null;
  return Number.isInteger(number) ? number : null;
}

/**
 * What saving sends: the limits that differ from Noust's drop-in as numbers, an emptied one as a
 * removal. A value that is not a number the limit accepts is returned as an error for its field.
 */
export function limitsChange(data: EngineLimitsData, draft: Draft): { body: Partial<Record<Key, number>> & { clear: Key[] }; errors: Partial<Record<Key, "whole" | "number">> } {
  const baseline = limitsDraft(data);
  const body: Partial<Record<Key, number>> & { clear: Key[] } = { clear: [] };
  const errors: Partial<Record<Key, "whole" | "number">> = {};
  for (const key of KEYS) {
    const typed = draft[key].trim();
    if (typed === baseline[key].trim()) continue;
    if (typed === "") {
      body.clear.push(key);
      continue;
    }
    const value = parse(key, typed);
    if (value === null || (key === "memory_mb" && value === 0) || (key === "tasks" && value === 0)) {
      errors[key] = key === "cpus" ? "number" : "whole";
      continue;
    }
    body[key] = value;
  }
  return { body, errors };
}

function sizeOf(megabytes: number, t: T): string {
  return formatBytes(megabytes * MB, t.locale);
}

/** What systemd holds for one limit and whether the kernel does, under its field. */
function Proof({ data, limit, t }: { data: EngineLimitsData; limit: Key; t: T }) {
  const held = data.effective[limit];
  const enforced = data.enforced[limit];
  const value = held === null || held === undefined ? null : limit === "memory_mb" || limit === "swap_mb" ? sizeOf(held, t) : String(held);
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span>{value === null ? t("databases.engineSettings.limits.holdsNone") : t("databases.engineSettings.limits.holds", { value })}</span>
      {value !== null && enforced === true ? <StatusPill appearance="inline" size="sm" state="running" label={t("databases.engineSettings.limits.enforced")} /> : null}
      {value !== null && enforced === false ? <StatusPill appearance="inline" size="sm" state="warning" label={t("databases.engineSettings.limits.notEnforced")} /> : null}
    </span>
  );
}

function LimitsForm({ engine, display, data }: { engine: string; display: string; data: EngineLimitsData }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const elsewhere = data.elsewhere ?? [];
  const current = limitsDraft(data);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [outcome, setOutcome] = useState<EngineLimitsOutcome | null>(null);
  const [warnings, setWarnings] = useState<{ messages: string[]; body: Record<string, unknown> } | null>(null);

  // Noust's drop-in changed underneath an untouched form (saved here, or from a terminal): follow it.
  const same = (a: Draft, b: Draft): boolean => KEYS.every((key) => a[key] === b[key]);
  if (!same(current, baseline)) {
    setBaseline(current);
    if (same(draft, baseline)) setDraft(current);
  }

  const save = useMutation({
    mutationFn: ({ body, confirm }: { body: Record<string, unknown>; confirm: boolean }) =>
      request("put", "/api/databases/engines/{engine}/limits", { params: { engine }, body: { ...body, confirm } }),
    onSuccess: (result) => {
      setOutcome(result);
      setWarnings(null);
      void queryClient.invalidateQueries({ queryKey: databaseKeys.engineLimits(engine) });
    },
    onError: (error, { body, confirm }) => {
      // A memory limit below the engine's use comes back with its warning, read before the one confirmation.
      if (confirm || !isApiError(error) || error.error !== "confirmation_required") return;
      const listed = error.extra["warnings"];
      const messages = Array.isArray(listed) ? listed.filter((item): item is string => typeof item === "string") : [];
      setWarnings({ messages: messages.length > 0 ? messages : [error.detail], body });
    },
  });

  const { body, errors } = limitsChange(data, draft);
  const changes = KEYS.filter((key) => key in errors || body.clear.includes(key) || body[key] !== undefined).length;
  const failure = save.isError && warnings === null && !(isApiError(save.error) && save.error.error === "confirmation_required") ? save.error : null;

  const edit = (key: Key, value: string): void => {
    setDraft((previous) => ({ ...previous, [key]: value }));
    setOutcome(null);
    if (save.isError) save.reset();
  };
  const message = (key: Key): string | undefined => {
    const error = errors[key];
    return error === undefined ? undefined : t(error === "number" ? "databases.engineSettings.limits.invalidNumber" : "databases.engineSettings.limits.invalidWhole");
  };
  const control = (key: Key, suffix?: string) => (
    <Input
      mono
      inputMode={key === "cpus" ? "decimal" : "numeric"}
      value={draft[key]}
      onValueChange={(next: string) => edit(key, next)}
      placeholder={t("databases.engineSettings.limits.noLimit")}
      autoComplete="off"
      spellCheck={false}
      {...(suffix !== undefined ? { suffix } : {})}
    />
  );
  const help = (key: Key, text: string) => (
    <>
      <span className="block max-w-measure-help">{text}</span>
      <Proof data={data} limit={key} t={t} />
    </>
  );

  return (
    <>
      {outcome !== null ? (
        <div className="flex flex-col gap-1.5">
          <InlineMessage tone="success" live>
            {outcome.changed.length > 0 ? t("databases.engineSettings.limits.applied", { engine: display }) : t("databases.engineSettings.limits.unchanged", { engine: display })}
          </InlineMessage>
          {(outcome.warnings ?? []).map((warning) => (
            <InlineMessage key={warning}>{warning}</InlineMessage>
          ))}
        </div>
      ) : null}
      {elsewhere.length > 0 ? <InlineMessage tone="info">{t("databases.engineSettings.limits.elsewhere", { names: elsewhere.join(", "), unit: data.unit })}</InlineMessage> : null}
      <Card>
        <div className="flex flex-col gap-5">
          {data.memory_in_use_mb !== null && data.memory_in_use_mb !== undefined ? (
            <p className="text-13 text-fg-muted">{t("databases.engineSettings.limits.inUse", { engine: display, memory: sizeOf(data.memory_in_use_mb, t) })}</p>
          ) : null}
          <div className="grid gap-5 lg:grid-cols-2">
            <Field label={t("databases.engineSettings.limits.memory")} error={message("memory_mb")} description={help("memory_mb", t("databases.engineSettings.limits.memoryHelp", { minimum: data.minimum_memory_mb }))}>
              {control("memory_mb", t("databases.engineSettings.limits.mbSuffix"))}
            </Field>
            <Field label={t("databases.engineSettings.limits.cpus")} error={message("cpus")} description={help("cpus", t("databases.engineSettings.limits.cpusHelp", { count: data.cpus }))}>
              {control("cpus")}
            </Field>
            <Field label={t("databases.engineSettings.limits.swap")} error={message("swap_mb")} description={help("swap_mb", t("databases.engineSettings.limits.swapHelp"))}>
              {control("swap_mb", t("databases.engineSettings.limits.mbSuffix"))}
            </Field>
            <Field label={t("databases.engineSettings.limits.tasks")} error={message("tasks")} description={help("tasks", t("databases.engineSettings.limits.tasksHelp"))}>
              {control("tasks")}
            </Field>
          </div>
          {failure !== null ? <ErrorBlock live compact error={failure} title={t("databases.engineSettings.limits.saveFailed", { engine: display })} /> : null}
          <div>
            {/* design-exception: card-save The limits are a systemd drop-in applied by an action of their own, on the page whose one bar saves the settings above: a second bar would fight it for the bottom edge. */}
            <Button
              loading={save.isPending && warnings === null}
              disabled={changes === 0 || Object.keys(errors).length > 0}
              onClick={() => save.mutate({ body, confirm: false })}
            >
              {t("databases.engineSettings.limits.apply")}
            </Button>
          </div>
        </div>
      </Card>
      <CommandHint command={`noust db limits ${engine}`} label={t("databases.common.fromTerminal")} />
      {warnings !== null ? (
        <ConfirmDialog
          open
          friction="simple"
          onOpenChange={(open) => (open ? undefined : setWarnings(null))}
          title={t("databases.engineSettings.limits.confirmTitle", { engine: display })}
          description={
            <span className="flex flex-col gap-2">
              {warnings.messages.map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
            </span>
          }
          actionLabel={t("databases.engineSettings.limits.confirmAction")}
          server={node}
          onConfirm={async () => {
            await save.mutateAsync({ body: warnings.body, confirm: true });
          }}
        />
      ) : null}
    </>
  );
}

/**
 * What an engine on this host may use (memory, processors, swap, tasks), under its settings: one
 * field each, with what systemd holds and whether the kernel enforces it. Saving applies a
 * systemd drop-in without restarting the engine, proves it, and puts the previous limits back
 * if the engine stops answering; a memory limit below what the engine uses asks first.
 */
export function EngineLimits({ engine, display }: { engine: string; display: string }) {
  const t = useT();
  const limits = useQuery(engineLimitsQuery(engine));
  // A server that does not have the endpoint yet (an older node behind a central) answers 404 or 405.
  if (limits.isError && isApiError(limits.error) && (limits.error.status === 404 || limits.error.status === 405)) return null;
  return (
    <Section icon={OBJECT_ICONS.limits} title={t("databases.engineSettings.limits.title")} description={t("databases.engineSettings.limits.description", { engine: display })}>
      <QueryState query={limits} label={t("databases.engineSettings.limits.noun", { engine: display })} skeleton={<Skeleton className="h-48 w-full rounded-card" />}>
        {(data) => <LimitsForm engine={engine} display={display} data={data} />}
      </QueryState>
    </Section>
  );
}
