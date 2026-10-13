import type { RecipeProblem } from "../../api/queries/recipes";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";

/**
 * The files in the server's own recipes that were left out of the catalogue, and why: the
 * server's words, verbatim, with the file they are about. Nothing is shown when every file
 * loaded. A broken file never takes the catalogue down; this is where it is told.
 */
export function CatalogProblems({ problems }: { problems: readonly RecipeProblem[] }) {
  const t = useT();
  if (problems.length === 0) return null;
  return (
    <Notice tone="warning" title={t("catalog.problems.title", { count: problems.length })}>
      <ul className="flex flex-col gap-2">
        {problems.map((problem) => (
          <li key={`${problem.origin}:${problem.location}`} className="flex min-w-0 flex-col gap-0.5">
            <Mono truncate title={problem.location}>
              {problem.location}
            </Mono>
            <span className="text-pretty">{problem.error}</span>
            {problem.details ? <span className="text-pretty text-fg-muted">{problem.details}</span> : null}
          </li>
        ))}
      </ul>
    </Notice>
  );
}
