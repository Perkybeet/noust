import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { changedFields } from "./formParts";
import type { FormPart } from "./formParts";
import { HEALTH_DEFAULTS, HEALTH_TIMEOUT_MAX, HEALTH_TIMEOUT_MIN, effectiveHealth, healthDraftOf, healthFieldOf, parseHealth, sameHealth } from "./healthCheck";
import type { HealthDraft, HealthErrors, HealthValues } from "./healthCheck";

const EMPTY: HealthDraft = { path: "", expect: "", timeout: "" };

export interface StartupCheckPart {
  part: FormPart;
  draft: HealthDraft;
  edit: (field: keyof HealthDraft) => (value: string) => void;
  blur: (field: keyof HealthDraft) => () => void;
  errorOf: (field: keyof HealthDraft) => string | undefined;
  /** A refusal no field is about, shown whole above the save bar's reach. */
  unplaced: unknown;
  /** Empties every field: the defaults, once saved. */
  toDefaults: () => void;
}

/**
 * The startup check as a part of the Deploys form: the path the gate requests on 127.0.0.1,
 * the statuses that pass and how long it waits, before a new version takes the traffic. An
 * empty field is the default. The rules are checked here for a quick answer and again,
 * authoritatively, where the settings are stored; a refusal lands on the field it is about.
 * Nothing restarts: the next activation uses them.
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

  // The app changed underneath an untouched form (saved here, or from a terminal): follow it.
  if (!sameHealth(current, baseline)) {
    setBaseline(current);
    if (sameHealth(draft, baseline)) setDraft(current);
  }

  const parsed = parseHealth(draft, t.locale);

  const save = useMutation({
    mutationFn: (values: HealthValues) => request("patch", "/api/apps/{domain}/health", { params: { domain }, body: values }),
    onSuccess: (result) => {
      // What was saved is what the form now says, whatever the app's detail says next.
      const saved = healthDraftOf({ health_path: result.path ?? null, health_expect: result.expect ?? null, health_timeout: result.timeout ?? null });
      setDraft(saved);
      setBaseline(saved);
      setSubmitted(false);
      setTouched({});
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) =>
        known ? { ...known, health_path: result.path ?? null, health_expect: result.expect ?? null, health_timeout: result.timeout ?? null } : known,
      );
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
    },
  });

  const serverFields: HealthErrors = {};
  let unplaced: unknown = null;
  if (save.isError && isApiError(save.error)) {
    if (save.error.fields) {
      for (const [name, message] of Object.entries(save.error.fields)) {
        if (name === "path" || name === "expect" || name === "timeout") serverFields[name] = message;
      }
    } else {
      const field = healthFieldOf(save.error.detail);
      if (field !== null) serverFields[field] = save.error.hint ? `${save.error.detail}. ${save.error.hint}` : save.error.detail;
    }
    if (Object.keys(serverFields).length === 0) unplaced = save.error;
  } else if (save.isError) {
    unplaced = save.error;
  }

  return {
    part: {
      changes: changedFields(draft, current),
      elevated: true,
      check: () => {
        setSubmitted(true);
        return Object.keys(parsed.errors).length === 0;
      },
      save: async () => {
        await save.mutateAsync(parsed.values);
      },
      discard: () => {
        setDraft(current);
        setTouched({});
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
    unplaced,
    toDefaults: () => {
      setDraft(EMPTY);
      save.reset();
    },
  };
}

/** What the gate asks now, each value marked when it is the default. */
function Effective({ app }: { app: App }) {
  const t = useT();
  const now = effectiveHealth(app, t.locale);
  const mark = (isDefault: boolean) => (isDefault ? <span className="text-fg-faint"> ({t("appSettings.healthCheck.default")})</span> : null);
  return (
    <p className="text-12 text-pretty text-fg-muted">
      {t.rich("appSettings.healthCheck.now", {
        path: (
          <>
            <Mono>{`GET ${now.path}`}</Mono>
            {mark(now.defaults.path)}
          </>
        ),
        expect: (
          <>
            <span className="text-fg">{now.expect}</span>
            {mark(now.defaults.expect)}
          </>
        ),
        timeout: (
          <>
            <Mono>{`${String(now.timeout)}s`}</Mono>
            {mark(now.defaults.timeout)}
          </>
        ),
      })}
    </p>
  );
}

/** The startup check's fields, saved with the rest of the subsection from its save bar. */
export function StartupCheckCard({ app, check }: { app: App; check: StartupCheckPart }) {
  const t = useT();
  const { draft, edit, blur, errorOf } = check;
  return (
    <Card
      title={t("appSettings.healthCheck.title")}
      description={t("appSettings.healthCheck.description")}
      actions={
        <Button size="sm" variant="ghost" disabled={sameHealth(draft, EMPTY)} onClick={check.toDefaults}>
          {t("appSettings.healthCheck.useDefaults")}
        </Button>
      }
    >
      <div className="flex flex-col gap-5">
        {/* On a phone the two short fields share a row under the path. */}
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
        </div>
        {check.unplaced !== null ? <ErrorBlock live compact error={check.unplaced} title={t("appSettings.healthCheck.saveFailed")} /> : null}
        <Effective app={app} />
      </div>
    </Card>
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
