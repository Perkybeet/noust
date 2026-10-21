import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Scaling, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { Job } from "../../../api/queries/jobs";
import {
  processesQuery,
  replicasQuery,
  runtimeKeys,
} from "../../../api/queries/runtime";
import type { AppProcess, Replicas } from "../../../api/queries/runtime";
import { announce } from "../../../app/Announcer";
import { AppStatePill } from "../../../components/page/AppStatePill";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { reportActionError } from "../../apps/useAppActions";
import { JobFailedError, waitForJob } from "./queries";
import {
  useSettingsApp,
  useSubsectionIcon,
  useSubsectionTitle,
} from "./settingsApp";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { SETTINGS_ICONS } from "./objectIcons";

/** The most copies of a background process the API takes. */
const MAX_PROCESS_COPIES = 32;

function whole(text: string, min: number, max: number): number | null {
  const trimmed = text.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number.parseInt(trimmed, 10);
  return value >= min && value <= max ? value : null;
}

// ---------------------------------------------------------------------------------------
// The copies of the web process

/** Asks for the copies and waits for the job it queues: done means done, not queued. */
async function setCopies(
  queryClient: ReturnType<typeof useQueryClient>,
  domain: string,
  copies: number,
): Promise<void> {
  const queued = await request("put", "/api/apps/{domain}/replicas", {
    params: { domain },
    body: { copies },
  });
  const job = await waitForJob(queryClient, queued.job as Job);
  void queryClient.invalidateQueries({
    queryKey: runtimeKeys.replicas(domain),
  });
  void queryClient.invalidateQueries({
    queryKey: appKeys.detail(domain),
    exact: true,
  });
  if (job.status !== "completed") throw new JobFailedError(job.error ?? "");
}

function CopiesList({ domain, status }: { domain: string; status: Replicas }) {
  const t = useT();
  return (
    <RowList aria-label={t("runtime.replicas.listLabel", { domain })}>
      {status.units.map((copy) => (
        <RowList.Item key={copy.unit}>
          <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
            <Mono truncate className="min-w-0 flex-1 text-13" title={copy.unit}>
              {copy.unit}
            </Mono>
            <span className="text-12 text-fg-muted">
              {t("runtime.replicas.columnPort")}
            </span>
            <Mono>{copy.port}</Mono>
            <AppStatePill status={copy.state} appearance="inline" size="sm" />
            {copy.own ? <Badge>{t("runtime.replicas.own")}</Badge> : null}
            <span className="text-12 text-fg-muted">
              {copy.serving
                ? t("runtime.replicas.sharing")
                : t("runtime.replicas.notSharing")}
            </span>
          </div>
        </RowList.Item>
      ))}
    </RowList>
  );
}

function CopiesForm({ domain, status }: { domain: string; status: Replicas }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [text, setText] = useState(String(status.copies));
  const [submitted, setSubmitted] = useState(false);
  const [baseline, setBaseline] = useState(status.copies);
  // The copies changed underneath an untouched field (scaled from a terminal): follow them.
  if (status.copies !== baseline) {
    setBaseline(status.copies);
    if (text.trim() === String(baseline)) setText(String(status.copies));
  }
  const copies = whole(text, 1, status.maximum);
  const apply = useMutation({
    mutationFn: (count: number) => setCopies(queryClient, domain, count),
    onSuccess: (_result, count) => {
      setSubmitted(false);
      announce(t("runtime.replicas.scaled", { domain, count }));
    },
    onError: (error) => {
      reportActionError(t("runtime.replicas.scaleFailed", { domain }), error);
    },
  });
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (copies === null || apply.isPending) return;
    apply.mutate(copies);
  };
  const error =
    submitted && copies === null
      ? t("runtime.replicas.copiesRange", { max: status.maximum })
      : undefined;
  return (
    <form
      noValidate
      onSubmit={submit}
      className="flex flex-wrap items-end gap-3"
    >
      <Field
        label={t("runtime.replicas.copiesLabel")}
        description={t("runtime.replicas.copiesDescription", {
          max: status.maximum,
        })}
        error={error}
        className="w-56"
      >
        <Input
          mono
          inputMode="numeric"
          autoComplete="off"
          value={text}
          onValueChange={setText}
        />
      </Field>
      <Button
        type="submit"
        loading={apply.isPending}
        disabled={copies === status.copies}
      >
        {t("runtime.replicas.apply")}
      </Button>
    </form>
  );
}

/** How the web process is scaled: the copies that run, and how many there should be. */
function ReplicasCard({ domain }: { domain: string }) {
  const t = useT();
  const replicas = useQuery(replicasQuery(domain));
  const status = replicas.data;
  return (
    <Card
      title={t("runtime.replicas.title")}
      icon={SETTINGS_ICONS.processes}
      description={t("runtime.replicas.description")}
    >
      <div className="flex flex-col gap-4">
        {status === undefined ? (
          replicas.isError ? (
            <ErrorBlock
              compact
              error={replicas.error}
              title={t("runtime.replicas.loadFailed")}
              onRetry={() => void replicas.refetch()}
              retrying={replicas.isRefetching}
            />
          ) : (
            <LoadingRegion
              label={t("runtime.replicas.loading")}
              className="flex flex-col gap-2"
            >
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </LoadingRegion>
          )
        ) : (
          <>
            <CopiesList domain={domain} status={status} />
            {status.eligible ? (
              <CopiesForm domain={domain} status={status} />
            ) : (
              <InlineMessage tone="info">
                {t("runtime.replicas.cannot", { reason: status.reason ?? "" })}
              </InlineMessage>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------
// The background processes

interface ProcessDraft {
  name: string;
  command: string;
  instances: string;
  memory: string;
  cpu: string;
  tasks: string;
}

function draftOf(process: AppProcess | undefined): ProcessDraft {
  return {
    name: process?.name ?? "",
    command: process?.command ?? "",
    instances: String(process?.instances ?? 1),
    memory: process?.memory_max_mb == null ? "" : String(process.memory_max_mb),
    cpu:
      process?.cpu_quota_percent == null
        ? ""
        : String(process.cpu_quota_percent),
    tasks: process?.tasks_max == null ? "" : String(process.tasks_max),
  };
}

/** A limit field: blank for none, otherwise a positive whole number. */
function limit(text: string): { value: number | null; bad: boolean } {
  const trimmed = text.trim();
  if (trimmed === "") return { value: null, bad: false };
  const value = whole(trimmed, 1, 10_000_000);
  return { value, bad: value === null };
}

function invalidateProcesses(
  queryClient: ReturnType<typeof useQueryClient>,
  domain: string,
): void {
  void queryClient.invalidateQueries({
    queryKey: runtimeKeys.processes(domain),
  });
  void queryClient.invalidateQueries({ queryKey: runtimeKeys.pending(domain) });
}

/** Adds a process, or changes one: its command, its copies and the limits of each. */
function ProcessDialog({
  domain,
  process,
  open,
  onOpenChange,
}: {
  domain: string;
  process?: AppProcess;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const editing = process !== undefined;
  const [draft, setDraft] = useState(() => draftOf(process));
  const [submitted, setSubmitted] = useState(false);
  const formId = useId();
  const edit = (field: keyof ProcessDraft) => (value: string) => {
    setDraft((previous) => ({ ...previous, [field]: value }));
  };
  const copies = whole(draft.instances, 0, MAX_PROCESS_COPIES);
  const memory = limit(draft.memory);
  const cpu = limit(draft.cpu);
  const tasks = limit(draft.tasks);
  const problems = {
    name: draft.name.trim() === "" ? t("runtime.processes.nameMissing") : null,
    command:
      draft.command.trim() === ""
        ? t("runtime.processes.commandMissing")
        : null,
    instances:
      copies === null
        ? t("runtime.processes.copiesRange", { max: MAX_PROCESS_COPIES })
        : null,
    memory: memory.bad ? t("runtime.processes.limitRange") : null,
    cpu: cpu.bad ? t("runtime.processes.limitRange") : null,
    tasks: tasks.bad ? t("runtime.processes.limitRange") : null,
  };
  const valid = Object.values(problems).every((problem) => problem === null);
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/processes/{name}", {
        params: { domain, name: draft.name.trim() },
        body: {
          command: draft.command.trim(),
          instances: copies ?? 1,
          memory_max_mb: memory.value,
          cpu_quota_percent: cpu.value,
          tasks_max: tasks.value,
        },
      }),
    onSuccess: () => {
      invalidateProcesses(queryClient, domain);
      announce(t("runtime.processes.saved", { name: draft.name.trim() }));
      onOpenChange(false);
    },
  });
  const serverError = (field: string): string | undefined =>
    isApiError(save.error) ? save.error.fields?.[field] : undefined;
  const close = (next: boolean): void => {
    if (!next && save.isPending) return;
    if (!next) save.reset();
    onOpenChange(next);
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (!valid || save.isPending) return;
    save.mutate();
  };
  const shown = (field: keyof typeof problems): string | undefined =>
    submitted ? (problems[field] ?? undefined) : undefined;
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={
        editing
          ? t("runtime.processes.dialogEditTitle", { name: process.name })
          : t("runtime.processes.dialogAddTitle")
      }
      description={t("runtime.processes.dialogDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={save.isPending}
          >
            {t("runtime.processes.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        onSubmit={submit}
        className="flex flex-col gap-5"
      >
        <Field
          label={t("runtime.processes.nameLabel")}
          description={t("runtime.processes.nameDescription")}
          error={shown("name") ?? serverError("name")}
        >
          <Input
            mono
            disabled={editing}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="worker"
            value={draft.name}
            onValueChange={edit("name")}
          />
        </Field>
        <Field
          label={t("runtime.processes.commandLabel")}
          description={t("runtime.processes.commandDescription")}
          error={shown("command") ?? serverError("command")}
        >
          <Input
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="node worker.js"
            value={draft.command}
            onValueChange={edit("command")}
          />
        </Field>
        <div className="grid gap-5 sm:grid-cols-2">
          <Field
            label={t("runtime.processes.copiesLabel")}
            description={t("runtime.processes.copiesDescription")}
            error={shown("instances") ?? serverError("instances")}
          >
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              value={draft.instances}
              onValueChange={edit("instances")}
            />
          </Field>
          <Field
            label={t("runtime.processes.memoryLabel")}
            optional
            description={t("runtime.processes.memoryDescription")}
            error={shown("memory") ?? serverError("memory_max_mb")}
          >
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              suffix="MB"
              value={draft.memory}
              onValueChange={edit("memory")}
            />
          </Field>
          <Field
            label={t("runtime.processes.cpuLabel")}
            optional
            description={t("runtime.processes.cpuDescription")}
            error={shown("cpu") ?? serverError("cpu_quota_percent")}
          >
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              suffix="%"
              value={draft.cpu}
              onValueChange={edit("cpu")}
            />
          </Field>
          <Field
            label={t("runtime.processes.tasksLabel")}
            optional
            description={t("runtime.processes.tasksDescription")}
            error={shown("tasks") ?? serverError("tasks_max")}
          >
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              value={draft.tasks}
              onValueChange={edit("tasks")}
            />
          </Field>
        </div>
        {save.isError && (!isApiError(save.error) || !save.error.fields) ? (
          <ErrorBlock
            live
            compact
            error={save.error}
            title={t("runtime.processes.saveFailed", {
              name: draft.name.trim() || "…",
            })}
          />
        ) : null}
      </form>
    </Dialog>
  );
}

/** How many copies a process runs: a number and a button, in a dialog of its own. */
function ScaleDialog({
  domain,
  process,
  open,
  onOpenChange,
}: {
  domain: string;
  process: AppProcess;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [text, setText] = useState(String(process.instances));
  const [submitted, setSubmitted] = useState(false);
  const copies = whole(text, 0, MAX_PROCESS_COPIES);
  const scale = useMutation({
    mutationFn: (instances: number) =>
      request("post", "/api/apps/{domain}/processes/{name}/scale", {
        params: { domain, name: process.name },
        body: { instances },
      }),
    onSuccess: (_result, instances) => {
      invalidateProcesses(queryClient, domain);
      announce(
        t("runtime.processes.scaled", { name: process.name, count: instances }),
      );
      onOpenChange(false);
    },
  });
  const close = (next: boolean): void => {
    if (!next && scale.isPending) return;
    if (!next) scale.reset();
    onOpenChange(next);
  };
  const formId = useId();
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (copies === null || scale.isPending) return;
    scale.mutate(copies);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="sm"
      title={t("runtime.processes.scaleTitle", { name: process.name })}
      description={t("runtime.processes.scaleDescription")}
      footer={
        <>
          <Button disabled={scale.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={scale.isPending}
          >
            {t("runtime.processes.scaleSave")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        onSubmit={submit}
        className="flex flex-col gap-4"
      >
        <Field
          label={t("runtime.processes.copiesLabel")}
          description={t("runtime.processes.copiesDescription")}
          error={
            submitted && copies === null
              ? t("runtime.processes.copiesRange", { max: MAX_PROCESS_COPIES })
              : undefined
          }
        >
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            value={text}
            onValueChange={setText}
          />
        </Field>
        {scale.isError ? (
          <ErrorBlock
            live
            compact
            error={scale.error}
            title={t("runtime.processes.scaleFailed", { name: process.name })}
          />
        ) : null}
      </form>
    </Dialog>
  );
}

/** The menu at the end of a process: change it, change its copies, remove it. */
function ProcessActions({
  domain,
  process,
  onEdit,
  onScale,
}: {
  domain: string;
  process: AppProcess;
  onEdit: () => void;
  onScale: () => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const remove = useMutation({
    mutationFn: () =>
      request("delete", "/api/apps/{domain}/processes/{name}", {
        params: { domain, name: process.name },
      }),
    onSuccess: () => {
      invalidateProcesses(queryClient, domain);
      announce(t("runtime.processes.removed", { name: process.name }));
    },
    onError: (error) => {
      reportActionError(
        t("runtime.processes.removeFailed", { name: process.name }),
        error,
      );
    },
  });
  return (
    <>
      <Menu
        align="end"
        trigger={
          <IconButton
            label={t("runtime.processes.actionsFor", { name: process.name })}
            icon={<ICONS.more />}
            size="sm"
            tooltip={false}
          />
        }
      >
        <MenuItem icon={<Pencil />} onClick={onEdit}>
          {t("runtime.processes.edit")}
        </MenuItem>
        <MenuItem icon={<Scaling />} onClick={onScale}>
          {t("runtime.processes.scale")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem
          icon={<Trash2 />}
          destructive
          onClick={() => setRemoving(true)}
        >
          {t("runtime.processes.remove")}
        </MenuItem>
      </Menu>
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("runtime.processes.removeTitle", { name: process.name })}
        description={t("runtime.processes.removeDescription")}
        actionLabel={t("runtime.processes.remove")}
        destructive
        onConfirm={async () => {
          await remove.mutateAsync();
        }}
      />
    </>
  );
}

function ProcessRow({
  domain,
  process,
}: {
  domain: string;
  process: AppProcess;
}) {
  const t = useT();
  const [dialog, setDialog] = useState<"edit" | "scale" | null>(null);
  const running = process.running;
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-baseline gap-2">
          <Mono className="text-13 font-medium text-fg">{process.name}</Mono>
          {process.source === "repository" ? (
            <Badge>{t("runtime.processes.fromRepository")}</Badge>
          ) : null}
        </span>
        <Mono tone="muted" truncate title={process.command}>
          {process.command}
        </Mono>
      </div>
      <span className="text-12 text-fg-muted">
        {process.instances === 0
          ? t("runtime.processes.stopped")
          : t("runtime.processes.runningOf", {
              running,
              instances: process.instances,
            })}
      </span>
      <div className="flex shrink-0 flex-wrap items-center gap-1">
        {(process.copies ?? []).map((copy) => (
          <AppStatePill
            key={copy.unit}
            status={copy.state}
            appearance="inline"
            size="sm"
          />
        ))}
      </div>
      <ProcessActions
        domain={domain}
        process={process}
        onEdit={() => {
          setDialog("edit");
        }}
        onScale={() => {
          setDialog("scale");
        }}
      />
      {dialog === "edit" ? (
        <ProcessDialog
          domain={domain}
          process={process}
          open
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      ) : null}
      {dialog === "scale" ? (
        <ScaleDialog
          domain={domain}
          process={process}
          open
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      ) : null}
    </li>
  );
}

function ProcessesCard({ domain }: { domain: string }) {
  const t = useT();
  const [adding, setAdding] = useState(false);
  const processes = useQuery(processesQuery(domain));
  const list = processes.data?.processes ?? [];
  return (
    <Card
      icon={SETTINGS_ICONS.processes}
      title={t("runtime.processes.title")}
      description={t("runtime.processes.description")}
      actions={
        <Button
          size="sm"
          icon={<Plus aria-hidden="true" />}
          onClick={() => {
            setAdding(true);
          }}
        >
          {t("runtime.processes.add")}
        </Button>
      }
      padding="none"
    >
      {processes.data === undefined ? (
        processes.isError ? (
          <div className="p-4">
            <ErrorBlock
              compact
              error={processes.error}
              title={t("runtime.processes.loadFailed")}
              onRetry={() => void processes.refetch()}
              retrying={processes.isRefetching}
            />
          </div>
        ) : (
          <LoadingRegion
            label={t("runtime.processes.loading")}
            className="flex flex-col gap-2 p-4"
          >
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </LoadingRegion>
        )
      ) : list.length === 0 ? (
        <div className="p-4">
          <EmptyState
            variant="inline"
            title={t("runtime.processes.emptyTitle")}
            description={t("runtime.processes.emptyDescription")}
          />
        </div>
      ) : (
        <ul
          aria-label={t("runtime.processes.listLabel", { domain })}
          className="flex flex-col divide-y divide-border"
        >
          {list.map((process) => (
            <ProcessRow key={process.name} domain={domain} process={process} />
          ))}
        </ul>
      )}
      {adding ? (
        <ProcessDialog domain={domain} open onOpenChange={setAdding} />
      ) : null}
    </Card>
  );
}

/**
 * Processes: the copies of the web process, and the background processes (queue workers,
 * schedulers) that run beside it from the same release. Both apply at once, behind sudo mode,
 * and are judged like an activation: copies that do not answer or stay up are put back.
 */
export function ProcessesSettings() {
  const t = useT();
  const app = useSettingsApp();
  useSubsectionTitle("runtime.nav.processes", app.domain);
  const headerIcon = useSubsectionIcon("runtime.nav.processes");
  return (
    <Section title={t("runtime.nav.processes")} {...headerIcon}>
      {hasUnit(app) ? (
        <div className="flex flex-col gap-6">
          <ReplicasCard domain={app.domain} />
          <ProcessesCard domain={app.domain} />
        </div>
      ) : (
        <Card>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">
            {t("runtime.replicas.cannot", {
              reason: t("appSettings.limits.staticNote"),
            })}
          </p>
        </Card>
      )}
    </Section>
  );
}
