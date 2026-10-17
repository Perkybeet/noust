import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { Job } from "../../../api/queries/jobs";
import { hardeningQuery, runtimeKeys, tuningQuery } from "../../../api/queries/runtime";
import type { Hardening, UnitTuning } from "../../../api/queries/runtime";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Subsection } from "../../../components/page/Subsection";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { changedFields, useSaveBar } from "./formParts";
import { JobFailedError, waitForJob } from "./queries";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";
import { PrivilegeFields, useTuningPart } from "./TuningFields";

type Profile = "off" | "default" | "strict";

interface HardeningDraft {
  profile: Profile;
  paths: string;
}

function profileOf(value: string): Profile {
  return value === "default" || value === "strict" ? value : "off";
}

function draftOf(hardening: Hardening): HardeningDraft {
  return { profile: profileOf(hardening.profile), paths: (hardening.writable_paths ?? []).join("\n") };
}

/** One absolute path per non-blank line. */
function pathsOf(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

function profileWords(t: T, profile: Profile): string {
  if (profile === "strict") return t("runtime.hardening.profileStrict");
  if (profile === "default") return t("runtime.hardening.profileDefault");
  return t("runtime.hardening.profileOff");
}

/** The score systemd gives the unit, in its own word, or why there is none. */
function Exposure({ hardening }: { hardening: Hardening }) {
  const t = useT();
  const score = hardening.exposure ?? null;
  return (
    <Subsection title={t("runtime.hardening.exposureTitle")}>
      {score === null ? (
        <p className="text-13 text-fg-muted">{t("runtime.hardening.exposureUnknown")}</p>
      ) : (
        <p className="text-13 text-fg">
          <Mono>{t("runtime.hardening.exposureValue", { score: score.toFixed(1), label: hardening.exposure_label ?? "" })}</Mono>
        </p>
      )}
      <p className="text-12 text-fg-muted">{t("runtime.hardening.exposureHint")}</p>
    </Subsection>
  );
}

function HardeningForm({ domain, hardening, tuning, reading }: { domain: string; hardening: Hardening; tuning: UnitTuning | undefined; reading: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const current = draftOf(hardening);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [restart, setRestart] = useState(true);
  const [submitted, setSubmitted] = useState(false);
  const privileges = useTuningPart(domain, tuning, "privileges", restart);

  // The sandbox changed underneath an untouched form (from a terminal): follow it.
  if (current.profile !== baseline.profile || current.paths !== baseline.paths) {
    setBaseline(current);
    if (draft.profile === baseline.profile && draft.paths === baseline.paths) setDraft(current);
  }

  const save = useMutation({
    mutationFn: async (values: HardeningDraft) => {
      const queued = await request("put", "/api/apps/{domain}/hardening", {
        params: { domain },
        body: { profile: values.profile, writable_paths: pathsOf(values.paths), restart },
      });
      const job = await waitForJob(queryClient, queued.job as Job);
      void queryClient.invalidateQueries({ queryKey: runtimeKeys.hardening(domain) });
      void queryClient.invalidateQueries({ queryKey: runtimeKeys.pending(domain) });
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
      if (job.status !== "completed") throw new JobFailedError(job.error ?? t("runtime.hardening.jobFailed"));
    },
    onSuccess: (_result, values) => {
      setSubmitted(false);
      toast.success(t("runtime.hardening.saved", { domain, profile: profileWords(t, values.profile) }));
    },
  });

  const writable = pathsOf(draft.paths);
  const bad = writable.find((path) => !path.startsWith("/") || path.includes("\0") || path.length > 1024);
  const pathError = bad === undefined ? undefined : `${bad}: ${t("runtime.hardening.writableDescription")}`;
  const serverPath = isApiError(save.error) ? (save.error.fields?.["writable_paths"] ?? save.error.fields?.["paths"]) : undefined;

  const bar = useSaveBar(
    [
      {
        changes: changedFields(draft, current),
        elevated: true,
        check: () => {
          setSubmitted(true);
          return bad === undefined;
        },
        save: async () => {
          await save.mutateAsync(draft);
        },
        discard: () => {
          setDraft(current);
          setSubmitted(false);
          save.reset();
        },
      },
      privileges.part,
    ],
    t("runtime.hardening.notSavedFor", { domain }),
  );

  return (
    <>
      <Card>
        <div className="flex flex-col gap-5">
          {!hardening.configured ? <p className="text-13 text-fg-muted">{t("runtime.hardening.notConfigured")}</p> : null}
          <ChoiceCards<Profile>
            legend={t("runtime.hardening.profileLabel")}
            value={draft.profile}
            onValueChange={(profile) => {
              setDraft((previous) => ({ ...previous, profile }));
              if (save.isError) save.reset();
            }}
            options={[
              { value: "off", label: t("runtime.hardening.profileOff"), description: t("runtime.hardening.profileOffDescription") },
              { value: "default", label: t("runtime.hardening.profileDefault"), description: t("runtime.hardening.profileDefaultDescription") },
              { value: "strict", label: t("runtime.hardening.profileStrict"), description: t("runtime.hardening.profileStrictDescription") },
            ]}
          />
          <Field
            label={t("runtime.hardening.writableLabel")}
            description={t("runtime.hardening.writableDescription")}
            error={(submitted ? pathError : undefined) ?? serverPath}
          >
            <Textarea
              mono
              rows={3}
              autoComplete="off"
              placeholder={t("runtime.hardening.writablePlaceholder")}
              value={draft.paths}
              onChange={(event) => {
                setDraft((previous) => ({ ...previous, paths: event.target.value }));
                if (save.isError) save.reset();
              }}
            />
          </Field>
          {tuning?.eligible ? (
            <Subsection title={t("runtime.tuning.privilegesTitle")}>
              <PrivilegeFields domain={domain} tuning={tuning} state={privileges} />
            </Subsection>
          ) : reading ? (
            // The place the section will take, so its arrival moves nothing.
            <div aria-busy="true">
              <span className="sr-only">{t("runtime.tuning.loading")}</span>
              <Skeleton className="h-40 w-full" />
            </div>
          ) : null}
          <Checkbox label={t("runtime.hardening.restartLabel")} description={t("runtime.hardening.restartDescription")} checked={restart} onCheckedChange={setRestart} />
          {save.isError && !serverPath ? <ErrorBlock live compact error={save.error} title={t("runtime.hardening.saveFailed", { domain })} /> : null}
          <Exposure hardening={hardening} />
          <Disclosure label={t("runtime.hardening.directivesTitle")}>
            {(hardening.directives ?? []).length === 0 ? (
              <p className="text-13 text-fg-muted">{t("runtime.hardening.noDirectives")}</p>
            ) : (
              <SystemOutput label={t("runtime.hardening.directivesTitle")} maxHeight="max-h-64">
                {(hardening.directives ?? []).join("\n")}
              </SystemOutput>
            )}
          </Disclosure>
        </div>
      </Card>
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
    </>
  );
}

/** Unit security: how much of systemd's sandbox the application's process runs in. */
export function SecuritySettings() {
  const t = useT();
  const app = useSettingsApp();
  useSubsectionTitle("runtime.nav.security", app.domain);
  const hardening = useQuery(hardeningQuery(app.domain));
  // Read with the sandbox, so the form is made once with what the unit has; a read that failed
  // is the sandbox alone.
  const tuning = useQuery(tuningQuery(app.domain));
  const data = hardening.data;
  return (
    <Section title={t("runtime.hardening.title")} description={t("runtime.hardening.description")}>
      {data === undefined ? (
        hardening.isError ? (
          <ErrorBlock error={hardening.error} title={t("runtime.hardening.loadFailed")} onRetry={() => void hardening.refetch()} retrying={hardening.isRefetching} />
        ) : (
          <div aria-busy="true" className="flex flex-col gap-3">
            <span className="sr-only">{t("runtime.hardening.loading")}</span>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        )
      ) : !data.eligible ? (
        <Notice tone="info">{t("runtime.hardening.cannot", { reason: data.reason ?? "" })}</Notice>
      ) : (
        <HardeningForm domain={app.domain} hardening={data} tuning={tuning.data} reading={tuning.isPending} />
      )}
    </Section>
  );
}
