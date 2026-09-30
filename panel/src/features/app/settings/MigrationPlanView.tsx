import type { MigrationPlan } from "../../../api/queries/apps";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Notice } from "../../../components/ui/Notice";
import { useT } from "../../../i18n";
import { translate } from "../../../i18n/translate";
import { formatBytes } from "../../../lib/format";

/** How the folders kept in shared/ were chosen, as the plan's `persistent_source` says. */
function persistentSourceHint(source: string, locale: Locale): string {
  if (source === "git") return translate(locale, "appSettings.migrationPlan.persistentSourceGit");
  if (source === "explicit") return translate(locale, "appSettings.migrationPlan.persistentSourceExplicit");
  if (source === "common") return translate(locale, "appSettings.migrationPlan.persistentSourceCommon");
  return source;
}

/** The rows of a plan, in the order the move does them. */
export function planItems(plan: MigrationPlan, locale: Locale = getLocale()): KeyValueItem[] {
  const fileCount = translate(locale, "appSettings.migrationPlan.fileCount", { count: plan.files });
  const items: KeyValueItem[] = [
    {
      label: translate(locale, "appSettings.migrationPlan.firstRelease"),
      value: plan.release_id,
      hint: translate(locale, "appSettings.migrationPlan.firstReleaseHint"),
    },
    {
      label: translate(locale, "appSettings.migrationPlan.commit"),
      value: plan.commit ?? translate(locale, "appSettings.migrationPlan.notGitCheckout"),
      mono: plan.commit !== null && plan.commit !== undefined,
      copy: false,
    },
    {
      label: translate(locale, "appSettings.migrationPlan.keptInShared"),
      value: plan.persistent.length > 0 ? plan.persistent.join(", ") : translate(locale, "appSettings.migrationPlan.nothing"),
      mono: plan.persistent.length > 0,
      copy: false,
      hint: persistentSourceHint(plan.persistent_source, locale),
    },
    {
      label: translate(locale, "appSettings.migrationPlan.environment"),
      value: plan.env_files.length > 0 ? plan.env_files.join(", ") : translate(locale, "appSettings.migrationPlan.noEnvFile"),
      mono: plan.env_files.length > 0,
      copy: false,
      hint: plan.env_files.length > 0 ? translate(locale, "appSettings.migrationPlan.envMovedHint") : undefined,
    },
    {
      label: translate(locale, "appSettings.migrationPlan.unit"),
      value: plan.unit
        ? translate(locale, plan.unit_rewrite ? "appSettings.migrationPlan.unitRewritten" : "appSettings.migrationPlan.unitUnchanged", { unit: plan.unit })
        : translate(locale, "appSettings.migrationPlan.unitNone"),
      mono: false,
      copy: false,
    },
    {
      label: translate(locale, "appSettings.migrationPlan.site"),
      value: plan.site_rewrite ? translate(locale, "appSettings.migrationPlan.siteRewritten") : translate(locale, "appSettings.migrationPlan.siteUnchanged"),
      mono: false,
      copy: false,
    },
    {
      label: translate(locale, "appSettings.migrationPlan.files"),
      value: translate(locale, "appSettings.migrationPlan.filesValue", { count: fileCount, bytes: formatBytes(plan.bytes, locale) }),
      mono: false,
      copy: false,
      hint: translate(locale, "appSettings.migrationPlan.filesHint"),
    },
  ];
  if (plan.untracked_files.length > 0) {
    items.push({
      label: translate(locale, "appSettings.migrationPlan.onlyInFirstRelease"),
      value: translate(locale, "appSettings.migrationPlan.untrackedFileCount", { count: plan.untracked_files.length }),
      mono: false,
      copy: false,
      hint:
        plan.untracked_files.slice(0, 5).join(", ") +
        (plan.untracked_files.length > 5 ? translate(locale, "appSettings.migrationPlan.untrackedHintMore") : ""),
    });
  }
  return items;
}

/**
 * What moving an in-place app onto releases would do, read from the disk: the warnings first,
 * verbatim, then each change in the order it happens.
 */
export function MigrationPlanView({ plan }: { plan: MigrationPlan }) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {plan.warnings.length > 0 ? (
        <ul aria-label={t("appSettings.migrationPlan.warningsLabel")} className="flex flex-col gap-2">
          {plan.warnings.map((warning) => (
            <li key={warning}>
              <Notice tone="warning">
                <span className="break-words">{warning}</span>
              </Notice>
            </li>
          ))}
        </ul>
      ) : null}
      <KeyValueList items={planItems(plan, t.locale)} />
    </div>
  );
}
