import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../../api/client";
import type { Job } from "../../../api/queries/jobs";
import { appKeys } from "../../../api/queries/apps";
import { runtimeKeys } from "../../../api/queries/runtime";
import type { UnitTuning } from "../../../api/queries/runtime";
import { systemInfoQuery } from "../../../api/queries/system";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { PlainKey, T } from "../../../i18n";
import { changedIn, draftOf, parseTuning, presetLines, requestOptions, withLines, FIELD_OF } from "./tuning";
import type { TuningDraft, TuningErrors, TuningGroup, TuningOptions } from "./tuning";
import { JobFailedError, waitForJob } from "./queries";
import type { FormPart } from "./formParts";

/** What each capability the server offers is for, in the console's words. */
const CAPABILITY_WORDS: Readonly<Record<string, PlainKey>> = {
  NET_BIND_SERVICE: "runtime.tuning.capability.NET_BIND_SERVICE",
  NET_RAW: "runtime.tuning.capability.NET_RAW",
  NET_BROADCAST: "runtime.tuning.capability.NET_BROADCAST",
  IPC_LOCK: "runtime.tuning.capability.IPC_LOCK",
  SYS_NICE: "runtime.tuning.capability.SYS_NICE",
  SYS_RESOURCE: "runtime.tuning.capability.SYS_RESOURCE",
  SYS_CHROOT: "runtime.tuning.capability.SYS_CHROOT",
};

export interface TuningPart {
  /** For the subsection's save bar; null while the unit cannot be tuned or is not read yet. */
  part: FormPart | null;
  draft: TuningDraft;
  edit: <K extends keyof TuningDraft>(field: K, value: TuningDraft[K]) => void;
  /** An error to show beside a field: the form's own once submitted, or the server's refusal. */
  errorOf: (field: keyof TuningDraft) => string | undefined;
  cores: number | null;
  /** The refusal of the server that no field of this half owns. */
  failure: unknown;
}

/**
 * One half of the tuning form: its draft, its checks, and how it is sent.
 *
 * The request carries the whole tuning, so the other half is sent as the server has it and a
 * save of one never undoes the other. The change is a job (the unit is rewritten and the
 * application restarts behind the startup check), which is waited for.
 *
 * @param tuning What the server says now; undefined while it is being read.
 * @param restart Whether to restart now, so the application runs under it.
 */
export function useTuningPart(domain: string, tuning: UnitTuning | undefined, group: TuningGroup, restart: boolean): TuningPart {
  const t = useT();
  const queryClient = useQueryClient();
  const info = useQuery({ ...systemInfoQuery(), staleTime: 10 * 60_000, refetchOnWindowFocus: false });
  const cores = info.data?.cpu.cores ?? tuning?.cpus ?? null;

  const stored = draftOf(tuning?.options);
  const [baseline, setBaseline] = useState(stored);
  const [draft, setDraft] = useState(stored);
  const [submitted, setSubmitted] = useState(false);

  // The tuning changed underneath an untouched form (saved from a terminal): follow it.
  if (changedIn(group, stored, baseline) > 0) {
    const untouched = changedIn(group, draft, baseline) === 0;
    setBaseline(stored);
    if (untouched) setDraft(stored);
  }

  const parsed = parseTuning(group, draft, cores, t.locale);
  const save = useMutation({
    // The restart is the one asked when Save was pressed: another part of the subsection may reset
    // its checkbox between the parts being saved.
    mutationFn: async ({ values, now }: { values: TuningOptions; now: boolean }) => {
      const queued = await request("put", "/api/apps/{domain}/tuning", {
        params: { domain },
        body: { options: requestOptions(tuning?.options as TuningOptions | undefined, group, values), restart: now },
      });
      const job = await waitForJob(queryClient, queued.job as Job);
      void queryClient.invalidateQueries({ queryKey: runtimeKeys.tuning(domain) });
      void queryClient.invalidateQueries({ queryKey: runtimeKeys.pending(domain) });
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
      if (job.status !== "completed") throw new JobFailedError(job.error ?? t("runtime.tuning.jobFailed"));
    },
    onSuccess: () => {
      setSubmitted(false);
      announce(t("runtime.tuning.saved", { domain }));
    },
  });

  const serverFields: TuningErrors = {};
  if (isApiError(save.error) && save.error.fields) {
    for (const [name, message] of Object.entries(save.error.fields)) {
      const field = FIELD_OF[name];
      if (field !== undefined) serverFields[field] = message;
    }
  }
  const edit: TuningPart["edit"] = (field, value) => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    if (save.isError) save.reset();
  };

  const part: FormPart | null =
    !tuning?.eligible
      ? null
      : {
          changes: changedIn(group, draft, stored),
          elevated: true,
          check: () => {
            setSubmitted(true);
            return Object.keys(parsed.errors).length === 0;
          },
          save: async () => {
            await save.mutateAsync({ values: parsed.values, now: restart });
          },
          discard: () => {
            setDraft(stored);
            setSubmitted(false);
            save.reset();
          },
        };

  return {
    part,
    draft,
    edit,
    errorOf: (field) => (submitted ? parsed.errors[field] : undefined) ?? serverFields[field],
    cores,
    failure: save.isError && Object.keys(serverFields).length === 0 ? save.error : undefined,
  };
}

function ShowFailure({ failure, domain }: { failure: unknown; domain: string }) {
  const t = useT();
  return failure === undefined ? null : <ErrorBlock live compact error={failure} title={t("runtime.tuning.saveFailed", { domain })} />;
}

/** The lines the tuning adds to the unit, put away until they are asked for. */
function Lines({ tuning, t }: { tuning: UnitTuning; t: T }) {
  const lines = tuning.directives ?? [];
  return (
    <Disclosure label={t("runtime.tuning.directivesTitle")}>
      {lines.length === 0 ? (
        <p className="text-13 text-fg-muted">{t("runtime.tuning.noDirectives")}</p>
      ) : (
        <SystemOutput label={t("runtime.tuning.directivesTitle")} maxHeight="max-h-64">
          {lines.join("\n")}
        </SystemOutput>
      )}
    </Disclosure>
  );
}

/**
 * The limits beyond the hard ones, under the application's resources: the soft memory limit and
 * swap, the CPU and disk shares, the CPUs it is pinned to and its ulimits. Folded away, because
 * most applications need none of them, and open when one is already set.
 */
export function AdvancedLimitsFields({ domain, tuning, state }: { domain: string; tuning: UnitTuning; state: TuningPart }) {
  const t = useT();
  const { draft, edit, errorOf } = state;
  const numeric = (field: keyof TuningDraft, label: string, description: string, suffix?: string) => (
    <Field label={label} description={description} error={errorOf(field)}>
      <Input
        mono
        inputMode="numeric"
        autoComplete="off"
        {...(suffix !== undefined ? { suffix } : {})}
        value={draft[field]}
        onValueChange={(value) => {
          edit(field, value as never);
        }}
      />
    </Field>
  );
  const anySet = Object.keys(tuning.options ?? {}).some((key) => !["capabilities", "devices"].includes(key));
  return (
    <Disclosure label={t("runtime.tuning.limitsTitle")} defaultOpen={anySet}>
      <div className="flex flex-col gap-5">
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("runtime.tuning.limitsIntro")}</p>
        <div className="grid gap-5 sm:grid-cols-3">
          {numeric("memoryHigh", t("runtime.tuning.memoryHighLabel"), t("runtime.tuning.memoryHighDescription"), "MB")}
          {numeric("swapMax", t("runtime.tuning.swapMaxLabel"), t("runtime.tuning.swapMaxDescription"), "MB")}
          {numeric("cpuWeight", t("runtime.tuning.cpuWeightLabel"), t("runtime.tuning.cpuWeightDescription"))}
          <Field
            label={t("runtime.tuning.allowedCpusLabel")}
            description={
              state.cores === null
                ? t("runtime.tuning.allowedCpusDescriptionUnknown")
                : t("runtime.tuning.allowedCpusDescriptionKnown", { cores: state.cores })
            }
            error={errorOf("allowedCpus")}
          >
            <Input
              mono
              autoComplete="off"
              spellCheck={false}
              value={draft.allowedCpus}
              onValueChange={(value) => {
                edit("allowedCpus", value);
              }}
            />
          </Field>
          {numeric("ioWeight", t("runtime.tuning.ioWeightLabel"), t("runtime.tuning.ioWeightDescription"))}
          {numeric("nofile", t("runtime.tuning.nofileLabel"), t("runtime.tuning.nofileDescription"))}
          {numeric("nproc", t("runtime.tuning.nprocLabel"), t("runtime.tuning.nprocDescription"))}
          <Field label={t("runtime.tuning.memlockLabel")} description={t("runtime.tuning.memlockDescription")} error={errorOf("memlock")}>
            <Input
              mono
              autoComplete="off"
              suffix="MB"
              value={draft.memlock}
              onValueChange={(value) => {
                edit("memlock", value);
              }}
            />
          </Field>
          <Field label={t("runtime.tuning.coreLabel")}>
            <Select<"default" | "allow" | "forbid">
              options={[
                { value: "default", label: t("runtime.tuning.coreDefault") },
                { value: "allow", label: t("runtime.tuning.coreAllow") },
                { value: "forbid", label: t("runtime.tuning.coreForbid") },
              ]}
              value={draft.core === "" ? "default" : draft.core}
              onValueChange={(value) => {
                edit("core", value === "default" ? "" : value);
              }}
            />
          </Field>
        </div>
        <ShowFailure failure={state.failure} domain={domain} />
        <Lines tuning={tuning} t={t} />
      </div>
    </Disclosure>
  );
}

/**
 * The capabilities the process keeps and the devices it may open, with the unit's security: the
 * sandbox takes both away, and these give back exactly what is named.
 */
export function PrivilegeFields({ domain, tuning, state }: { domain: string; tuning: UnitTuning; state: TuningPart }) {
  const t = useT();
  const { draft, edit, errorOf } = state;
  const offered = Object.keys(tuning.capabilities ?? {});
  const described = (name: string): string => {
    const key = CAPABILITY_WORDS[name];
    return key === undefined ? (tuning.capabilities?.[name] ?? "") : t(key);
  };
  const presets = tuning.gpu_presets ?? {};
  const vendorWords = (vendor: string): string => (vendor === "nvidia" ? t("runtime.tuning.gpuNvidia") : vendor === "dri" ? t("runtime.tuning.gpuDri") : vendor);
  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("runtime.tuning.privilegesIntro")}</p>
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1 text-13 font-medium text-fg">{t("runtime.tuning.capabilitiesLegend")}</legend>
        <p className="max-w-measure text-12 text-fg-muted">{t("runtime.tuning.capabilitiesDescription")}</p>
        {offered.map((name) => (
          <Checkbox
            key={name}
            label={<Mono>{`CAP_${name}`}</Mono>}
            description={described(name)}
            checked={draft.capabilities.includes(name)}
            onCheckedChange={(checked) => {
              edit("capabilities", checked ? [...draft.capabilities, name] : draft.capabilities.filter((known) => known !== name));
            }}
          />
        ))}
      </fieldset>
      <Field label={t("runtime.tuning.devicesLabel")} description={t("runtime.tuning.devicesDescription")} error={errorOf("devices")}>
        <Textarea
          mono
          rows={3}
          autoComplete="off"
          spellCheck={false}
          placeholder={t("runtime.tuning.devicesPlaceholder")}
          value={draft.devices}
          onChange={(event) => {
            edit("devices", event.target.value);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        {Object.entries(presets).map(([vendor, groups]) => (
          <Button
            key={vendor}
            size="sm"
            variant="ghost"
            onClick={() => {
              edit("devices", withLines(draft.devices, presetLines(groups)));
            }}
          >
            {t("runtime.tuning.gpuAdd", { vendor: vendorWords(vendor) })}
          </Button>
        ))}
      </div>
      <ShowFailure failure={state.failure} domain={domain} />
      <Lines tuning={tuning} t={t} />
    </div>
  );
}
