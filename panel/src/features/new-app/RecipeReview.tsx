import { KeyRound } from "lucide-react";
import type { Ref, SyntheticEvent } from "react";

import type { Recipe } from "../../api/queries/recipes";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { AddressFields } from "./AddressFields";
import { addressItem } from "./DeployStep";
import { ExternalLink } from "./ExternalLink";
import { ReviewGroup } from "./ReviewStep";
import { useDomainDnsCheck } from "./useDomainDnsCheck";
import { editableVariables, generatedVariables } from "./recipe";
import type { RecipeForm } from "./recipe";
import { joinList, typeName } from "./wizard";
import type { AppTypeOption, ReviewErrors } from "./wizard";

/** Where a recipe's code comes from, and how it is pinned or checked, in the server's terms. */
function sourceItem(t: T, recipe: Recipe): KeyValueItem | null {
  const source = recipe.source ?? null;
  if (source === null) return null;
  const pin =
    source.ref !== null && source.ref !== undefined
      ? t("newApp.recipes.sourceTag", { ref: source.ref })
      : source.sha256 !== null && source.sha256 !== undefined
        ? t("newApp.recipes.sourceSha", { sha: source.sha256 })
        : source.checksum_url !== null && source.checksum_url !== undefined
          ? t("newApp.recipes.sourceChecksum", { checksum: source.checksum_url })
          : null;
  if (source.url === null || source.url === undefined) {
    return source.files.length > 0
      ? { label: t("newApp.recipes.source"), value: t("newApp.recipes.sourceTemplate", { files: joinList(source.files, t.locale) }), mono: false, copy: false }
      : null;
  }
  return { label: t("newApp.recipes.source"), value: source.url, ...(pin !== null ? { hint: pin } : {}) };
}

/** What a recipe sets up besides the code: read-only, it comes with the recipe. */
function setupItems(t: T, recipe: Recipe): KeyValueItem[] {
  const health = recipe.health ?? null;
  const expect = health?.expect ?? null;
  return [
    {
      label: t("newApp.recipes.databaseLabel"),
      value: recipe.database ? t("newApp.recipes.databaseValue", { engine: recipe.database }) : t("newApp.recipes.none"),
      mono: false,
      copy: false,
    },
    {
      label: t("newApp.recipes.persistentLabel"),
      value: recipe.persistent_paths.length > 0 ? joinList(recipe.persistent_paths, t.locale) : t("newApp.recipes.none"),
      mono: recipe.persistent_paths.length > 0,
      copy: false,
    },
    ...(health !== null
      ? [
          {
            label: t("newApp.recipes.health"),
            value: expect !== null ? t("newApp.recipes.healthExpect", { path: health.path, expect }) : t("newApp.recipes.healthValue", { path: health.path }),
            copy: false as const,
          },
        ]
      : []),
    {
      label: t("newApp.recipes.deploys"),
      value: t(recipe.layout === "inplace" ? "newApp.recipes.inplace" : "newApp.recipes.releases"),
      mono: false,
      copy: false,
    },
    ...(recipe.requires.length > 0
      ? [
          {
            label: t("newApp.recipes.requires"),
            value: (
              <ul className="flex flex-col gap-0.5">
                {recipe.requires.map((need) => (
                  <li key={need}>{need}</li>
                ))}
              </ul>
            ),
            mono: false,
            copy: false as const,
          },
        ]
      : []),
  ];
}

/** What is about to be deployed from a recipe, one fact per row. */
export function recipeSummary(t: T, recipe: Recipe, form: RecipeForm): KeyValueItem[] {
  const set = Object.values(form.env).filter((value) => value.trim() !== "").length;
  return [
    { label: t("newApp.recipes.recipe"), value: recipe.title, mono: false, copy: false, hint: recipe.description },
    addressItem(t, form),
    ...setupItems(t, recipe).slice(0, 2),
    {
      label: t("newApp.deploy.environment"),
      value: set > 0 ? t("newApp.recipes.summaryVariables", { count: set }) : t("newApp.recipes.summaryGenerated"),
      mono: false,
      copy: false,
    },
  ];
}

export interface RecipeReviewProps {
  recipe: Recipe;
  types: readonly AppTypeOption[];
  form: RecipeForm;
  errors: ReviewErrors;
  onChange: (form: RecipeForm) => void;
  onBack: () => void;
  onContinue: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}

/**
 * Step two for a recipe: where it answers, and what comes with it. The database, the
 * persistent paths and the health check are the recipe's and shown as such; the generated
 * variables are listed, never asked; the others can be given a value over the recipe's.
 */
export function RecipeReview({ recipe, types, form, errors, onChange, onBack, onContinue, headingRef }: RecipeReviewProps) {
  const t = useT();
  const dns = useDomainDnsCheck(form.domain);
  const generated = generatedVariables(recipe);
  const editable = editableVariables(recipe);
  const source = sourceItem(t, recipe);
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    dns.checkNow();
    onContinue();
  };

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 ref={headingRef} tabIndex={-1} className="title text-18 text-fg outline-none">
          {t("newApp.review.heading")}
        </h2>
        <p className="text-14 text-pretty text-fg-muted">{t("newApp.recipes.reviewIntro", { title: recipe.title })}</p>
      </header>

      <section aria-label={t("newApp.recipes.recipe")} className="flex flex-col gap-2 rounded-card border border-border bg-surface px-4 py-3 shadow-raised">
        <KeyValueList
          items={[
            { label: t("newApp.recipes.recipe"), value: recipe.title, mono: false, copy: false, hint: recipe.description },
            ...(source !== null ? [source] : []),
            ...(recipe.app_type ? [{ label: t("newApp.recipes.type"), value: typeName(types, recipe.app_type), mono: false, copy: false as const }] : []),
          ]}
        />
        <ExternalLink href={recipe.homepage} className="text-12">
          {recipe.homepage}
        </ExternalLink>
      </section>

      <ReviewGroup title={t("newApp.review.address")} description={t("newApp.review.addressDescription")}>
        <AddressFields value={form} onChange={(patch) => onChange({ ...form, ...patch })} error={errors["domain"]} dns={dns} />
      </ReviewGroup>

      <ReviewGroup title={t("newApp.recipes.setsUp")} description={t("newApp.recipes.setsUpDescription")}>
        <KeyValueList items={setupItems(t, recipe)} />
      </ReviewGroup>

      <ReviewGroup title={t("newApp.recipes.environment")} description={t("newApp.review.environmentDescription")}>
        <div className="flex flex-col gap-5">
          {recipe.env.length === 0 ? <p className="text-13 text-fg-muted">{t("newApp.recipes.noVariables")}</p> : null}
          {generated.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <span className="flex items-center gap-2 text-13 font-medium text-fg">
                <KeyRound aria-hidden="true" className="size-3.5 text-fg-muted" />
                {t("newApp.recipes.generated")}
              </span>
              <p className="text-12 text-pretty text-fg-muted">{t("newApp.recipes.generatedDescription")}</p>
              <ul className="flex flex-wrap gap-1.5">
                {generated.map((name) => (
                  <li key={name}>
                    <code translate="no" className="rounded-[4px] border border-border bg-bg-sunken px-1.5 py-0.5 text-12 text-fg">
                      {name}
                    </code>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {editable.length === 0 && generated.length > 0 ? <p className="text-13 text-fg-muted">{t("newApp.recipes.allGenerated")}</p> : null}
          {editable.map((name) => (
            <Field
              key={name}
              optional
              label={
                <code translate="no" className="text-12 font-medium text-fg">
                  {name}
                </code>
              }
              description={t("newApp.recipes.overridesDescription")}
            >
              <Input
                mono
                value={form.env[name] ?? ""}
                onValueChange={(value: string) => onChange({ ...form, env: { ...form.env, [name]: value } })}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
              />
            </Field>
          ))}
        </div>
      </ReviewGroup>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-6">
        <Button onClick={onBack}>{t("newApp.review.back")}</Button>
        <Button type="submit" variant="primary">
          {t("newApp.review.continue")}
        </Button>
      </div>
    </form>
  );
}
