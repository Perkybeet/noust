import { useQuery } from "@tanstack/react-query";

import { recipeRequirementsQuery } from "../../api/queries/recipes";
import type { RecipeRequirement } from "../../api/queries/recipes";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Subsection } from "../../components/page/Subsection";
import { Badge } from "../../components/ui/Badge";
import { Checkbox } from "../../components/ui/Checkbox";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { useT } from "../../i18n";
import { olderServer } from "../app/settings/queries";

export interface RecipeRequirementsProps {
  /** The recipe. */
  name: string;
  /**
   * Whether the install is to provide what is missing (`install_requirements`), with the
   * handler that changes it. Without them the list only reports, as the recipe's page does.
   */
  install?: { checked: boolean; onChange: (checked: boolean) => void };
  /** The heading level of the section: 3 under a page section, 4 inside a card. */
  level?: 3 | 4;
}

/** One requirement: what it is, whether the server has it, and when not, what was found and the command that provides it. */
function Requirement({ item }: { item: RecipeRequirement }) {
  const t = useT();
  return (
    <li className="flex min-w-0 flex-col gap-1 px-3 py-2">
      <span className="flex flex-wrap items-center gap-2">
        <Mono>{item.name}</Mono>
        <StatusPill state={item.met ? "running" : "warning"} label={item.met ? t("catalog.requirements.met") : t("catalog.requirements.missing")} size="sm" />
        {!item.met && item.installable ? <Badge>{t("catalog.requirements.installable")}</Badge> : null}
      </span>
      <span className="text-12 text-pretty text-fg-muted">{item.detail}</span>
      {!item.met && item.fix ? <CommandHint command={item.fix} label={t("catalog.requirements.fix")} /> : null}
    </li>
  );
}

/**
 * What a recipe needs from this server (tools, a language runtime, database engines) and
 * whether it has each, from `GET /api/recipes/{name}/requirements`. In the install form it also
 * offers to provide what is missing when the server can, so the install does not start and then
 * fail on the first missing runtime.
 */
export function RecipeRequirements({ name, install, level = 3 }: RecipeRequirementsProps) {
  const t = useT();
  const query = useQuery(recipeRequirementsQuery(name));
  const title = t("catalog.requirements.title");

  if (query.isPending) {
    return (
      <Subsection title={title} level={level}>
        <LoadingRegion label={t("catalog.requirements.loading")}>
          <Skeleton className="h-16 w-full rounded-control" />
        </LoadingRegion>
      </Subsection>
    );
  }
  // A server older than 3.4 cannot say: the install form works as it always did.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) {
    return (
      <Subsection title={title} level={level}>
        <ErrorBlock compact error={query.error} title={t("catalog.requirements.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      </Subsection>
    );
  }
  const { items, ready } = query.data;
  if (items.length === 0) return null;
  const missing = items.filter((item) => !item.met);
  const provides = missing.some((item) => item.installable);
  const stuck = missing.some((item) => !item.installable);
  return (
    <Subsection title={title} description={ready ? t("catalog.requirements.ready") : t("catalog.requirements.notReady", { count: missing.length })} level={level}>
      <ul aria-label={title} className="flex flex-col divide-y divide-border rounded-control border border-border">
        {items.map((item) => (
          <Requirement key={`${item.kind}:${item.name}`} item={item} />
        ))}
      </ul>
      {install !== undefined && provides ? (
        <Checkbox
          label={t("catalog.requirements.installLabel")}
          description={t("catalog.requirements.installDescription")}
          checked={install.checked}
          onCheckedChange={install.onChange}
        />
      ) : null}
      {install !== undefined && stuck ? <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("catalog.requirements.stuck")}</p> : null}
    </Subsection>
  );
}
