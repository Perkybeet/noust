import { useQuery } from "@tanstack/react-query";
import { BookOpen, Ban } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";

import { recipesQuery } from "../../api/queries/recipes";
import type { RecipeSummary } from "../../api/queries/recipes";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";

/** The host of a project's website, as the link's text: "wordpress.org". */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function RecipeCard({ recipe, opening, disabled, onChoose }: { recipe: RecipeSummary; opening: boolean; disabled: boolean; onChoose: () => void }) {
  const t = useT();
  const titleId = useId();
  const database = recipe.database ?? null;
  const needs = [...(database !== null ? [t("newApp.recipes.database", { engine: database })] : []), ...recipe.requires];
  return (
    <li
      aria-labelledby={titleId}
      className={
        recipe.available
          ? "flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-4 shadow-raised"
          : "flex min-w-0 flex-col gap-3 rounded-card border border-dashed border-border bg-bg-sunken p-4"
      }
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h3 id={titleId} translate="no" className="flex items-center gap-2 text-14 font-medium text-fg">
          <BookOpen aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
          {recipe.title}
        </h3>
        <p className="text-13 text-pretty text-fg-muted">{recipe.description}</p>
      </div>
      {needs.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="text-12 font-medium text-fg">{t("newApp.recipes.needs")}</span>
          <ul className="flex list-disc flex-col gap-0.5 pl-4 text-12 text-pretty text-fg-muted marker:text-fg-faint">
            {needs.map((need) => (
              <li key={need}>{need}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {recipe.available ? null : (
        <div className="flex flex-col gap-1">
          <span className="flex items-center gap-1.5 text-12 font-medium text-fg">
            <Ban aria-hidden="true" className="size-3.5 shrink-0 text-fg-muted" />
            {t("newApp.recipes.unavailable")}
          </span>
          {recipe.unavailable_reason ? <p className="text-12 text-pretty text-fg-muted">{recipe.unavailable_reason}</p> : null}
        </div>
      )}
      <div className="mt-auto flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <ExternalLink href={recipe.homepage} inline unlinked="text" className="text-12">
          {hostOf(recipe.homepage)}
        </ExternalLink>
        {recipe.available ? (
          <Button size="sm" variant="primary" loading={opening} disabled={disabled && !opening} onClick={onChoose}>
            {t("newApp.recipes.use", { title: recipe.title })}
          </Button>
        ) : null}
      </div>
    </li>
  );
}

export interface RecipeGalleryProps {
  /** The recipe being read (`GET /api/recipes/{name}`) after it was chosen. */
  opening: string | null;
  /** Why reading the chosen recipe failed, verbatim. */
  failure: { title: string; error: unknown } | null;
  onChoose: (recipe: RecipeSummary) => void;
}

/**
 * The recipes this release ships, as cards: what each is, what it needs from this server, its
 * website. The ones it cannot deploy yet are shown too, marked, with the reason the server gives.
 */
export function RecipeGallery({ opening, failure, onChoose }: RecipeGalleryProps) {
  const t = useT();
  const recipes = useQuery(recipesQuery());
  const listId = useId();

  let body: ReactNode;
  if (recipes.data === undefined) {
    body = recipes.isError ? (
      <ErrorBlock error={recipes.error} title={t("newApp.recipes.loadFailed")} onRetry={() => void recipes.refetch()} retrying={recipes.isFetching} />
    ) : (
      <div aria-busy="true" className="grid gap-3 sm:grid-cols-2">
        <span className="sr-only">{t("newApp.recipes.loading")}</span>
        {["a", "b", "c", "d"].map((key) => (
          <Skeleton key={key} className="h-40" />
        ))}
      </div>
    );
  } else if (recipes.data.items.length === 0) {
    body = <p className="rounded-card border border-dashed border-border px-4 py-6 text-center text-13 text-fg-muted">{t("newApp.recipes.empty")}</p>;
  } else {
    body = (
      <ul aria-labelledby={listId} className="grid gap-3 sm:grid-cols-2">
        {recipes.data.items.map((recipe) => (
          <RecipeCard
            key={recipe.name}
            recipe={recipe}
            opening={opening === recipe.name}
            disabled={opening !== null}
            onChoose={() => onChoose(recipe)}
          />
        ))}
      </ul>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h3 id={listId} className="sr-only">
        {t("newApp.recipes.label")}
      </h3>
      {failure !== null ? <ErrorBlock live error={failure.error} title={failure.title} /> : null}
      {body}
      <CommandHint command="wasm recipe list" label={t("newApp.source.terminal")} />
    </div>
  );
}
