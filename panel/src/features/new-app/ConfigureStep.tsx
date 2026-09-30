import { ChevronDown } from "lucide-react";

import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Subsection } from "../../components/page/Subsection";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { InspectionReadout } from "./InspectionReadout";
import { PersistentPathsField } from "./PersistentPathsField";
import { PlatformProposalPanel } from "./PlatformProposalPanel";
import { ResourceLimitsFields } from "./ResourceLimitsFields";
import { HEALTH_FIELDS, errorsOf, hasPort, platformName, typeName, typeOptions, withProposal } from "./wizard";
import type { AppTypeOption, Inspection, Layout, ReviewErrors, ReviewForm, WebServer } from "./wizard";

/** Where the proposed port came from, so a number that is not the framework's default is explained. */
function portNote(t: T, inspection: Inspection, form: ReviewForm, types: readonly AppTypeOption[], taken: ReadonlyMap<number, string>): string {
  const proposal = inspection.platform_proposal ?? null;
  const asked = proposal?.port ?? null;
  if (form.useProposal && proposal !== null && asked !== null) {
    return t("newApp.review.portProposal", { platform: platformName(proposal.platform), port: String(asked) });
  }
  const preferred = inspection.default_port;
  const owner = taken.get(preferred);
  if (inspection.detected_types.length === 0) return t("newApp.review.portNote");
  const type = typeName(types, inspection.app_type);
  if (owner === undefined) return t("newApp.review.portDefault", { type, port: String(preferred) });
  return t("newApp.review.portTaken", { type, port: String(preferred), owner });
}

/** The advanced choices in a line, for the folded group's summary: how deploys work and the web server. */
function advancedSummary(t: T, form: ReviewForm): string {
  const layout = form.layout === "releases" ? t("newApp.review.releases") : t("newApp.review.inplace");
  const webserver = form.webserver === "apache" ? "Apache" : "nginx";
  return t("newApp.review.advancedSummary", { layout, webserver });
}

export interface ConfigureStepProps {
  inspection: Inspection;
  /** Every type the deployer registry knows, for the type select and its labels. */
  types: readonly AppTypeOption[];
  /** Ports other apps on this machine hold, and which. */
  taken: ReadonlyMap<number, string>;
  /** CPUs of this machine, for the resource limits' description; null while unknown. */
  cores: number | null;
  source: string;
  form: ReviewForm;
  errors: ReviewErrors;
  onChange: (form: ReviewForm) => void;
}

/**
 * How the app runs: what the inspection found, every part of it editable. The type is a
 * choice, never a silent guess; the commands are shown as they will run; the port is proposed
 * free. What most deploys leave as it is (how deploys work, the folders kept between them, the
 * web server, the resource limits) is folded under "Advanced", open when one of them needs a fix.
 */
export function ConfigureStep({ inspection, types, taken, cores, source, form, errors, onChange }: ConfigureStepProps) {
  const t = useT();
  const set = (patch: Partial<ReviewForm>): void => {
    onChange({ ...form, ...patch });
  };
  const detected = inspection.detected_types.length > 0;
  const chosenElsewhere = detected && form.appType !== inspection.app_type;
  const proposal = inspection.platform_proposal ?? null;
  const advancedErrors = errorsOf(errors, "configure").some(([field]) => field.startsWith("path:") || field.startsWith("limit:"));

  return (
    <div className="flex flex-col gap-6">
      <InspectionReadout inspection={inspection} types={types} source={source} />

      {proposal !== null ? (
        <PlatformProposalPanel
          proposal={proposal}
          used={form.useProposal}
          onUse={(used) => onChange(withProposal(form, inspection, taken, used))}
          healthErrors={HEALTH_FIELDS.flatMap((field) => errors[field] ?? [])}
        />
      ) : null}

      <div className="flex flex-col gap-5">
        <Field label={t("newApp.review.deployAs")} nativeLabel={false} error={errors["appType"]} description={t("newApp.review.deployAsDescription")} className="sm:max-w-96">
          <Select
            options={typeOptions(types, inspection.detected_types)}
            value={form.appType === "" ? null : form.appType}
            placeholder={t("newApp.review.chooseType")}
            onValueChange={(appType) => set({ appType })}
            className="w-full"
          />
        </Field>
        {chosenElsewhere ? (
          <Notice tone="warning">
            {t("newApp.review.chosenElsewhere", { detected: typeName(types, inspection.app_type), chosen: typeName(types, form.appType) })}
          </Notice>
        ) : null}
        {hasPort(form.appType) ? (
          <Field label={t("newApp.review.port")} error={errors["port"]} description={portNote(t, inspection, form, types, taken)} className="sm:max-w-80">
            <Input mono inputMode="numeric" value={form.port} onValueChange={(value: string) => set({ port: value })} autoComplete="off" />
          </Field>
        ) : null}
      </div>

      <details open={advancedErrors} className="group overflow-hidden rounded-card border border-border">
        <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 -outline-offset-2 hover:bg-surface-hover [&::-webkit-details-marker]:hidden">
          <span className="text-13 font-medium text-fg">{t("newApp.review.advanced")}</span>
          <span className="flex items-center gap-1.5 text-12 text-fg-muted">
            <span className="group-open:hidden">{advancedSummary(t, form)}</span>
            <ChevronDown aria-hidden="true" className="size-icon-sm transition-transform duration-(--duration-fast) group-open:rotate-180" />
          </span>
        </summary>
        <div className="flex flex-col gap-6 border-t border-border px-4 py-4">
          <ChoiceCards<Layout>
            legend={t("newApp.review.deploys")}
            options={[
              { value: "releases", label: t("newApp.review.releases"), description: t("newApp.review.releasesDescription"), badge: t("newApp.review.recommended") },
              { value: "inplace", label: t("newApp.review.inplace"), description: t("newApp.review.inplaceDescription") },
            ]}
            value={form.layout}
            onValueChange={(layout) => set({ layout })}
          />
          {form.layout === "releases" ? (
            <Subsection title={t("newApp.review.persistentPaths")} description={t("newApp.review.persistentDescription")}>
              <PersistentPathsField rows={form.persistentPaths} errors={errors} onChange={(persistentPaths) => set({ persistentPaths })} />
            </Subsection>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <span aria-hidden="true" className="text-13 font-medium text-fg">
              {t("newApp.review.webServer")}
            </span>
            <SegmentedControl<WebServer>
              label={t("newApp.review.webServer")}
              options={[
                { value: "nginx", label: "nginx" },
                { value: "apache", label: "Apache" },
              ]}
              value={form.webserver}
              onValueChange={(webserver) => set({ webserver })}
              className="self-start"
            />
          </div>
          <ResourceLimitsFields draft={form.limits} cores={cores} errors={errors} onChange={(limits) => set({ limits })} />
        </div>
      </details>
    </div>
  );
}
