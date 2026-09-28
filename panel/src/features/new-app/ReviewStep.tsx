import { TriangleAlert } from "lucide-react";
import { useId } from "react";
import type { ReactNode, Ref, SyntheticEvent } from "react";

import { Section } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { cx } from "../../lib/cx";
import { AddressFields } from "./AddressFields";
import { EnvironmentFields } from "./EnvironmentFields";
import { InspectionReadout } from "./InspectionReadout";
import { PersistentPathsField } from "./PersistentPathsField";
import { PlatformProposalPanel } from "./PlatformProposalPanel";
import { ResourceLimitsFields } from "./ResourceLimitsFields";
import { useDomainDnsCheck } from "./useDomainDnsCheck";
import { hasPort, platformName, typeName, typeOptions, withProposal } from "./wizard";
import type { AppTypeOption, Inspection, Layout, ReviewErrors, ReviewForm, WebServer } from "./wizard";

const LAYOUTS: readonly { value: Layout; label: PlainKey; description: PlainKey }[] = [
  { value: "releases", label: "newApp.review.releases", description: "newApp.review.releasesDescription" },
  { value: "inplace", label: "newApp.review.inplace", description: "newApp.review.inplaceDescription" },
];

function Choice<V extends string>({
  legend,
  options,
  value,
  onChange,
  badge,
}: {
  legend: string;
  options: readonly { value: V; label: string; description: string }[];
  value: V;
  onChange: (value: V) => void;
  badge?: Partial<Record<V, string>>;
}) {
  const name = useId();
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1.5 text-13 font-medium text-fg">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <label
            key={option.value}
            className={cx(
              "grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] content-start items-start gap-x-2.5 gap-y-0.5 rounded-control border px-3 py-2.5",
              "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-focus",
              value === option.value ? "border-accent bg-accent-soft" : "border-border bg-surface hover:bg-surface-hover",
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="row-span-2 mt-0.5 size-4 shrink-0 accent-accent"
            />
            <span className="flex items-center gap-2 text-13 font-medium text-fg">
              {option.label}
              {badge?.[option.value] !== undefined ? <span className="text-12 font-normal text-fg-muted">{badge[option.value]}</span> : null}
            </span>
            <span className="col-start-2 text-12 text-pretty text-fg-muted">{option.description}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

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

/** A titled part of a review, ruled off from the one above. */
export function ReviewGroup({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <Section title={title} level={3} {...(description !== undefined ? { description } : {})} className="border-t border-border pt-6">
      {children}
    </Section>
  );
}

export interface ReviewStepProps {
  inspection: Inspection;
  /** Every type the deployer registry knows, for the "Deploy as" select and its labels. */
  types: readonly AppTypeOption[];
  /** Ports other apps on this machine hold, and which. */
  taken: ReadonlyMap<number, string>;
  /** CPUs of this machine, for the resource limits' description; null while unknown. */
  cores: number | null;
  source: string;
  form: ReviewForm;
  errors: ReviewErrors;
  onChange: (form: ReviewForm) => void;
  onBack: () => void;
  onContinue: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}

/**
 * Step two: what the inspection proposes, every part of it editable. The detected type is a
 * choice, never a silent guess; the commands are shown as they will run; the environment is a
 * form generated from `.env.example`. The source is not asked again.
 */
export function ReviewStep({ inspection, types, taken, cores, source, form, errors, onChange, onBack, onContinue, headingRef }: ReviewStepProps) {
  const t = useT();
  const set = (patch: Partial<ReviewForm>): void => {
    onChange({ ...form, ...patch });
  };
  const dns = useDomainDnsCheck(form.domain);
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    dns.checkNow();
    onContinue();
  };
  const detected = inspection.detected_types.length > 0;
  const chosenElsewhere = detected && form.appType !== inspection.app_type;
  const proposal = inspection.platform_proposal ?? null;

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 ref={headingRef} tabIndex={-1} className="title text-18 text-fg outline-none">
          {t("newApp.review.heading")}
        </h2>
        <p className="text-14 text-pretty text-fg-muted">{t("newApp.review.intro")}</p>
      </header>

      <InspectionReadout inspection={inspection} types={types} source={source} />

      {proposal !== null ? (
        <PlatformProposalPanel proposal={proposal} used={form.useProposal} onUse={(used) => onChange(withProposal(form, inspection, taken, used))} />
      ) : null}

      <Field
        label={t("newApp.review.deployAs")}
        nativeLabel={false}
        error={errors["appType"]}
        description={t("newApp.review.deployAsDescription")}
        className="sm:max-w-96"
      >
        <Select
          options={typeOptions(types, inspection.detected_types)}
          value={form.appType === "" ? null : form.appType}
          placeholder={t("newApp.review.chooseType")}
          onValueChange={(appType) => set({ appType })}
          className="w-full"
        />
      </Field>
      {chosenElsewhere ? (
        <p className="-mt-3 flex items-start gap-2 text-13 text-pretty text-fg">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
          {t("newApp.review.chosenElsewhere", { detected: typeName(types, inspection.app_type), chosen: typeName(types, form.appType) })}
        </p>
      ) : null}

      <ReviewGroup title={t("newApp.review.address")} description={t("newApp.review.addressDescription")}>
        <div className="flex flex-col gap-5">
          <AddressFields value={form} onChange={set} error={errors["domain"]} dns={dns} />
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
        </div>
      </ReviewGroup>

      <ReviewGroup title={t("newApp.review.runtime")}>
        <div className="flex flex-col gap-5">
          {hasPort(form.appType) ? (
            <Field
              label={t("newApp.review.port")}
              error={errors["port"]}
              description={portNote(t, inspection, form, types, taken)}
              className="sm:max-w-80"
            >
              <Input mono inputMode="numeric" value={form.port} onValueChange={(value: string) => set({ port: value })} autoComplete="off" />
            </Field>
          ) : null}
          <Choice<Layout>
            legend={t("newApp.review.deploys")}
            options={LAYOUTS.map((option) => ({ value: option.value, label: t(option.label), description: t(option.description) }))}
            value={form.layout}
            onChange={(layout) => set({ layout })}
            badge={{ releases: t("newApp.review.recommended") }}
          />
          {form.layout === "releases" ? (
            <div className="flex flex-col gap-2">
              <div className="flex flex-col gap-0.5">
                <span className="text-13 font-medium text-fg">{t("newApp.review.persistentPaths")}</span>
                <span className="text-12 text-fg-muted">
                  {t.rich("newApp.review.persistentDescription", { shared: <code translate="no">shared/</code> })}
                </span>
              </div>
              <PersistentPathsField rows={form.persistentPaths} errors={errors} onChange={(persistentPaths) => set({ persistentPaths })} />
            </div>
          ) : null}
          <ResourceLimitsFields draft={form.limits} cores={cores} errors={errors} onChange={(limits) => set({ limits })} />
        </div>
      </ReviewGroup>

      <ReviewGroup
        title={t("newApp.review.environment")}
        description={t("newApp.review.environmentDescription")}
      >
        <EnvironmentFields rows={form.env} errors={errors} onChange={(env) => set({ env })} />
      </ReviewGroup>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-6">
        <Button onClick={onBack}>{t("newApp.review.back")}</Button>
        <Button type="submit" variant="primary">
          {t("newApp.review.continue")}
        </Button>
      </div>
    </form>
  );
}
