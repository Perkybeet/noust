import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Send, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { logDrainsQuery, runtimeKeys } from "../../api/queries/runtime";
import type { LogDrain } from "../../api/queries/runtime";
import { useDocumentTitle } from "../../app/documentTitle";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { SkeletonRows } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatCount } from "../../lib/format";
import { reportActionError } from "../apps/useAppActions";

type Kind = "loki" | "axiom" | "newrelic" | "http" | "otlp" | "syslog";
const KINDS: readonly Kind[] = ["loki", "axiom", "newrelic", "http", "otlp", "syslog"];

function kindWords(t: T, kind: string): string {
  switch (kind) {
    case "loki":
      return t("runtime.logShipping.kindLoki");
    case "axiom":
      return t("runtime.logShipping.kindAxiom");
    case "newrelic":
      return t("runtime.logShipping.kindNewRelic");
    case "http":
      return t("runtime.logShipping.kindHttp");
    case "otlp":
      return t("runtime.logShipping.kindOtlp");
    default:
      return t("runtime.logShipping.kindSyslog");
  }
}

function text(value: unknown): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

function labelsText(value: unknown): string {
  if (typeof value !== "object" || value === null) return "";
  return Object.entries(value as Record<string, unknown>)
    .map(([name, label]) => `${name}=${text(label)}`)
    .join("\n");
}

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

interface Draft {
  name: string;
  kind: Kind;
  url: string;
  tenant: string;
  username: string;
  auth: string;
  dataset: string;
  region: string;
  secret: string;
  transport: string;
  host: string;
  port: string;
  facility: string;
  ca: string;
  pin: string;
  labels: string;
  apps: string;
  enabled: boolean;
}

function draftOf(drain: LogDrain | undefined): Draft {
  const settings = (drain?.settings ?? {}) as Record<string, unknown>;
  return {
    name: drain?.name ?? "",
    kind: (KINDS as readonly string[]).includes(drain?.kind ?? "") ? (drain?.kind as Kind) : "loki",
    url: text(settings["url"]),
    tenant: text(settings["tenant"]),
    username: text(settings["username"]),
    auth: text(settings["auth"]),
    dataset: text(settings["dataset"]),
    region: text(settings["region"]) || "us",
    secret: "",
    transport: text(settings["transport"]) || "tls",
    host: text(settings["host"]),
    port: text(settings["port"]),
    facility: text(settings["facility"]),
    ca: text(settings["ca"]),
    pin: text(settings["pin_sha256"]),
    labels: labelsText(settings["labels"]),
    apps: (drain?.apps ?? []).join("\n"),
    enabled: drain?.enabled ?? true,
  };
}

function wholeInRange(value: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(value)) return null;
  const number = Number.parseInt(value, 10);
  return number >= min && number <= max ? number : null;
}

type Problems = Partial<Record<keyof Draft | "labels", string>>;

/** What the destination would be told, and what is wrong with it before anything is sent. */
function build(draft: Draft, editing: boolean, hasSecret: boolean, t: T): { settings: Record<string, unknown>; problems: Problems } {
  const problems: Problems = {};
  const settings: Record<string, unknown> = {};
  if (draft.name.trim() === "") problems.name = t("runtime.logShipping.nameMissing");
  const labels: Record<string, string> = {};
  for (const line of lines(draft.labels)) {
    const [name, ...rest] = line.split("=");
    if (name === undefined || name.trim() === "" || rest.length === 0) {
      problems.labels = t("runtime.logShipping.labelsHow");
      break;
    }
    labels[name.trim()] = rest.join("=");
  }
  if (Object.keys(labels).length > 0) settings["labels"] = labels;
  if (draft.kind === "syslog") {
    settings["transport"] = draft.transport;
    if (draft.host.trim() === "") problems.host = t("runtime.logShipping.hostMissing");
    else settings["host"] = draft.host.trim();
    if (draft.port.trim() !== "") {
      const port = wholeInRange(draft.port.trim(), 1, 65535);
      if (port === null) problems.port = t("runtime.logShipping.portRange");
      else settings["port"] = port;
    }
    if (draft.facility.trim() !== "") {
      const facility = wholeInRange(draft.facility.trim(), 0, 23);
      if (facility === null) problems.facility = t("runtime.logShipping.facilityRange");
      else settings["facility"] = facility;
    }
    if (draft.ca.trim() !== "") settings["ca"] = draft.ca.trim();
    if (draft.pin.trim() !== "") settings["pin_sha256"] = draft.pin.trim();
    return { settings, problems };
  }
  if (draft.kind === "axiom") {
    if (draft.url.trim() !== "") settings["url"] = draft.url.trim();
    if (draft.dataset.trim() === "") problems.dataset = t("runtime.logShipping.datasetMissing");
    else settings["dataset"] = draft.dataset.trim();
    if (draft.secret.trim() === "" && !(editing && hasSecret)) problems.secret = t("runtime.logShipping.secretMissing");
    return { settings, problems };
  }
  if (draft.kind === "newrelic") {
    settings["region"] = draft.region;
    if (draft.url.trim() !== "") settings["url"] = draft.url.trim();
    if (draft.secret.trim() === "" && !(editing && hasSecret)) problems.secret = t("runtime.logShipping.licenseMissing");
    return { settings, problems };
  }
  if (draft.url.trim() === "") problems.url = t("runtime.logShipping.urlMissing");
  else settings["url"] = draft.url.trim();
  if (draft.kind === "loki" && draft.tenant.trim() !== "") settings["tenant"] = draft.tenant.trim();
  if (draft.username.trim() !== "") settings["username"] = draft.username.trim();
  if (draft.kind !== "loki" && draft.auth !== "") settings["auth"] = draft.auth;
  return { settings, problems };
}

/** A destination of logs: where it is, how it is told who is shipping, and which applications. */
function DrainDialog({ drain, open, onOpenChange }: { drain?: LogDrain; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const editing = drain !== undefined;
  const formId = useId();
  const [draft, setDraft] = useState(() => draftOf(drain));
  const [submitted, setSubmitted] = useState(false);
  const set = <K extends keyof Draft>(field: K) => (value: Draft[K]) => {
    setDraft((previous) => ({ ...previous, [field]: value }));
  };
  const { settings, problems } = build(draft, editing, drain?.has_secret ?? false, t);
  const valid = Object.keys(problems).length === 0;
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/log-drains/{name}", {
        params: { name: draft.name.trim() },
        body: {
          kind: draft.kind,
          settings,
          apps: lines(draft.apps),
          enabled: draft.enabled,
          ...(draft.secret.trim() !== "" ? { secret: draft.secret.trim(), keep_secret: false } : { keep_secret: editing }),
        },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runtimeKeys.logDrains });
      onOpenChange(false);
    },
  });
  const serverError = (field: string): string | undefined => (isApiError(save.error) ? save.error.fields?.[field] : undefined);
  const shown = (field: keyof Problems): string | undefined => (submitted ? problems[field] : undefined);
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
  const secretLabel = draft.kind === "syslog" ? null : t("runtime.logShipping.secretLabel");
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={editing ? t("runtime.logShipping.dialogEditTitle", { name: drain.name }) : t("runtime.logShipping.dialogAddTitle")}
      description={t("runtime.logShipping.dialogDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("runtime.logShipping.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label={t("runtime.logShipping.nameLabel")} description={t("runtime.logShipping.nameDescription")} error={shown("name") ?? serverError("name")}>
            <Input mono disabled={editing} autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="grafana" value={draft.name} onValueChange={set("name")} />
          </Field>
          <Field label={t("runtime.logShipping.kindLabel")} nativeLabel={false} error={serverError("kind")}>
            <Select<Kind>
              value={draft.kind}
              disabled={editing}
              options={KINDS.map((kind) => ({ value: kind, label: kindWords(t, kind) }))}
              onValueChange={set("kind")}
            />
          </Field>
        </div>
        {draft.kind === "syslog" ? (
          <>
            <div className="grid gap-5 sm:grid-cols-3">
              <Field label={t("runtime.logShipping.transportLabel")} nativeLabel={false} error={serverError("transport")}>
                <Select
                  value={draft.transport}
                  options={[
                    { value: "tls", label: t("runtime.logShipping.transportTls") },
                    { value: "tcp", label: t("runtime.logShipping.transportTcp") },
                    { value: "udp", label: t("runtime.logShipping.transportUdp") },
                  ]}
                  onValueChange={set("transport")}
                />
              </Field>
              <Field className="sm:col-span-2" label={t("runtime.logShipping.hostLabel")} description={t("runtime.logShipping.hostDescription")} error={shown("host") ?? serverError("host")}>
                <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="logs.example.com" value={draft.host} onValueChange={set("host")} />
              </Field>
              <Field label={t("runtime.logShipping.portLabel")} description={t("runtime.logShipping.portDescription")} error={shown("port") ?? serverError("port")}>
                <Input mono inputMode="numeric" autoComplete="off" value={draft.port} onValueChange={set("port")} />
              </Field>
              <Field label={t("runtime.logShipping.facilityLabel")} description={t("runtime.logShipping.facilityDescription")} error={shown("facility") ?? serverError("facility")}>
                <Input mono inputMode="numeric" autoComplete="off" value={draft.facility} onValueChange={set("facility")} />
              </Field>
            </div>
            {draft.transport === "tls" ? (
              <div className="grid gap-5 sm:grid-cols-2">
                <Field label={t("runtime.logShipping.caLabel")} optional description={t("runtime.logShipping.caDescription")} error={serverError("ca")}>
                  <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={draft.ca} onValueChange={set("ca")} />
                </Field>
                <Field label={t("runtime.logShipping.pinLabel")} optional description={t("runtime.logShipping.pinDescription")} error={serverError("pin_sha256")}>
                  <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={draft.pin} onValueChange={set("pin")} />
                </Field>
              </div>
            ) : null}
          </>
        ) : (
          <>
            <Field
              label={t("runtime.logShipping.urlLabel")}
              description={t("runtime.logShipping.urlDescription")}
              optional={draft.kind === "axiom" || draft.kind === "newrelic"}
              error={shown("url") ?? serverError("url")}
            >
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder={draft.kind === "axiom" ? "https://api.axiom.co" : draft.kind === "newrelic" ? "https://log-api.newrelic.com/log/v1" : draft.kind === "loki" ? "https://logs.example.com" : "https://collector.example.com"}
                value={draft.url}
                onValueChange={set("url")}
              />
            </Field>
            {draft.kind === "axiom" ? (
              <Field label={t("runtime.logShipping.datasetLabel")} description={t("runtime.logShipping.datasetDescription")} error={shown("dataset") ?? serverError("dataset")}>
                <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={draft.dataset} onValueChange={set("dataset")} />
              </Field>
            ) : null}
            {draft.kind === "newrelic" ? (
              <Field label={t("runtime.logShipping.regionLabel")} nativeLabel={false} description={t("runtime.logShipping.regionDescription")} error={serverError("region")}>
                <Select
                  value={draft.region}
                  options={[
                    { value: "us", label: t("runtime.logShipping.regionUs") },
                    { value: "eu", label: t("runtime.logShipping.regionEu") },
                  ]}
                  onValueChange={set("region")}
                />
              </Field>
            ) : null}
            {draft.kind === "loki" ? (
              <Field label={t("runtime.logShipping.tenantLabel")} optional description={t("runtime.logShipping.tenantDescription")} error={serverError("tenant")}>
                <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={draft.tenant} onValueChange={set("tenant")} />
              </Field>
            ) : null}
            {draft.kind === "http" || draft.kind === "otlp" ? (
              <Field label={t("runtime.logShipping.authLabel")} nativeLabel={false} error={serverError("auth")}>
                <Select
                  value={draft.auth === "" ? "bearer" : draft.auth}
                  options={[
                    { value: "bearer", label: t("runtime.logShipping.authBearer") },
                    { value: "basic", label: t("runtime.logShipping.authBasic") },
                    { value: "none", label: t("runtime.logShipping.authNone") },
                  ]}
                  onValueChange={set("auth")}
                />
              </Field>
            ) : null}
            {draft.kind !== "axiom" && draft.kind !== "newrelic" ? (
              <Field label={t("runtime.logShipping.usernameLabel")} optional description={t("runtime.logShipping.usernameDescription")} error={serverError("username")}>
                <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={draft.username} onValueChange={set("username")} />
              </Field>
            ) : null}
          </>
        )}
        {secretLabel !== null ? (
          <Field
            label={secretLabel}
            optional={draft.kind !== "axiom" && draft.kind !== "newrelic"}
            description={editing && drain.has_secret ? t("runtime.logShipping.secretDescriptionKeep") : t("runtime.logShipping.secretDescriptionNew")}
            error={shown("secret") ?? serverError("secret")}
          >
            <Input mono type="password" autoComplete="new-password" spellCheck={false} value={draft.secret} onValueChange={set("secret")} />
          </Field>
        ) : null}
        <Field label={t("runtime.logShipping.appsLabel")} optional description={t("runtime.logShipping.appsDescription")} error={serverError("apps")}>
          <Textarea mono rows={2} autoComplete="off" value={draft.apps} onChange={(event) => set("apps")(event.target.value)} />
        </Field>
        <Field label={t("runtime.logShipping.labelsLabel")} optional description={t("runtime.logShipping.labelsDescription")} error={shown("labels") ?? serverError("labels")}>
          <Textarea mono rows={2} autoComplete="off" placeholder="env=prod" value={draft.labels} onChange={(event) => set("labels")(event.target.value)} />
        </Field>
        <Checkbox label={t("runtime.logShipping.enabledLabel")} description={t("runtime.logShipping.enabledDescription")} checked={draft.enabled} onCheckedChange={set("enabled")} />
        {save.isError && (!isApiError(save.error) || !save.error.fields) ? (
          <ErrorBlock live compact error={save.error} title={t("runtime.logShipping.saveFailed", { name: draft.name.trim() || "…" })} />
        ) : null}
      </form>
    </Dialog>
  );
}

function DrainActions({ drain, onEdit }: { drain: LogDrain; onEdit: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: runtimeKeys.logDrains });
  };
  const test = useMutation({
    mutationFn: () => request("post", "/api/log-drains/{name}/test", { params: { name: drain.name } }),
    onSuccess: (result) => {
      // design-exception: toast-success what the destination received is not on this screen
      toast.success(t("runtime.logShipping.tested", { name: drain.name }), { description: t("runtime.logShipping.testedDetail", { line: result.sent }) });
      refresh();
    },
    onError: (error) => {
      reportActionError(t("runtime.logShipping.testFailed", { name: drain.name }), error);
      refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: () =>
      request("put", "/api/log-drains/{name}", {
        params: { name: drain.name },
        body: { kind: drain.kind, settings: drain.settings, apps: drain.apps, enabled: !drain.enabled, keep_secret: true },
      }),
    onSuccess: refresh,
    onError: (error) => {
      reportActionError(t("runtime.logShipping.toggleFailed", { name: drain.name }), error);
    },
  });
  const remove = useMutation({
    mutationFn: () => request("delete", "/api/log-drains/{name}", { params: { name: drain.name } }),
    onSuccess: refresh,
    onError: (error) => {
      reportActionError(t("runtime.logShipping.removeFailed", { name: drain.name }), error);
    },
  });
  return (
    <>
      <Menu align="end" trigger={<IconButton label={t("runtime.logShipping.actionsFor", { name: drain.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
        <MenuItem icon={<Send />} disabled={test.isPending} onClick={() => test.mutate()}>
          {t("runtime.logShipping.test")}
        </MenuItem>
        <MenuItem icon={<Pencil />} onClick={onEdit}>
          {t("runtime.logShipping.edit")}
        </MenuItem>
        <MenuItem icon={<Power />} disabled={toggle.isPending} onClick={() => toggle.mutate()}>
          {drain.enabled ? t("runtime.logShipping.turnOff") : t("runtime.logShipping.turnOn")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem
          icon={<Trash2 />}
          destructive
          onClick={() => {
            setRemoving(true);
          }}
        >
          {t("runtime.logShipping.remove")}
        </MenuItem>
      </Menu>
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("runtime.logShipping.removeTitle", { name: drain.name })}
        description={t("runtime.logShipping.removeDescription")}
        actionLabel={t("runtime.logShipping.remove")}
        destructive
        onConfirm={async () => {
          await remove.mutateAsync();
        }}
      />
    </>
  );
}

function DrainRow({ drain }: { drain: LogDrain }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const failing = drain.enabled && drain.last_error != null;
  return (
    <li className="flex min-w-0 flex-col gap-2 px-4 py-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex min-w-0 items-baseline gap-2">
            <Mono className="text-13 font-medium text-fg">{drain.name}</Mono>
            <Badge>{kindWords(t, drain.kind)}</Badge>
            {drain.plaintext ? <Badge>{t("runtime.logShipping.plaintext")}</Badge> : null}
          </span>
          <span className="text-12 text-fg-muted">{drain.apps.length === 0 ? t("runtime.logShipping.allApps") : drain.apps.join(", ")}</span>
        </div>
        <StatusPill
          state={!drain.enabled ? "stopped" : failing ? "failed" : "running"}
          label={!drain.enabled ? t("runtime.logShipping.stateOff") : failing ? t("runtime.logShipping.stateFailing") : t("runtime.logShipping.stateShipping")}
          appearance="inline"
          size="sm"
        />
        <span className="flex flex-wrap items-center gap-x-3 text-12 text-fg-muted">
          <span>
            {t("runtime.logShipping.lastDelivery")}: <RelativeTime value={drain.last_ok_at} fallback={t("runtime.logShipping.never")} />
          </span>
          <span>{t("runtime.logShipping.shipped", { count: drain.shipped, lines: formatCount(drain.shipped, t.locale) })}</span>
        </span>
        <DrainActions
          drain={drain}
          onEdit={() => {
            setEditing(true);
          }}
        />
      </div>
      {failing && drain.last_error != null ? (
        <div className="flex flex-col gap-1">
          <p className="text-12 text-fg-muted">{t("runtime.logShipping.lastError")}</p>
          <SystemOutput label={t("runtime.logShipping.errorLabel", { name: drain.name })} maxHeight="max-h-28">
            {drain.last_error}
          </SystemOutput>
        </div>
      ) : null}
      {editing ? <DrainDialog drain={drain} open onOpenChange={setEditing} /> : null}
    </li>
  );
}

/**
 * Log shipping: where the logs of applications go besides the journal. Destinations are the
 * server's own; their tokens are written and never read back, and every line is scrubbed of its
 * application's secrets before it leaves. Changing them needs sudo mode.
 */
export function LogShippingSettings() {
  const t = useT();
  useDocumentTitle(t("runtime.logShipping.title"), 1);
  const drains = useQuery(logDrainsQuery());
  const [adding, setAdding] = useState(false);
  const data = drains.data;
  const notRunning = data !== undefined && data.drains.some((drain) => drain.enabled) && !data.service["active"];
  return (
    <Section title={t("runtime.logShipping.title")} description={t("runtime.logShipping.description")}>
      {notRunning ? (
        <Notice tone="warning" title={t("runtime.logShipping.notRunningTitle")}>
          {t("runtime.logShipping.notRunningBody")}
        </Notice>
      ) : null}
      <Card
        title={t("runtime.logShipping.destinations")}
        padding="none"
        actions={
          <Button icon={<Plus aria-hidden="true" />}
            size="sm"
            variant="primary"
            onClick={() => {
              setAdding(true);
            }}
          >
            {t("runtime.logShipping.add")}
          </Button>
        }
      >
        <div className="p-4">
          <QueryState
            query={drains}
            label={t("runtime.logShipping.noun")}
            skeleton={<SkeletonRows rows={2} />}
            isEmpty={(answer) => answer.drains.length === 0}
            empty={<EmptyState variant="inline" title={t("runtime.logShipping.emptyTitle")} description={t("runtime.logShipping.emptyDescription")} />}
          >
            {(answer) => (
              <ul aria-label={t("runtime.logShipping.listLabel")} className="flex flex-col divide-y divide-border">
                {answer.drains.map((drain) => (
                  <DrainRow key={drain.name} drain={drain} />
                ))}
              </ul>
            )}
          </QueryState>
        </div>
      </Card>
      {adding ? <DrainDialog open onOpenChange={setAdding} /> : null}
    </Section>
  );
}
