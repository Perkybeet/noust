import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { healthDetailsQuery, runtimeKeys } from "../../../api/queries/runtime";
import type { HealthDetails } from "../../../api/queries/runtime";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { changedFields } from "./formParts";
import type { FormPart } from "./formParts";
import { HEALTH_DEFAULTS, HEALTH_TIMEOUT_MAX, HEALTH_TIMEOUT_MIN, effectiveHealth, healthDraftOf, healthFieldOf, parseHealth, sameHealth } from "./healthCheck";
import type { HealthDraft, HealthErrors, HealthValues } from "./healthCheck";
import {
  EXTRAS_DEFAULTS,
  EXTRAS_EMPTY,
  HEALTH_METHODS,
  HEALTH_SCHEMES,
  INTERVAL_MAX,
  INTERVAL_MIN,
  RETRIES_MAX,
  RETRIES_MIN,
  START_PERIOD_MAX,
  START_PERIOD_MIN,
  extrasChanged,
  extrasDraftOf,
  extrasFieldOf,
  hasExtras,
  parseExtras,
  sameExtras,
} from "./healthExtras";
import type { HealthExtrasDraft, HealthExtrasErrors, HealthKind } from "./healthExtras";
import { LoadingRegion } from "../../../components/page";

const EMPTY: HealthDraft = { path: "", expect: "", timeout: "" };

/** A Compose stack runs a command check in one of its services. */
const COMPOSE = "docker-compose";

export interface ExtrasPart {
  draft: HealthExtrasDraft;
  /** The extra fields are still being read: nothing is saved before they are known. */
  loading: boolean;
  failed: unknown;
  edit: (field: keyof HealthExtrasDraft) => (value: string) => void;
  setKind: (kind: HealthKind) => void;
  blur: (field: keyof HealthExtrasDraft) => () => void;
  errorOf: (field: keyof HealthExtrasDraft) => string | undefined;
}

export interface StartupCheckPart {
  part: FormPart;
  draft: HealthDraft;
  edit: (field: keyof HealthDraft) => (value: string) => void;
  blur: (field: keyof HealthDraft) => () => void;
  errorOf: (field: keyof HealthDraft) => string | undefined;
  /** How it asks and the timing: stored beside the path, the statuses and the time limit. */
  extras: ExtrasPart;
  /** A refusal no field is about, shown whole above the save bar's reach. */
  unplaced: unknown;
  /** Empties every field: the defaults, once saved. */
  toDefaults: () => void;
}

/**
 * The startup check as a part of the Deploys form: how it asks (a request to the application,
 * or a command run as it), the path the gate requests on 127.0.0.1, the statuses that pass,
 * the request's method, scheme, Host header and port, and the timing: how long it waits, how
 * often it asks and how many times. An empty field is the default. The rules are checked here
 * for a quick answer and again, authoritatively, where the settings are stored; a refusal lands
 * on the field it is about. Nothing restarts: the next activation uses them.
 *
 * All of it is stored together (a field left out goes back to its default), so the form reads
 * the whole check before it lets anything be saved and always sends all of it.
 */
export function useStartupCheck(app: App): StartupCheckPart {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const current = healthDraftOf(app);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [touched, setTouched] = useState<Partial<Record<keyof HealthDraft, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);

  const details = useQuery(healthDetailsQuery(domain));
  const storedExtras = extrasDraftOf(details.data);
  const [extrasBaseline, setExtrasBaseline] = useState(storedExtras);
  const [extras, setExtras] = useState(storedExtras);
  const [extrasTouched, setExtrasTouched] = useState<Partial<Record<keyof HealthExtrasDraft, boolean>>>({});

  // The app changed underneath an untouched form (saved here, or from a terminal): follow it.
  if (!sameHealth(current, baseline)) {
    setBaseline(current);
    if (sameHealth(draft, baseline)) setDraft(current);
  }
  if (!sameExtras(storedExtras, extrasBaseline)) {
    setExtrasBaseline(storedExtras);
    if (sameExtras(extras, extrasBaseline)) setExtras(storedExtras);
  }

  const parsed = parseHealth(draft, t.locale);
  const parsedExtras = parseExtras(extras, t.locale);

  const save = useMutation({
    mutationFn: (values: HealthValues & ReturnType<typeof parseExtras>["values"]) =>
      request("patch", "/api/apps/{domain}/health", { params: { domain }, body: values }),
    onSuccess: (result: HealthDetails) => {
      // What was saved is what the form now says, whatever the app's detail says next.
      const saved = healthDraftOf({ health_path: result.path ?? null, health_expect: result.expect ?? null, health_timeout: result.timeout ?? null });
      setDraft(saved);
      setBaseline(saved);
      const savedExtras = extrasDraftOf(result);
      setExtras(savedExtras);
      setExtrasBaseline(savedExtras);
      setSubmitted(false);
      setTouched({});
      setExtrasTouched({});
      queryClient.setQueryData<HealthDetails>(runtimeKeys.health(domain), result);
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) =>
        known ? { ...known, health_path: result.path ?? null, health_expect: result.expect ?? null, health_timeout: result.timeout ?? null } : known,
      );
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
    },
  });

  const serverFields: HealthErrors = {};
  const serverExtras: HealthExtrasErrors = {};
  let unplaced: unknown = null;
  if (save.isError && isApiError(save.error)) {
    if (save.error.fields) {
      for (const [name, message] of Object.entries(save.error.fields)) {
        if (name === "path" || name === "expect" || name === "timeout") serverFields[name] = message;
        else {
          const extra = extrasFieldOf(name);
          if (extra !== null) serverExtras[extra] = message;
        }
      }
    } else {
      const field = healthFieldOf(save.error.detail);
      if (field !== null) serverFields[field] = save.error.hint ? `${save.error.detail}. ${save.error.hint}` : save.error.detail;
    }
    if (Object.keys(serverFields).length === 0 && Object.keys(serverExtras).length === 0) unplaced = save.error;
  } else if (save.isError) {
    unplaced = save.error;
  }

  const loaded = details.data !== undefined;
  return {
    part: {
      changes: changedFields(draft, current) + (loaded ? extrasChanged(extras, storedExtras) : 0),
      elevated: true,
      check: () => {
        setSubmitted(true);
        return loaded && Object.keys(parsed.errors).length === 0 && Object.keys(parsedExtras.errors).length === 0;
      },
      save: async () => {
        await save.mutateAsync({ ...parsed.values, ...parsedExtras.values });
      },
      discard: () => {
        setDraft(current);
        setExtras(storedExtras);
        setTouched({});
        setExtrasTouched({});
        setSubmitted(false);
        save.reset();
      },
    },
    draft,
    edit: (field) => (value) => {
      setDraft((previous) => ({ ...previous, [field]: value }));
      if (save.isError) save.reset();
    },
    blur: (field) => () => {
      setTouched((previous) => ({ ...previous, [field]: true }));
    },
    errorOf: (field) => (submitted || touched[field] ? parsed.errors[field] : undefined) ?? serverFields[field],
    extras: {
      draft: extras,
      loading: details.isPending,
      failed: details.isError ? details.error : null,
      edit: (field) => (value) => {
        setExtras((previous) => ({ ...previous, [field]: value }));
        if (save.isError) save.reset();
      },
      setKind: (kind) => {
        setExtras((previous) => ({ ...previous, kind }));
        if (save.isError) save.reset();
      },
      blur: (field) => () => {
        setExtrasTouched((previous) => ({ ...previous, [field]: true }));
      },
      errorOf: (field) => (submitted || extrasTouched[field] ? parsedExtras.errors[field] : undefined) ?? serverExtras[field],
    },
    unplaced,
    toDefaults: () => {
      setDraft(EMPTY);
      setExtras(EXTRAS_EMPTY);
      save.reset();
    },
  };
}

/** What the gate asks now, each value marked when it is the default. */
function Effective({ app, extras }: { app: App; extras: HealthExtrasDraft }) {
  const t = useT();
  const now = effectiveHealth(app, t.locale);
  const mark = (isDefault: boolean) => (isDefault ? <span className="text-fg-faint"> ({t("appSettings.healthCheck.default")})</span> : null);
  const timeout = (
    <>
      <Mono>{`${String(now.timeout)}s`}</Mono>
      {mark(now.defaults.timeout)}
    </>
  );
  if (extras.kind === "command") {
    return (
      <p className="text-12 text-pretty text-fg-muted">
        {t.rich("runtime.health.nowCommand", { command: <Mono>{extras.command.trim() || "…"}</Mono>, timeout })}
      </p>
    );
  }
  const method = extras.method.trim().toUpperCase() || EXTRAS_DEFAULTS.method;
  return (
    <p className="text-12 text-pretty text-fg-muted">
      {t.rich("appSettings.healthCheck.now", {
        path: (
          <>
            <Mono>{`${method} ${now.path}`}</Mono>
            {mark(now.defaults.path)}
          </>
        ),
        expect: (
          <>
            <span className="text-fg">{now.expect}</span>
            {mark(now.defaults.expect)}
          </>
        ),
        timeout,
      })}
    </p>
  );
}

function numeric(placeholder: string | number) {
  return { mono: true, inputMode: "numeric" as const, autoComplete: "off", placeholder: String(placeholder) };
}

/** The request's own options: method, scheme, the Host header and the port to ask. */
function RequestOptions({ extras }: { extras: ExtrasPart }) {
  const t = useT();
  const { draft, edit, blur, errorOf } = extras;
  return (
    <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
      <Field
        nativeLabel={false}
        label={t("runtime.health.methodLabel")}
        description={t("runtime.health.methodDescription", { default: EXTRAS_DEFAULTS.method })}
        error={errorOf("method")}
      >
        <Select
          mono
          value={draft.method === "" ? EXTRAS_DEFAULTS.method : draft.method.toUpperCase()}
          options={HEALTH_METHODS.map((method) => ({ value: method, label: method }))}
          onValueChange={(method) => {
            // The default is the empty field: stored as nothing, not as the word.
            edit("method")(method === EXTRAS_DEFAULTS.method ? "" : method);
          }}
        />
      </Field>
      <Field
        nativeLabel={false}
        label={t("runtime.health.schemeLabel")}
        description={t("runtime.health.schemeDescription", { default: EXTRAS_DEFAULTS.scheme })}
        error={errorOf("scheme")}
      >
        <Select
          mono
          value={draft.scheme === "" ? EXTRAS_DEFAULTS.scheme : draft.scheme.toLowerCase()}
          options={HEALTH_SCHEMES.map((scheme) => ({ value: scheme, label: scheme }))}
          onValueChange={(scheme) => {
            edit("scheme")(scheme === EXTRAS_DEFAULTS.scheme ? "" : scheme);
          }}
        />
      </Field>
      <Field className="col-span-2 sm:col-span-1" label={t("runtime.health.hostLabel")} description={t("runtime.health.hostDescription")} error={errorOf("host")}>
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="shop.example.com"
          value={draft.host}
          onValueChange={edit("host")}
          onBlur={blur("host")}
        />
      </Field>
      <Field label={t("runtime.health.portLabel")} description={t("runtime.health.portDescription")} error={errorOf("port")}>
        <Input {...numeric(t("runtime.health.portPlaceholder"))} value={draft.port} onValueChange={edit("port")} onBlur={blur("port")} />
      </Field>
    </div>
  );
}

/** The command a command check runs, and the service of a stack it runs in. */
function CommandOptions({ extras, stack }: { extras: ExtrasPart; stack: boolean }) {
  const t = useT();
  const { draft, edit, blur, errorOf } = extras;
  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
      <Field
        className={stack ? "sm:col-span-2" : "sm:col-span-3"}
        label={t("runtime.health.commandLabel")}
        description={t("runtime.health.commandDescription")}
        error={errorOf("command")}
      >
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder={t("runtime.health.commandPlaceholder")}
          value={draft.command}
          onValueChange={edit("command")}
          onBlur={blur("command")}
        />
      </Field>
      {stack ? (
        <Field label={t("runtime.health.serviceLabel")} description={t("runtime.health.serviceDescription")} error={errorOf("service")}>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="web" value={draft.service} onValueChange={edit("service")} onBlur={blur("service")} />
        </Field>
      ) : null}
    </div>
  );
}

/** How often it asks, how many times, and how long a failure does not count. */
function TimingOptions({ extras }: { extras: ExtrasPart }) {
  const t = useT();
  const { draft, edit, blur, errorOf } = extras;
  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
      <Field
        label={t("runtime.health.intervalLabel")}
        description={t("runtime.health.intervalDescription", { min: INTERVAL_MIN, max: INTERVAL_MAX, default: EXTRAS_DEFAULTS.interval })}
        error={errorOf("interval")}
      >
        <Input {...numeric(EXTRAS_DEFAULTS.interval)} suffix="s" value={draft.interval} onValueChange={edit("interval")} onBlur={blur("interval")} />
      </Field>
      <Field
        label={t("runtime.health.retriesLabel")}
        description={t("runtime.health.retriesDescription", { min: RETRIES_MIN, max: RETRIES_MAX })}
        error={errorOf("retries")}
      >
        <Input {...numeric("")} value={draft.retries} onValueChange={edit("retries")} onBlur={blur("retries")} />
      </Field>
      <Field
        label={t("runtime.health.startPeriodLabel")}
        description={t("runtime.health.startPeriodDescription", { min: START_PERIOD_MIN, max: START_PERIOD_MAX, default: EXTRAS_DEFAULTS.startPeriod })}
        error={errorOf("startPeriod")}
      >
        <Input {...numeric(EXTRAS_DEFAULTS.startPeriod)} suffix="s" value={draft.startPeriod} onValueChange={edit("startPeriod")} onBlur={blur("startPeriod")} />
      </Field>
    </div>
  );
}

/** The startup check's fields, saved with the rest of the subsection from its save bar. */
export function StartupCheckCard({ app, check }: { app: App; check: StartupCheckPart }) {
  const t = useT();
  const { draft, edit, blur, errorOf, extras } = check;
  const kind = extras.draft.kind;
  return (
    <Card
      title={t("appSettings.healthCheck.title")}
      description={t("appSettings.healthCheck.description")}
      actions={
        <Button size="sm" variant="ghost" disabled={sameHealth(draft, EMPTY) && !hasExtras(extras.draft)} onClick={check.toDefaults}>
          {t("appSettings.healthCheck.useDefaults")}
        </Button>
      }
    >
      <div className="flex flex-col gap-5">
        {extras.loading ? (
          <LoadingRegion label={t("runtime.health.loading")} className="flex flex-col gap-3">
            <Skeleton className="h-6 w-56" />
            <Skeleton className="h-16 w-full" />
          </LoadingRegion>
        ) : extras.failed !== null ? (
          <ErrorBlock compact error={extras.failed} title={t("runtime.health.readFailed")} />
        ) : (
          <SegmentedControl<HealthKind>
            label={t("runtime.health.kindLabel")}
            value={kind}
            onValueChange={extras.setKind}
            options={[
              { value: "http", label: t("runtime.health.kindRequest") },
              { value: "command", label: t("runtime.health.kindCommand") },
            ]}
          />
        )}
        {kind === "http" ? (
          /* On a phone the two short fields share a row under the path. */
          <div className="grid grid-cols-2 gap-5 sm:grid-cols-3">
            <Field
              className="col-span-2 sm:col-span-1"
              label={t("appSettings.healthCheck.pathLabel")}
              description={t("appSettings.healthCheck.pathDescription", { default: HEALTH_DEFAULTS.path })}
              error={errorOf("path")}
            >
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder={HEALTH_DEFAULTS.path}
                value={draft.path}
                onValueChange={edit("path")}
                onBlur={blur("path")}
              />
            </Field>
            <Field label={t("appSettings.healthCheck.expectLabel")} description={t("appSettings.healthCheck.expectDescription")} error={errorOf("expect")}>
              <Input
                mono
                autoComplete="off"
                placeholder={t("appSettings.healthCheck.expectPlaceholder")}
                value={draft.expect}
                onValueChange={edit("expect")}
                onBlur={blur("expect")}
              />
            </Field>
            <TimeoutField check={check} />
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            <CommandOptions extras={extras} stack={app.app_type === COMPOSE} />
            <div className="grid grid-cols-2 gap-5 sm:grid-cols-3">
              <TimeoutField check={check} />
            </div>
          </div>
        )}
        {/* The options open for an application that already uses them, which is known once the check is read. */}
        {extras.loading ? null : (
          <>
            {kind === "http" ? (
              <Disclosure
                label={t("runtime.health.requestOptions")}
                defaultOpen={extras.draft.method !== "" || extras.draft.scheme !== "" || extras.draft.host !== "" || extras.draft.port !== ""}
              >
                <RequestOptions extras={extras} />
              </Disclosure>
            ) : null}
            <Disclosure
              label={t("runtime.health.timingOptions")}
              defaultOpen={extras.draft.interval !== "" || extras.draft.retries !== "" || extras.draft.startPeriod !== ""}
            >
              <TimingOptions extras={extras} />
            </Disclosure>
          </>
        )}
        {check.unplaced !== null ? <ErrorBlock live compact error={check.unplaced} title={t("appSettings.healthCheck.saveFailed")} /> : null}
        <Effective app={app} extras={extras.draft} />
      </div>
    </Card>
  );
}

/** How long the check waits, which both kinds of check have. */
function TimeoutField({ check }: { check: StartupCheckPart }) {
  const t = useT();
  const { draft, edit, blur, errorOf } = check;
  return (
    <Field
      label={t("appSettings.healthCheck.timeoutLabel")}
      description={t("appSettings.healthCheck.timeoutDescription", { min: HEALTH_TIMEOUT_MIN, max: HEALTH_TIMEOUT_MAX, default: HEALTH_DEFAULTS.timeout })}
      error={errorOf("timeout")}
    >
      <Input
        mono
        inputMode="numeric"
        autoComplete="off"
        placeholder={String(HEALTH_DEFAULTS.timeout)}
        suffix="s"
        value={draft.timeout}
        onValueChange={edit("timeout")}
        onBlur={blur("timeout")}
      />
    </Field>
  );
}

/** A static site's check is its files: there is nothing to configure. */
export function StaticStartupNote() {
  const t = useT();
  return (
    <Card title={t("appSettings.healthCheck.title")}>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("appSettings.healthCheck.staticNote")}</p>
    </Card>
  );
}
