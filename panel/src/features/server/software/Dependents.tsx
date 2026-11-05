import { Badge } from "../../../components/ui/Badge";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { useT } from "../../../i18n";
import type { Dependent } from "./queries";
import { dependentWords, namesOf } from "./words";

/** What depends on a piece, as chips in a table cell: "18 sites", "2 tunnels"; "Nothing" when nothing does. */
export function DependentChips({ dependents, installed }: { dependents: readonly Dependent[]; installed: boolean }) {
  const t = useT();
  if (!installed) return <EmptyCell reason={t("software.state.notInstalled")} />;
  if (dependents.length === 0) return <span className="text-fg-muted">{t("software.dependents.none")}</span>;
  return (
    <ul className="flex flex-wrap gap-1">
      {dependents.map((dependent) => (
        <li key={dependent.kind}>
          <Badge>{dependentWords(t, dependent)}</Badge>
        </li>
      ))}
    </ul>
  );
}

/** How many names of what depends on a piece are listed before "and N more": enough to recognise it, not a wall. */
const NAMES_SHOWN = 8;

/** What stands in the way of a removal, kind by kind with the names the operator knows each by, in mono. */
export function DependentList({ dependents }: { dependents: readonly Dependent[] }) {
  const t = useT();
  return (
    <RowList aria-label={t("software.remove.dependents")}>
      {dependents.map((dependent) => {
        const { shown, more } = namesOf(dependent.names ?? [], NAMES_SHOWN);
        return (
          <RowList.Item key={dependent.kind} className="min-h-0 px-3 py-2">
            <span className="flex flex-col gap-0.5 text-13">
              <span className="font-medium text-fg">{dependentWords(t, dependent)}</span>
              {shown.length > 0 ? (
                <span className="text-12 text-fg-muted">
                  <Mono tone="muted">{shown.join(", ")}</Mono>
                  {more > 0 ? <> {t("software.dependents.more", { count: more })}</> : null}
                </span>
              ) : null}
            </span>
          </RowList.Item>
        );
      })}
    </RowList>
  );
}
