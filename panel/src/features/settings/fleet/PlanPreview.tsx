import { Subsection } from "../../../components/page/Subsection";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { StatusSetting } from "./data";
import { savePlan } from "./faces";
import type { Does } from "./faces";

/** The state each outcome is drawn with: what goes as asked is plain, what waits is queued, and what will not is grey. */
const STATE: Record<Does, Status | null> = {
  applies: null,
  waits: "queued",
  keepsOwn: null,
  skips: "stopped",
  refused: "stopped",
  unsupported: "stopped",
};

/**
 * What saving a value of the fleet does on each server, said before it is saved: who gets it now, who gets it when
 * it is back, who keeps a value of its own and who will not take it, and why. The change is never a surprise.
 */
export function PlanPreview({ row }: { row: StatusSetting }) {
  const t = useT();
  const plan = savePlan(row);
  if (plan.rows.length === 0) return null;
  const heading = t("fleetSettings.plan.title");
  return (
    <Subsection title={heading}>
      <RowList aria-label={heading}>
        {plan.rows.map((entry) => {
          const word = t(`fleetSettings.plan.${entry.does}`);
          const state = STATE[entry.does];
          return (
            <RowList.Item key={entry.node} actions={state !== null ? <StatusPill state={state} label={word} appearance="inline" size="sm" /> : <span className="text-13 text-fg">{word}</span>}>
              <Mono tone="default">{entry.node}</Mono>
            </RowList.Item>
          );
        })}
      </RowList>
    </Subsection>
  );
}
