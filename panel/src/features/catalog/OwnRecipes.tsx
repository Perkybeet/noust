import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { recipeKeys } from "../../api/queries/recipes";
import type { PrivateRecipeResult } from "../../api/queries/recipes";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Textarea } from "../../components/ui/Textarea";
import { useT } from "../../i18n";

export interface OwnRecipesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The shape of a recipe, for an empty field: the least a recipe says. */
const EXAMPLE = ["name: my-app", "title: My app", "description: What it does.", "homepage: https://example.org", "kind: native", "# ... see docs/recipes.md"].join("\n");

/**
 * A recipe of the server's own (`noust recipe check|add`, `POST /api/recipes/private[/check]`):
 * the YAML typed or pasted here is checked against the catalogue's schema and lint without
 * adding anything, then added to the server's recipes, where it is installed like any other and
 * kept apart from the release's. The server's refusal names the field and is shown verbatim.
 */
export function OwnRecipesDialog({ open, onOpenChange }: OwnRecipesDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [document, setDocument] = useState("");
  const [replace, setReplace] = useState(false);
  const [checked, setChecked] = useState<PrivateRecipeResult | null>(null);

  const send = useMutation({
    mutationFn: ({ add }: { add: boolean }) =>
      request("post", add ? "/api/recipes/private" : "/api/recipes/private/check", { body: { document, assets: {}, replace } }),
    onSuccess: (result, { add }) => {
      if (!add) {
        setChecked(result);
        return;
      }
      void queryClient.invalidateQueries({ queryKey: recipeKeys.all });
      announce(t("catalog.own.added", { title: result.recipe.title }));
      onOpenChange(false);
      setDocument("");
      setChecked(null);
    },
  });

  const edit = (value: string): void => {
    setDocument(value);
    setChecked(null);
    if (send.isError) send.reset();
  };
  const busy = send.isPending;
  const empty = document.trim() === "";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) send.reset();
      }}
      size="lg"
      title={t("catalog.own.title")}
      description={t("catalog.own.description")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button disabled={empty || busy} loading={busy && !send.variables.add} onClick={() => send.mutate({ add: false })}>
            {t("catalog.own.check")}
          </Button>
          <Button variant="primary" disabled={empty || busy} loading={busy && send.variables.add} onClick={() => send.mutate({ add: true })}>
            {t("catalog.own.add")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t("catalog.own.documentLabel")} description={t("catalog.own.documentDescription")}>
          <Textarea mono rows={14} wrap="off" autoComplete="off" spellCheck={false} placeholder={EXAMPLE} value={document} onChange={(event) => edit(event.target.value)} />
        </Field>
        <Checkbox label={t("catalog.own.replace")} description={t("catalog.own.replaceDescription")} checked={replace} onCheckedChange={setReplace} />
        {checked !== null ? (
          <Notice tone="success" title={t("catalog.own.valid", { title: checked.recipe.title })} live>
            {t.rich("catalog.own.validBody", { name: <Mono>{checked.recipe.name}</Mono> })}
          </Notice>
        ) : null}
        {send.isError ? <ErrorBlock live compact error={send.error} title={t("catalog.own.refused")} /> : null}
        <CommandHint command="noust recipe add FILE" label={t("common.words.fromTerminal")} />
      </div>
    </Dialog>
  );
}
