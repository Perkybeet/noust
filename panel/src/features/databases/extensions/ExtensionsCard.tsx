import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackageCheck, Plus } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import { extensionKeys, extensionsQuery } from "../../../api/queries/databaseExtensions";
import type { DatabaseExtension } from "../../../api/queries/databaseExtensions";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { Mono } from "../../../components/ui/Mono";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { ICONS } from "../../../components/ui/icons";

/** Loading one: which, and whether to load what it needs too. */
function AddDialog({ engine, name, offered, loaded, onOpenChange }: { engine: string; name: string; offered: DatabaseExtension[]; loaded: Set<string>; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<string | null>(null);
  const [cascade, setCascade] = useState(false);
  const [pressed, setPressed] = useState(false);
  const picked = offered.find((item) => item.name === chosen);
  const needs = (picked?.requires ?? []).filter((item) => !loaded.has(item));
  const add = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/{engine}/{name}/extensions", { params: { engine, name }, body: { name: chosen ?? "", cascade } }),
    onSuccess: (info) => {
      void queryClient.invalidateQueries({ queryKey: extensionKeys.list(engine, name) });
      toast.success(t("databases.extensions.loadedToast", { extension: info.name, database: name }));
      onOpenChange(false);
    },
  });
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      size="md"
      title={t("databases.extensions.addTitle", { database: name })}
      description={t("databases.extensions.addDescription")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.confirmDialog.cancel")}</Button>
          <Button
            variant="primary"
            loading={add.isPending}
            onClick={() => {
              // Pressing says what is missing under the field that lacks it.
              if (picked === undefined || (needs.length > 0 && !cascade)) setPressed(true);
              else add.mutate();
            }}
          >
            {t("databases.extensions.addAction")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field
          label={t("databases.extensions.extension")}
          nativeLabel={false}
          description={picked !== undefined && picked.comment !== "" ? picked.comment : undefined}
          error={pressed && picked === undefined ? t("databases.extensions.chooseFirst") : undefined}
        >
          <Select
            value={chosen}
            placeholder={t("databases.extensions.choose")}
            onValueChange={setChosen}
            options={offered.map((item) => ({
              value: item.name,
              label: item.name,
              hint: item.trusted ? item.default_version : `${item.default_version} · ${t("databases.extensions.serverOnly")}`,
            }))}
          />
        </Field>
        {picked !== undefined && !picked.trusted ? (
          <Notice title={t("databases.extensions.notTrustedTitle")}>{t("databases.extensions.notTrustedBody")}</Notice>
        ) : null}
        {needs.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <Checkbox checked={cascade} onCheckedChange={setCascade} label={t("databases.extensions.cascade", { needs: needs.join(", ") })} />
            {pressed && !cascade ? <InlineMessage tone="error">{t("databases.extensions.cascadeFirst")}</InlineMessage> : null}
          </div>
        ) : null}
        {add.isError ? <ErrorBlock live compact error={add.error} title={t("databases.extensions.addFailed")} /> : null}
      </div>
    </Dialog>
  );
}

/**
 * The PostgreSQL extensions of a database: which are loaded, load another from what the server
 * offers (its own list: the host's packages, an instance's image), unload one. Loading an
 * extension that is not trusted runs its script with the superuser's rights, so a team does not
 * do it; the server says so and the card says why.
 */
export function ExtensionsCard({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery(extensionsQuery(engine, name));
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<DatabaseExtension | null>(null);
  const [cascade, setCascade] = useState(false);
  const all = query.data?.extensions ?? [];
  const loaded = all.filter((item) => item.installed);
  const names = new Set(loaded.map((item) => item.name));
  const offered = all.filter((item) => !item.installed);

  return (
    <Card
      icon={PackageCheck}
      title={t("databases.extensions.title")}
      level={2}
      description={t("databases.extensions.description")}
      padding="sm"
      actions={
        <Button size="sm" icon={<Plus aria-hidden="true" />} disabled={offered.length === 0} onClick={() => setAdding(true)}>
          {t("databases.extensions.add")}
        </Button>
      }
    >
      {query.isError ? (
        <ErrorBlock compact error={query.error} title={t("databases.extensions.readFailed")} onRetry={() => void query.refetch()} />
      ) : query.isPending ? (
        <Skeleton className="h-16 w-full rounded-control" />
      ) : (
        <div className="flex flex-col gap-3">
          {loaded.length === 0 ? (
            <EmptyState variant="inline" title={t("databases.extensions.none")} />
          ) : (
            <ul className="flex flex-col divide-y divide-border">
              {loaded.map((item) => (
                <li key={item.name} className="flex min-w-0 items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                  <span className="flex min-w-0 flex-col">
                    <span className="flex items-center gap-2">
                      <Mono tone="default">{item.name}</Mono>
                      <Badge mono>{item.installed_version}</Badge>
                    </span>
                    {item.comment ? <span className="truncate text-12 text-fg-muted">{item.comment}</span> : null}
                  </span>
                  <IconButton label={t("databases.extensions.removeLabel", { extension: item.name })} icon={<ICONS.delete aria-hidden="true" />} size="sm" onClick={() => setRemoving(item)} />
                </li>
              ))}
            </ul>
          )}
          {query.data.source !== "" ? <p className="text-12 text-fg-muted">{query.data.source}</p> : null}
          <CommandHint command={`noust db extension list ${name} -e ${engine} --all`} label={t("common.words.fromTerminal")} />
        </div>
      )}
      {adding ? <AddDialog engine={engine} name={name} offered={offered} loaded={names} onOpenChange={(open) => (open ? undefined : setAdding(false))} /> : null}
      {removing !== null ? (
        <ConfirmDialog
          open
          friction="simple"
          destructive
          onOpenChange={(open) => {
            if (!open) {
              setRemoving(null);
              setCascade(false);
            }
          }}
          server={node}
          title={t("databases.extensions.removeTitle", { extension: removing.name, database: name })}
          description={t("databases.extensions.removeDescription")}
          actionLabel={t("databases.extensions.removeAction")}
          onConfirm={async () => {
            await request("delete", "/api/databases/databases/{engine}/{name}/extensions/{extension}", {
              params: { engine, name, extension: removing.name },
              query: { cascade },
            });
            void queryClient.invalidateQueries({ queryKey: extensionKeys.list(engine, name) });
            toast.success(t("databases.extensions.removedToast", { extension: removing.name, database: name }));
          }}
        >
          <Checkbox checked={cascade} onCheckedChange={setCascade} label={t("databases.extensions.cascadeDrop")} description={t("databases.extensions.cascadeDropHelp")} />
        </ConfirmDialog>
      ) : null}
    </Card>
  );
}
