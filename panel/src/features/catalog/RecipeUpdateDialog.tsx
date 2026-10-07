import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { recipeQuery } from "../../api/queries/recipes";
import type { InstalledRecipe } from "../../api/queries/recipes";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { requiredVariables, variableNamed } from "../new-app/recipe";
import { versionLabel } from "./model";

/** What the server answers when it queues an update: the job to follow. */
export interface QueuedUpdate {
  job_id: string;
  job?: unknown;
}

export interface RecipeUpdateDialogProps {
  /** The installed recipe to update; null closes the dialog. */
  item: InstalledRecipe | null;
  onOpenChange: (open: boolean) => void;
  /** The update was queued: the page follows its job. */
  onQueued: (accepted: QueuedUpdate, item: InstalledRecipe) => void;
}

/**
 * Updates an application to its recipe's current version. It is the one update: the server makes
 * its pre-update copy (a Compose stack's databases included), builds, checks that the new
 * version answers and goes back to the one that served when it does not, so the dialog says what
 * changes and what happens if it fails, and asks only for what the new version cannot do
 * without: a variable it declares that the application lacks. The variables the application has
 * are never reset. A refusal (a variable the application lacks and no value was given) is shown
 * verbatim and the dialog stays open.
 */
export function RecipeUpdateDialog({ item, onOpenChange, onQueued }: RecipeUpdateDialogProps) {
  const t = useT();
  const recipe = useQuery({ ...recipeQuery(item?.recipe ?? ""), enabled: item !== null });
  const [values, setValues] = useState<Record<string, string>>({});
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  // A different application gets a clean form: what was typed for one never reaches another.
  if ((item?.domain ?? null) !== openedFor) {
    setOpenedFor(item?.domain ?? null);
    setValues({});
  }

  const update = useMutation({
    mutationFn: (target: InstalledRecipe) => {
      const given = Object.fromEntries(Object.entries(values).filter(([, value]) => value.trim() !== ""));
      return request("post", "/api/recipes/installed/{domain}/update", { params: { domain: target.domain }, body: { variables: given, force: false } });
    },
    onSuccess: (accepted, target) => {
      onQueued(accepted, target);
      onOpenChange(false);
    },
  });

  const asked = recipe.data !== undefined ? requiredVariables(recipe.data) : [];
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (item !== null && !update.isPending) update.mutate(item);
  };

  return (
    <Dialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next && update.isPending) return;
        if (!next) update.reset();
        onOpenChange(next);
      }}
      size="md"
      title={item !== null ? t("catalog.update.title", { domain: item.domain }) : ""}
      description={
        item !== null
          ? t("catalog.update.description", {
              from: versionLabel(item.installed_version, item.installed_revision) ?? item.installed_version,
              to: versionLabel(item.latest_version, item.latest_revision) ?? item.latest_version ?? "",
            })
          : undefined
      }
      footer={
        <>
          <Button disabled={update.isPending} onClick={() => onOpenChange(false)}>
            {t("catalog.update.cancel")}
          </Button>
          <Button type="submit" form="recipe-update" variant="primary" loading={update.isPending}>
            {t("catalog.update.action")}
          </Button>
        </>
      }
    >
      {item === null ? null : (
        <form id="recipe-update" onSubmit={submit} noValidate className="flex flex-col gap-5">
          {item.changes.length > 0 ? (
            <div className="flex flex-col gap-2">
              <p className="text-13 font-medium text-fg">{t("catalog.update.changes")}</p>
              <ol aria-label={t("catalog.update.changes")} className="flex flex-col gap-1.5 text-13 text-pretty text-fg">
                {item.changes.map((change) => (
                  <li key={change.version} className="flex gap-2">
                    <Mono className="shrink-0">{change.version}</Mono>
                    <span>{change.summary}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <p className="text-13 text-fg-muted">{t("catalog.update.noChanges")}</p>
          )}

          <Notice title={t("catalog.update.safeTitle")}>{t("catalog.update.safe")}</Notice>

          {recipe.isPending ? (
            <div aria-busy="true">
              <span className="sr-only">{t("catalog.update.reading")}</span>
              <Skeleton className="h-9 w-full" />
            </div>
          ) : recipe.isError ? null : asked.length > 0 ? (
            <div className="flex flex-col gap-4">
              <p className="text-13 text-pretty text-fg-muted">{t("catalog.update.variables")}</p>
              {asked.map((name) => {
                const variable = variableNamed(recipe.data, name);
                const description = variable?.description ?? "";
                return (
                  <Field key={name} optional label={<Mono>{name}</Mono>} description={description !== "" ? description : t("catalog.update.variableHelp")}>
                    <Input
                      mono
                      type={variable?.secret === true ? "password" : "text"}
                      value={values[name] ?? ""}
                      onValueChange={(value: string) => setValues({ ...values, [name]: value })}
                      autoComplete={variable?.secret === true ? "new-password" : "off"}
                      autoCapitalize="off"
                      spellCheck={false}
                    />
                  </Field>
                );
              })}
            </div>
          ) : null}

          {update.isError ? (
            <ErrorBlock live compact error={update.error} title={t("catalog.update.failed", { domain: item.domain })} />
          ) : null}
        </form>
      )}
    </Dialog>
  );
}
