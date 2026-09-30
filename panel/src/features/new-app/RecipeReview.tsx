import { KeyRound } from "lucide-react";

import type { Recipe } from "../../api/queries/recipes";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Subsection } from "../../components/page/Subsection";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { addressItem } from "./DeployStep";
import { editableVariables, generatedVariables } from "./recipe";
import type { RecipeForm } from "./recipe";
import { joinList, typeName } from "./wizard";
import type { AppTypeOption } from "./wizard";

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

/** What the recipe is, above where it answers: its name, where its code comes from, its type. */
export function RecipeFacts({ recipe, types }: { recipe: Recipe; types: readonly AppTypeOption[] }) {
  const t = useT();
  const source = sourceItem(t, recipe);
  return (
    <Card padding="none">
      <div className="flex flex-col gap-1 px-4 pt-1 pb-3">
        <KeyValueList
          items={[
            { label: t("newApp.recipes.recipe"), value: recipe.title, mono: false, copy: false, hint: recipe.description },
            ...(source !== null ? [source] : []),
            ...(recipe.app_type ? [{ label: t("newApp.recipes.type"), value: typeName(types, recipe.app_type), mono: false, copy: false as const }] : []),
          ]}
        />
        <ExternalLink href={recipe.homepage} inline unlinked="text" className="text-12">
          {recipe.homepage}
        </ExternalLink>
      </div>
    </Card>
  );
}

export interface RecipeVariablesProps {
  recipe: Recipe;
  form: RecipeForm;
  onChange: (form: RecipeForm) => void;
}

/**
 * A recipe's variables and what else it sets up: the database, the folders it keeps, its
 * startup check are the recipe's and shown as such; the generated variables are listed, never
 * asked; the others can be given a value over the recipe's.
 */
export function RecipeVariables({ recipe, form, onChange }: RecipeVariablesProps) {
  const t = useT();
  const generated = generatedVariables(recipe);
  const editable = editableVariables(recipe);
  return (
    <div className="flex flex-col gap-6">
      <Subsection title={t("newApp.recipes.setsUp")} description={t("newApp.recipes.setsUpDescription")}>
        <Card padding="none">
          <div className="px-4 py-1">
            <KeyValueList items={setupItems(t, recipe)} />
          </div>
        </Card>
      </Subsection>

      <Subsection title={t("newApp.recipes.environment")} description={t("newApp.review.environmentDescription")}>
        <div className="flex flex-col gap-5">
          {recipe.env.length === 0 ? <p className="text-13 text-fg-muted">{t("newApp.recipes.noVariables")}</p> : null}
          {generated.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <p className="flex items-center gap-2 text-13 font-medium text-fg">
                <KeyRound aria-hidden="true" className="size-icon-sm text-fg-muted" />
                {t("newApp.recipes.generated")}
              </p>
              <p className="text-12 text-pretty text-fg-muted">{t("newApp.recipes.generatedDescription")}</p>
              <ul aria-label={t("newApp.recipes.generated")} className="flex flex-wrap gap-1.5">
                {generated.map((name) => (
                  <li key={name}>
                    <Badge mono>{name}</Badge>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {editable.length === 0 && generated.length > 0 ? <p className="text-13 text-fg-muted">{t("newApp.recipes.allGenerated")}</p> : null}
          {editable.map((name) => (
            <Field key={name} optional label={<Mono>{name}</Mono>} description={t("newApp.recipes.overridesDescription")}>
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
      </Subsection>
    </div>
  );
}
