import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { assistantKeys, assistantPresetsQuery, saveAssistantSettings } from "../../../api/queries/assistant";
import type { AssistantSettings } from "../../../api/queries/assistant";
import { rolesQuery } from "../../../api/queries/roles";
import { sessionQuery } from "../../../api/queries/auth";
import { useDocumentTitle } from "../../../app/documentTitle";
import { useCan } from "../../../components/page/permissions";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Stepper } from "../../../components/page/Stepper";
import type { WizardStep } from "../../../components/page/Stepper";
import { Subsection } from "../../../components/page/Subsection";
import { WizardActions } from "../../../components/page/Wizard";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { Select } from "../../../components/ui/Select";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { TextLink } from "../../../components/ui/TextLink";
import { Textarea } from "../../../components/ui/Textarea";
import { locationWord } from "../../../components/assistant/AiMark";
import { useT } from "../../../i18n";
import { LimitsFields } from "./LimitsFields";
import { OTHER_ADDRESS, draftFrom, isOwn, limitsBody, modelBody, modelMissing } from "./model";
import type { Draft, Origin } from "./model";
import { ProbeResult } from "./ProbeResult";
import type { Step } from "./search";
import { STEPS } from "./search";

const ORIGIN_ICONS = { here: OBJECT_ICONS.server, network: OBJECT_ICONS.network, service: OBJECT_ICONS.cloud } as const;

export interface TurnOnWizardProps {
  settings: AssistantSettings;
  step: Step;
  onStep: (step: Step | undefined) => void;
}

/** The fields of the model step that decide what was tested: when one changes after the test, the result no longer speaks of it. */
function tested(draft: Draft): string {
  return JSON.stringify([draft.baseUrl.trim(), draft.model.trim(), draft.apiKey, draft.preset, draft.origin]);
}

function ModelStep({ draft, onDraft, settings, onTested }: { draft: Draft; onDraft: (next: Draft) => void; settings: AssistantSettings; onTested: (draft: Draft) => void }) {
  const t = useT();
  const presets = useQuery(assistantPresetsQuery());
  const queryClient = useQueryClient();
  const session = useQuery(sessionQuery());
  const ens = session.data?.security_profile === "ens-medium";
  const own = isOwn(draft.origin);
  const preset = presets.data?.presets.find((candidate) => candidate.id === draft.preset);
  const testing = useMutation({
    mutationFn: () => saveAssistantSettings(modelBody(draft, preset)),
    onSuccess: (saved) => {
      queryClient.setQueryData(assistantKeys.settings, saved);
      onTested(draft);
    },
  });
  const can = useCan();
  const missing = modelMissing(draft);
  const probeModels = settings.probe?.models ?? [];

  const originCard = (origin: Origin) => ({
    value: origin,
    label: t(`assistantSettings.model.${origin}.label`),
    description: t(`assistantSettings.model.${origin}.description`),
    detail: t(`assistantSettings.model.${origin}.detail`),
    icon: ORIGIN_ICONS[origin],
  });
  const chooseOrigin = (origin: Origin): void => {
    // A short-list service has its own address; the others are typed, starting from where such servers usually listen.
    onDraft({ ...draft, origin, preset: OTHER_ADDRESS, baseUrl: origin === "here" ? "http://localhost:11434/v1" : origin === "network" ? "" : "", model: "", allowPrivate: false, mask: origin === "service" ? true : draft.mask });
  };
  return (
    <div className="flex flex-col gap-5">
      <ChoiceCards<Origin> legend={t("assistantSettings.model.origin")} columns={1} value={draft.origin} onValueChange={chooseOrigin} options={(["here", "network", "service"] as const).map(originCard)} />
      {draft.origin === "service" ? (
        <Field label={t("assistantSettings.model.serviceField")} description={t("assistantSettings.model.serviceHelp")}>
          <Select
            value={draft.preset}
            options={[...(presets.data?.presets ?? []).filter((candidate) => candidate.location !== "machine").map((candidate) => ({ value: candidate.id, label: candidate.name })), { value: OTHER_ADDRESS, label: t("assistantSettings.model.otherAddress") }]}
            onValueChange={(id) => {
              const chosen = presets.data?.presets.find((candidate) => candidate.id === id);
              onDraft({ ...draft, preset: id, baseUrl: chosen?.base_url ?? "", model: "" });
            }}
          />
        </Field>
      ) : null}
      <Field label={t("assistantSettings.model.address")} description={t("assistantSettings.model.addressHelp")}>
        <Input mono value={draft.baseUrl} placeholder="https://api.example.org/v1" onChange={(event) => onDraft({ ...draft, baseUrl: event.target.value, preset: OTHER_ADDRESS })} />
      </Field>
      {own ? (
        <Checkbox label={t("assistantSettings.model.allowPrivate")} description={t("assistantSettings.model.allowPrivateHelp")} checked={draft.allowPrivate} onCheckedChange={(checked) => onDraft({ ...draft, allowPrivate: checked })} />
      ) : (
        <Field label={t("assistantSettings.model.key")} description={settings.has_key && draft.apiKey === "" ? t("assistantSettings.model.keySaved") : t("assistantSettings.model.keyHelp")} optional={preset?.needs_key === false}>
          <Input mono type="password" autoComplete="off" spellCheck={false} value={draft.apiKey} onChange={(event) => onDraft({ ...draft, apiKey: event.target.value })} />
        </Field>
      )}
      <Field label={t("assistantSettings.model.model")} description={t("assistantSettings.model.modelHelp")}>
        <Input mono value={draft.model} onChange={(event) => onDraft({ ...draft, model: event.target.value })} />
      </Field>
      {probeModels.length > 0 && tested(draft) === tested(draftFrom(settings)) ? (
        <Field label={t("assistantSettings.model.pickModel")} optional>
          <Select value={probeModels.includes(draft.model) ? draft.model : null} options={probeModels.map((name) => ({ value: name, label: name }))} onValueChange={(name) => onDraft({ ...draft, model: name })} mono />
        </Field>
      ) : null}
      {draft.origin === "service" && ens ? (
        <Notice tone="warning" title={t("assistantSettings.model.flowTitle")}>
          {t("assistantSettings.model.flowBody")}
        </Notice>
      ) : null}
      {draft.origin === "service" ? (
        <Disclosure label={t("assistantSettings.model.locationLabel")} defaultOpen={ens}>
          <div className="flex flex-col gap-5">
            <Field label={t("assistantSettings.model.locationLabel")} description={t("assistantSettings.model.locationHelp")} optional>
              <Select
                value={draft.location}
                options={[{ value: "", label: locationWord(t, "unknown") }, ...(presets.data?.locations ?? []).filter((place) => place === "eu" || place === "outside").map((place) => ({ value: place, label: locationWord(t, place) }))]}
                onValueChange={(place) => onDraft({ ...draft, location: place })}
              />
            </Field>
            <Field label={t("assistantSettings.model.flowReason")} description={t("assistantSettings.model.flowReasonHelp")} optional={!ens}>
              <Textarea value={draft.flowReason} rows={2} onChange={(event) => onDraft({ ...draft, flowReason: event.target.value })} />
            </Field>
          </div>
        </Disclosure>
      ) : null}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" icon={<VERB_ICONS.test aria-hidden="true" />} loading={testing.isPending} onClick={() => (missing === null && can("settings.manage") ? testing.mutate() : undefined)} aria-disabled={missing !== null || undefined}>
            {t("assistantSettings.model.test")}
          </Button>
          <p className="min-w-0 flex-1 text-13 text-pretty text-fg-muted">{missing !== null ? t("assistantSettings.model.missingAddress") : t("assistantSettings.model.testHelp")}</p>
        </div>
        {testing.isError ? <ErrorBlock compact live error={testing.error} title={t("assistantSettings.model.testFailed")} /> : null}
      </div>
      {settings.probe !== null && settings.probe !== undefined && !testing.isPending ? <ProbeResult probe={settings.probe} /> : null}
    </div>
  );
}

function DataStep({ draft, onDraft }: { draft: Draft; onDraft: (next: Draft) => void }) {
  const t = useT();
  const own = isOwn(draft.origin);
  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-5 sm:grid-cols-2">
        <Subsection title={t("assistantSettings.data.leaves")}>
          <ul className="m-0 flex list-disc flex-col gap-1.5 pl-5 text-13 text-pretty text-fg">
            <li>{t("assistantSettings.data.leavesList.question")}</li>
            <li>{t("assistantSettings.data.leavesList.page")}</li>
            <li>{t("assistantSettings.data.leavesList.tools")}</li>
          </ul>
        </Subsection>
        <Subsection title={t("assistantSettings.data.never")}>
          <ul className="m-0 flex list-disc flex-col gap-1.5 pl-5 text-13 text-pretty text-fg">
            <li>{t("assistantSettings.data.neverList.secrets")}</li>
            <li>{t("assistantSettings.data.neverList.files")}</li>
            <li>{t("assistantSettings.data.neverList.accounts")}</li>
          </ul>
        </Subsection>
      </div>
      <Checkbox
        label={t("assistantSettings.data.mask")}
        description={`${own ? t("assistantSettings.data.maskOwn") : t("assistantSettings.data.maskOutside")} ${t("assistantSettings.data.maskHelp")}`}
        checked={own ? draft.mask : true}
        disabled={!own}
        onCheckedChange={(checked) => onDraft({ ...draft, mask: checked })}
      />
      <Disclosure variant="card" label={t("assistantSettings.data.example")} summary={t("assistantSettings.data.exampleHelp")}>
        <div className="flex flex-col gap-2 pt-1">
          <p className="text-12 text-fg-muted">{t("assistantSettings.data.exampleSent")}</p>
          <SystemOutput label={t("assistantSettings.data.exampleSent")}>{t("assistantSettings.data.exampleLine")}</SystemOutput>
          <p className="text-12 text-fg-muted">{t("assistantSettings.data.exampleHidden")}</p>
        </div>
      </Disclosure>
    </div>
  );
}

function WhoStep() {
  const t = useT();
  const roles = useQuery(rolesQuery());
  const holding = (roles.data?.roles ?? []).filter((role) => role.permissions.includes("assistant.use"));
  return (
    <div className="flex flex-col gap-4">
      <p className="text-13 text-pretty text-fg">{t("assistantSettings.who.description")}</p>
      <div className="flex flex-col gap-1.5">
        <p className="text-13 font-medium text-fg">{holding.length > 0 ? t("assistantSettings.who.roles") : t("assistantSettings.who.rolesNone")}</p>
        {holding.length > 0 ? (
          <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-13 text-fg">
            {holding.map((role) => (
              <li key={role.name}>
                <Mono>{role.name}</Mono>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <p className="text-13 text-pretty text-fg-muted">{t("assistantSettings.who.teams")}</p>
      <p className="text-13 text-pretty text-fg-muted">{t("assistantSettings.who.note")}</p>
      <p>
        <TextLink to="/settings/accounts" size="ui">
          {t("assistantSettings.who.seeRoles")}
        </TextLink>
      </p>
    </div>
  );
}

/**
 * The four steps that turn the assistant on: where the model comes from (with the probe, in sentences), what leaves the server, the
 * limits and prices, and who may use it. Each step saves what it asks as it goes, and nothing is turned on and nothing leaves the
 * server until the last: the assistant is on only after a probe found the provider answering. Pressing Continue with something
 * missing says what, once, and moves nowhere.
 */
export function TurnOnWizard({ settings, step, onStep }: TurnOnWizardProps) {
  const t = useT();
  useDocumentTitle(t("assistantSettings.turnOn.title"), 1);
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(settings));
  const [testedAs, setTestedAs] = useState<string | null>(() => (settings.probe?.reachable === true ? tested(draftFrom(settings)) : null));
  const steps: WizardStep[] = STEPS.map((id) => ({ id, label: t(`assistantSettings.turnOn.steps.${id}`) }));
  const at = STEPS.indexOf(step);
  const go = (next: Step): void => {
    onStep(next);
  };

  // Each step saves what it asked as it goes; the last one turns the assistant on. One call, so the permission it takes is declared once.
  const can = useCan();
  const apply = useMutation({
    mutationFn: async (from: "data" | "limits" | "who") => {
      const mask = { mask_personal: isOwn(draft.origin) ? draft.mask : true };
      if (from === "data") return saveAssistantSettings({ ...mask, probe: false });
      if (from === "limits") return saveAssistantSettings(limitsBody(draft, settings));
      await saveAssistantSettings({ ...limitsBody(draft, settings), ...mask });
      return saveAssistantSettings({ enabled: true, probe: false });
    },
    onSuccess: (saved, from) => {
      queryClient.setQueryData(assistantKeys.settings, saved);
      if (from === "data") go("limits");
      else if (from === "limits") go("who");
      else onStep(undefined);
    },
  });

  const missingForModel = ((): string | undefined => {
    if (modelMissing(draft) !== null) return t("assistantSettings.model.missingAddress");
    if (testedAs === null || testedAs !== tested(draft)) return t("assistantSettings.model.missingTest");
    return undefined;
  })();

  const next = (): void => {
    if (step === "model") go("data");
    else if (can("settings.manage")) apply.mutate(step);
  };

  return (
    <div className="flex max-w-settings flex-col gap-6">
      <Card padding="md">
        <div className="flex flex-col gap-6">
          <Stepper steps={steps} current={step} orientation="horizontal" onSelect={(id) => go(id as Step)} />
          <Section
            title={step === "model" ? t("assistantSettings.model.title") : step === "data" ? t("assistantSettings.data.title") : step === "limits" ? t("assistantSettings.limits.title") : t("assistantSettings.who.title")}
            {...(step === "model" ? { description: t("assistantSettings.model.description") } : step === "data" ? { description: t("assistantSettings.data.description") } : step === "limits" ? { description: t("assistantSettings.limits.description") } : {})}
          >
          {step === "model" ? (
            <ModelStep
              draft={draft}
              settings={settings}
              onDraft={setDraft}
              onTested={(done) => {
                setTestedAs(tested(done));
              }}
            />
          ) : step === "data" ? (
            <DataStep draft={draft} onDraft={setDraft} />
          ) : step === "limits" ? (
            <LimitsFields draft={draft} onChange={setDraft} model={draft.model} />
          ) : (
            <WhoStep />
          )}
          </Section>
          {apply.isError ? <ErrorBlock compact live error={apply.error} title={t("assistantSettings.turnOn.saveFailed")} /> : null}
          <WizardActions
            {...(at > 0 ? { back: { label: t("common.words.back"), onClick: () => go(STEPS[at - 1] ?? "model") } } : { back: { label: t("common.words.back"), onClick: () => onStep(undefined) } })}
            next={{ label: step === "who" ? t("assistantSettings.turnOn.finish") : t("common.words.continue"), onClick: next, loading: apply.isPending }}
            {...(step === "model" && missingForModel !== undefined ? { missing: missingForModel } : {})}
          />
        </div>
      </Card>
    </div>
  );
}

