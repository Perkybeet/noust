import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { catalogStateQuery, recipeKeys } from "../../api/queries/recipes";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { formatDate } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { olderServer } from "../app/settings/queries";

export interface CatalogPackageDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * The catalogue between releases (`noust recipe catalog`, `/api/recipes/catalog`): which signed
 * package of recipes is in place, who signed it, and the two things an operator does with it:
 * fetch a newer one over https (a key the server trusts must have signed it, every file is
 * checked against the digest the signature covers, and nothing in it runs) or go back to the
 * recipes the release ships. It is never fetched on its own: nothing here polls.
 */
export function CatalogPackageDialog({ open, onOpenChange }: CatalogPackageDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const state = useQuery({ ...catalogStateQuery(), enabled: open });
  const [url, setUrl] = useState("");
  const [force, setForce] = useState(false);
  const [removing, setRemoving] = useState(false);

  const fetchPackage = useMutation({
    mutationFn: () => request("post", "/api/recipes/catalog/fetch", { body: { url: url.trim(), force } }),
    onSuccess: (report) => {
      void queryClient.invalidateQueries({ queryKey: recipeKeys.all });
      announce(t("catalog.package.fetched", { serial: report.package.serial }));
    },
  });

  const body = (() => {
    if (state.isPending) return <Skeleton className="h-24 w-full rounded-control" />;
    if (state.isError && olderServer(state.error)) return <Notice title={t("catalog.package.older")} />;
    if (state.isError) return <ErrorBlock compact error={state.error} title={t("catalog.package.readFailed")} onRetry={() => void state.refetch()} retrying={state.isRefetching} />;
    const { package: current, trusted_keys: keys } = state.data;
    return (
      <div className="flex flex-col gap-4">
        {current ? (
          <KeyValueList
            items={[
              { label: t("catalog.package.serial"), value: String(current.serial) },
              { label: t("catalog.package.signer"), value: current.signer },
              { label: t("catalog.package.created"), value: formatDate(new Date(current.created), {}, t.locale), mono: false, copy: false },
              { label: t("catalog.package.recipes"), value: String(Object.keys(current.recipes).length), mono: false, copy: false },
            ]}
          />
        ) : (
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("catalog.package.none")}</p>
        )}
        <p className="max-w-measure text-12 text-pretty text-fg-muted" data-testid="trusted-keys">
          {keys.length > 0 ? t("catalog.package.trusted", { keys: keys.join(", ") }) : t("catalog.package.noKeys")}
        </p>
        {current ? (
          <Button size="sm" variant="ghost" className="self-start" onClick={() => setRemoving(true)}>
            {t("catalog.package.remove")}
          </Button>
        ) : null}
      </div>
    );
  })();

  const report = fetchPackage.data;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) fetchPackage.reset();
      }}
      size="lg"
      title={t("catalog.package.title")}
      description={t("catalog.package.description")}
      footer={<Button onClick={() => onOpenChange(false)}>{t("common.dialog.close")}</Button>}
    >
      <div className="flex flex-col gap-5">
        {body}
        <form
          className="flex flex-col gap-3"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            if (url.trim() !== "") fetchPackage.mutate();
          }}
        >
          <Field label={t("catalog.package.urlLabel")} description={t("catalog.package.urlDescription")}>
            <Input mono type="url" value={url} onValueChange={setUrl} autoComplete="off" spellCheck={false} placeholder="https://example.org/recipes.tar.gz" />
          </Field>
          <Checkbox label={t("catalog.package.force")} description={t("catalog.package.forceDescription")} checked={force} onCheckedChange={setForce} />
          <Button type="submit" variant="primary" className="self-start" loading={fetchPackage.isPending} disabled={url.trim() === ""}>
            {t("catalog.package.fetch")}
          </Button>
        </form>
        {fetchPackage.isError ? <ErrorBlock live compact error={fetchPackage.error} title={t("catalog.package.refused")} /> : null}
        {report ? (
          <Notice tone="success" title={t("catalog.package.imported", { serial: report.package.serial })} live>
            <ul className="flex flex-col gap-0.5">
              <li>{t("catalog.package.added", { count: report.added.length })}</li>
              <li>{t("catalog.package.updated", { count: report.updated.length })}</li>
              <li>{t("catalog.package.ignored", { count: report.ignored.length })}</li>
              {report.clashes.length > 0 ? <li>{t("catalog.package.clashes", { names: report.clashes.join(", ") })}</li> : null}
            </ul>
          </Notice>
        ) : null}
        <CommandHint command="noust recipe catalog import FILE" label={t("common.words.fromTerminal")} />
      </div>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        server={node}
        title={t("catalog.package.removeTitle")}
        description={t("catalog.package.removeDescription")}
        actionLabel={t("catalog.package.removeAction")}
        onConfirm={async () => {
          await request("delete", "/api/recipes/catalog", {});
          void queryClient.invalidateQueries({ queryKey: recipeKeys.all });
          announce(t("catalog.package.removed"));
        }}
      />
    </Dialog>
  );
}
