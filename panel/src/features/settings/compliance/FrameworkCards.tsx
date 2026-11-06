import type { ComplianceFramework } from "../../../api/queries/compliance";
import { Can } from "../../../components/page/permissions";
import { Button } from "../../../components/ui/Button";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { ResourceCard } from "../../../components/ui/ResourceCard";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { frameworkSummary } from "./model";

/** The permission every change of a framework or a control takes (the server's, `routes_compliance.py`). */
export const MANAGE = "security.manage";

/**
 * The frameworks the server offers and that are off, one card each: its name, the one line on what it is, how many
 * controls it asks anything of, and the button that turns it on, which says what changes before it does. The list is
 * the server's (`GET /api/compliance`), never one the console made up: a framework added there is a card here. The ones
 * that are on are not here: they are a report, above.
 */
export function FrameworkCards({ frameworks, onTurnOn }: { frameworks: readonly ComplianceFramework[]; onTurnOn: (framework: ComplianceFramework) => void }) {
  const t = useT();
  return (
    <ul className="grid min-w-0 list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3">
      {frameworks.map((framework) => (
        <li key={framework.id} className="min-w-0">
          <ResourceCard
            name={framework.name}
            untranslated
            icon={OBJECT_ICONS.control}
            status={<StatusPill state="stopped" label={t("complianceSetup.frameworks.off")} size="sm" />}
          >
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{frameworkSummary(t, framework)}</p>
            <p className="text-12 text-fg-muted">{t("complianceSetup.frameworks.controlsCount", { count: framework.requirement_count })}</p>
            {/* An action of a card is not drawn for a role that cannot take it: the page says once that it can only be read. */}
            <Can permission={MANAGE}>
              <div>
                <Button
                  size="sm"
                  onClick={() => {
                    onTurnOn(framework);
                  }}
                >
                  {t("complianceSetup.frameworks.enable")}
                </Button>
              </div>
            </Can>
          </ResourceCard>
        </li>
      ))}
    </ul>
  );
}
