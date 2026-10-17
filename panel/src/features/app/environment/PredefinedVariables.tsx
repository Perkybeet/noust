import { KeyValueList } from "../../../components/page/KeyValueList";
import { Section } from "../../../components/page/Section";
import { useT } from "../../../i18n";

/** The order the variables are explained in: where it answers, then what it was built from. */
const ORDER = ["NOUST_URL", "NOUST_DOMAIN", "NOUST_BRANCH", "NOUST_COMMIT", "NOUST_RELEASE", "NOUST_PR_NUMBER", "NOUST_PREVIEW_OF"] as const;

function rank(name: string): number {
  const index = ORDER.indexOf(name as (typeof ORDER)[number]);
  return index === -1 ? ORDER.length : index;
}

/**
 * What Noust itself gives the application about its deployment (`NOUST_URL`, `NOUST_BRANCH`...),
 * read-only and apart from the application's own variables: they are not in its `.env`, they
 * cannot be changed or removed, and their names are not the operator's to take.
 */
export function PredefinedVariables({ domain, predefined }: { domain: string; predefined: Readonly<Record<string, string>> | undefined }) {
  const t = useT();
  const entries = Object.entries(predefined ?? {}).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
  return (
    <Section title={t("variables.env.predefinedTitle")} description={t("variables.env.predefinedDescription")}>
      {entries.length === 0 ? (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("variables.env.predefinedEmpty")}</p>
      ) : (
        <div role="group" aria-label={t("variables.env.predefinedTable", { domain })}>
          <KeyValueList items={entries.map(([name, value]) => ({ label: name, value }))} />
        </div>
      )}
      <p className="max-w-measure text-12 text-pretty text-fg-muted">
        {t("variables.env.predefinedRead")} {t("variables.env.predefinedBuildNote")}
      </p>
    </Section>
  );
}
