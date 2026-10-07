import { Checkbox } from "../../../components/ui/Checkbox";
import { Field } from "../../../components/ui/Field";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import type { SelectOption } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import type { EngineChoice } from "../../../api/queries/provisioning";
import {
  DEFAULT_VERSION,
  choiceFor,
  defaultOffer,
  isPickable,
  isSelectable,
  needsInstall,
  offerState,
  orderedChoices,
  shownVersion,
} from "./choices";
import type { ProvisionPick } from "./choices";

/** What a flavour says of itself in the list: installed (and which version), or installable. */
function engineHint(t: T, choice: EngineChoice): string | undefined {
  if (choice.installed) {
    return choice.running_version !== null && choice.running_version !== undefined
      ? t("provisioning.engineState.installed", { version: choice.running_version })
      : t("provisioning.engineState.installedUnknown");
  }
  return choice.installable ? t("provisioning.engineState.installable") : undefined;
}

/** The first option of the version list: what "no version" means for this flavour. */
function defaultLabel(t: T, choice: EngineChoice): string {
  if (choice.installed) {
    return choice.running_version !== null && choice.running_version !== undefined
      ? t("provisioning.version.defaultRunning", { version: choice.running_version })
      : t("provisioning.version.defaultRunningUnknown");
  }
  const offer = defaultOffer(choice);
  return offer === undefined ? t("provisioning.version.defaultInstallUnknown") : t("provisioning.version.defaultInstall", { version: offer.version });
}

export interface EngineVersionFieldsProps {
  choices: readonly EngineChoice[];
  pick: ProvisionPick;
  onPick: (pick: ProvisionPick) => void;
  /** Said under the install box when the operator went on without ticking it. */
  installError?: string | undefined;
}

/**
 * The engine and the version a new database is created on, from what the server says it can
 * do: an installed engine offers the version that runs, an engine that is not installed offers
 * the versions Noust can install and asks to install it first. What cannot be used is listed
 * with the server's own reason, never hidden.
 */
export function EngineVersionFields({ choices, pick, onPick, installError }: EngineVersionFieldsProps) {
  const t = useT();
  const chosen = choiceFor(choices, pick.flavour);
  const usable = orderedChoices(choices).filter(isPickable);
  const unusable = choices.filter((choice) => !isPickable(choice));

  const engineOptions: SelectOption[] = usable.map((choice) => {
    const hint = engineHint(t, choice);
    return { value: choice.flavour, label: choice.display_name, ...(hint !== undefined ? { hint } : {}) };
  });

  const versionOptions: SelectOption[] =
    chosen === undefined
      ? []
      : [
          { value: DEFAULT_VERSION, label: defaultLabel(t, chosen) },
          ...chosen.versions.map((offer) => ({
            value: offer.version,
            label: offer.version,
            hint: t(`provisioning.version.state.${offerState(offer)}`),
            disabled: !isSelectable(chosen, offer),
          })),
        ];

  const installing = needsInstall(chosen);
  const version = shownVersion(chosen, pick);

  if (choices.length > 0 && usable.length === 0) {
    return (
      <Notice tone="warning" title={t("provisioning.none.title")}>
        {t("provisioning.none.body")}
      </Notice>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <Field label={t("provisioning.engineLabel")} nativeLabel={false}>
        <Select
          value={pick.flavour}
          placeholder={t("provisioning.enginePlaceholder")}
          onValueChange={(flavour) => onPick({ flavour, version: null, install: false })}
          options={engineOptions}
        />
      </Field>
      {unusable.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className="text-12 font-medium text-fg-muted">{t("provisioning.unavailable.title")}</p>
          <ul className="flex flex-col gap-0.5 text-12 text-fg-muted">
            {unusable.map((choice) => (
              <li key={choice.flavour}>{t("provisioning.unavailable.line", { engine: choice.display_name, reason: choice.reason ?? t("provisioning.unavailable.noReason") })}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {chosen !== undefined ? (
        <Field
          label={t("provisioning.versionLabel")}
          nativeLabel={false}
          description={chosen.installed ? t("provisioning.version.helpInstalled", { engine: chosen.display_name }) : t("provisioning.version.helpInstallable", { engine: chosen.display_name })}
        >
          <Select
            value={pick.version ?? DEFAULT_VERSION}
            onValueChange={(next) => onPick({ ...pick, version: next === DEFAULT_VERSION ? null : next })}
            options={versionOptions}
          />
        </Field>
      ) : null}
      {installing && chosen !== undefined ? (
        <Checkbox
          checked={pick.install}
          onCheckedChange={(install) => onPick({ ...pick, install })}
          label={t("provisioning.install.label", { engine: chosen.display_name })}
          description={
            version !== null
              ? t("provisioning.install.description", { engine: chosen.display_name, version })
              : t("provisioning.install.descriptionUnknown", { engine: chosen.display_name })
          }
        />
      ) : null}
      {installError !== undefined ? <Notice tone="warning" live>{installError}</Notice> : null}
    </div>
  );
}
