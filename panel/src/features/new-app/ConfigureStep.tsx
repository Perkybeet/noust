import { KeyValueList } from "../../components/page/KeyValueList";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Subsection } from "../../components/page/Subsection";
import { Card } from "../../components/ui/Card";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { ContainerFields } from "../container/ContainerFields";
import { HowItRuns } from "./HowItRuns";
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

/**
 * The port a container's process listens on that its Dockerfile says, to name where "empty" leads. The server says it
 * with the container way (a Node.js repository with a Dockerfile has Node.js's 3000 for its type and its own EXPOSE
 * for its container); against a server that does not, it is the inspection's port when the repository is a container.
 */
function suggestedContainerPort(inspection: Inspection, form: ReviewForm): number | null {
  if (form.container.method !== "dockerfile") return null;
  if (inspection.ways != null) return inspection.ways.container.port ?? null;
  return inspection.detected_types.includes("container") ? inspection.default_port : null;
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
  // The type of one of the two ways is the card's choice and not a choice against the detection (the card says what it
  // is); the warning is for a type picked by hand, which the commands read above are not those of.
  const ofAWay = inspection.ways?.server.app_type === form.appType || inspection.ways?.container.app_type === form.appType;
  const chosenElsewhere = detected && form.appType !== inspection.app_type && !ofAWay;
  const proposal = inspection.platform_proposal ?? null;
  const container = form.appType === "container";
  // An image in a registry has no repository: nothing was inspected, so there is nothing to read out.
  const pulled = container && form.container.method === "image";
  // How it runs is the server's decision, with its two cards; the type, which only a person who knows the
  // repository better than detection does changes, is then a choice under "Advanced" and not the question.
  const ways = detected && !pulled ? (inspection.ways ?? null) : null;
  // Where there is a choice, its two cards are the question and the type moves under "Advanced"; where there is none
  // (a Compose file, a lone Dockerfile) the type is where it always was.
  const typeInAdvanced = ways?.choice === true;
  const advancedErrors = errorsOf(errors, "configure").some(([field]) => field.startsWith("path:") || field.startsWith("limit:") || (typeInAdvanced && field === "appType"));

  const typeField = (
    <Field label={t("newApp.review.deployAs")} nativeLabel={false} error={errors["appType"]} description={t("newApp.review.deployAsDescription")} className="sm:max-w-96">
      <Select
        options={typeOptions(types, inspection.detected_types)}
        value={form.appType === "" ? null : form.appType}
        placeholder={t("newApp.review.chooseType")}
        onValueChange={(appType) => set({ appType })}
        className="w-full"
      />
    </Field>
  );
  const chosenNotice =
    chosenElsewhere && !pulled ? (
      <Notice tone="warning">
        {t("newApp.review.chosenElsewhere", { detected: typeName(types, inspection.app_type), chosen: typeName(types, form.appType) })}
      </Notice>
    ) : null;

  return (
    <div className="flex flex-col gap-6">
      {pulled ? (
        <Card padding="none">
          <div className="px-4 py-1">
            <KeyValueList
              items={[
                { label: t("containers.source.image"), value: form.container.image },
                {
                  label: t("containers.summary.builtFrom"),
                  value: t("containers.settings.fromImage"),
                  mono: false,
                  copy: false,
                  hint: t("containers.source.pulledEvery"),
                },
              ]}
            />
          </div>
        </Card>
      ) : (
        <InspectionReadout inspection={inspection} types={types} source={source} />
      )}

      {proposal !== null ? (
        <PlatformProposalPanel
          proposal={proposal}
          used={form.useProposal}
          onUse={(used) => onChange(withProposal(form, inspection, taken, used))}
          healthErrors={HEALTH_FIELDS.flatMap((field) => errors[field] ?? [])}
        />
      ) : null}

      {ways !== null ? <HowItRuns ways={ways} form={form} onChange={onChange} /> : null}

      <div className="flex flex-col gap-5">
        {pulled || typeInAdvanced ? null : typeField}
        {chosenNotice}
        {hasPort(form.appType) ? (
          <Field label={t("newApp.review.port")} error={errors["port"]} description={portNote(t, inspection, form, types, taken)} className="sm:max-w-80">
            <Input mono inputMode="numeric" value={form.port} onValueChange={(value: string) => set({ port: value })} autoComplete="off" />
          </Field>
        ) : null}
      </div>

      {container ? (
        <ContainerFields
          draft={form.container}
          errors={errors}
          onChange={(next) => set({ container: next })}
          showImage={form.container.method === "image"}
          suggestedPort={suggestedContainerPort(inspection, form)}
        />
      ) : null}

      {/* Opens by itself when something in it has an error to show; the key makes it open when that happens. */}
      <Disclosure
        key={advancedErrors ? "open" : "closed"}
        variant="card"
        defaultOpen={advancedErrors}
        label={t("newApp.review.advanced")}
        summary={advancedSummary(t, form)}
      >
        <div className="flex flex-col gap-6">
          {typeInAdvanced ? typeField : null}
          {container ? null : (
          <ChoiceCards<Layout>
            legend={t("newApp.review.deploys")}
            options={[
              { value: "releases", label: t("newApp.review.releases"), description: t("newApp.review.releasesDescription"), badge: t("newApp.review.recommended") },
              { value: "inplace", label: t("newApp.review.inplace"), description: t("newApp.review.inplaceDescription") },
            ]}
            value={form.layout}
            onValueChange={(layout) => set({ layout })}
          />
          )}
          {!container && form.layout === "releases" ? (
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
      </Disclosure>
    </div>
  );
}
