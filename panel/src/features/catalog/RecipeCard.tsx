import { Ban, Container } from "lucide-react";

import type { RecipeSummary } from "../../api/queries/recipes";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDate } from "../../lib/format";
import { DEPENDENCY_LABELS, categoryLabelKey, dayOf, versionLabel } from "./model";
import { RecipeLogo } from "./RecipeLogo";

/** The host of a project's website, as the link's text: "wordpress.org". */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** Tags drawn on a card; the rest are one count away, and all of them are on its page. */
const TAGS_SHOWN = 3;

/** What a recipe asks the server to provide, in the words of each (a name the console does not know is shown as is). */
export function dependencyWords(t: T, dependencies: readonly string[]): string[] {
  return dependencies.map((name) => {
    const key = DEPENDENCY_LABELS[name];
    return key === undefined ? name : t(key);
  });
}

/** A category's name, or the category as the server wrote it when this console has no word for it. */
export function categoryWords(t: T, category: string): string {
  const key = categoryLabelKey(category);
  return key === null ? category : t(key);
}

export interface RecipeCardProps {
  recipe: RecipeSummary;
  /** Whether the card's Use button is the one working: the recipe is being read in full. */
  opening?: boolean;
  /** Another card's recipe is being opened, so this one waits. */
  disabled?: boolean;
  /** What "Use" does: opens the recipe's install form. Omitted, the card has no such button. */
  onChoose?: () => void;
  /** Offers the recipe's own page (`/catalog/$name`): where the catalogue is browsed, not inside the wizard. */
  detail?: boolean;
  /** The heading level of its name: 2 directly under a page's title, 3 inside a step's section. */
  level?: 2 | 3;
}

/**
 * One recipe of the catalogue: its logo, name and slogan, what it needs to run, the version it
 * pins and when the recipe last changed, where its documentation and site are, and the one
 * button that starts using it. A recipe this release cannot deploy is drawn too, with the
 * reason the server gives and no button: listing what is missing is part of being honest about
 * the catalogue.
 */
export function RecipeCard({ recipe, opening = false, disabled = false, onChoose, detail = false, level = 3 }: RecipeCardProps) {
  const t = useT();
  const version = versionLabel(recipe.version, recipe.revision);
  const updated = dayOf(recipe.updated);
  // What Noust sets up for it, and what has to be on the server: for a recipe that runs in
  // Docker the server's side is the one badge above, so it is not said twice.
  const needs = [...dependencyWords(t, recipe.dependencies), ...(recipe.runs_in_docker ? [] : recipe.requires)];
  const shown = recipe.tags.slice(0, TAGS_SHOWN);
  const more = recipe.tags.length - shown.length;
  const docs = recipe.docs !== null && recipe.docs !== undefined && recipe.docs !== recipe.homepage ? recipe.docs : null;
  return (
    <Card
      as="li"
      level={level}
      padding="sm"
      title={
        <span className="flex min-w-0 items-center gap-3">
          <RecipeLogo logo={recipe.logo} title={recipe.title} />
          <span translate="no" className="min-w-0 break-words">
            {recipe.title}
          </span>
        </span>
      }
      description={recipe.slogan !== "" ? recipe.slogan : recipe.description}
      className="h-full"
    >
      <div className="flex h-full min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {version !== null ? <Badge mono>{version}</Badge> : null}
          <Badge>{categoryWords(t, recipe.category)}</Badge>
          {recipe.origin === "operator" ? <Badge>{t("catalog.card.yours")}</Badge> : null}
          {recipe.origin === "catalog" ? <Badge>{t("catalog.card.package")}</Badge> : null}
          {recipe.runs_in_docker ? (
            <Badge>
              <Container aria-hidden="true" className="size-icon-xs" />
              {t("catalog.card.docker")}
            </Badge>
          ) : null}
        </div>
        {shown.length > 0 ? (
          <ul aria-label={t("catalog.card.tags")} className="flex flex-wrap items-center gap-1.5">
            {shown.map((tag) => (
              <li key={tag} translate="no" className="text-12 text-fg-muted">
                <span aria-hidden="true">#</span>
                {tag}
              </li>
            ))}
            {more > 0 ? <li className="text-12 text-fg-faint">{t("catalog.card.moreTags", { count: more })}</li> : null}
          </ul>
        ) : null}
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
              <Ban aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
              {t("newApp.recipes.unavailable")}
            </span>
            {recipe.unavailable_reason ? <p className="text-12 text-pretty text-fg-muted">{recipe.unavailable_reason}</p> : null}
          </div>
        )}
        <div className="mt-auto flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          {/* Each link is a 24px target on its own (WCAG 2.5.8): wrapped onto a second line, as in
              Spanish inside the wizard's three columns, a 16px link has too little room around it. */}
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-12">
            <ExternalLink href={recipe.homepage} inline unlinked="text" className="min-h-6">
              {hostOf(recipe.homepage)}
            </ExternalLink>
            {docs !== null ? (
              <ExternalLink href={docs} inline label={t("catalog.card.docsOf", { title: recipe.title })} className="min-h-6">
                {t("catalog.card.docs")}
              </ExternalLink>
            ) : null}
            {detail ? (
              <TextLink
                to="/catalog/$name"
                params={{ name: recipe.name }}
                size="inline"
                className="inline-flex min-h-6 items-center"
                aria-label={t("catalog.card.detailsOf", { title: recipe.title })}
              >
                {t("catalog.card.details")}
              </TextLink>
            ) : null}
            {updated !== null ? (
              <time dateTime={recipe.updated ?? ""} className="text-fg-faint">
                {t("catalog.card.updated", { date: formatDate(updated, {}, t.locale) })}
              </time>
            ) : null}
          </div>
          {recipe.available && onChoose !== undefined ? (
            <Button size="sm" loading={opening} disabled={disabled && !opening} onClick={onChoose}>
              {t("newApp.recipes.use", { title: recipe.title })}
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
