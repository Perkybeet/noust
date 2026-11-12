import { useState } from "react";

import type { CentralCopy } from "../../../api/queries/centralBackups";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { textOf } from "../policy/record";
import { useCentralBackupActions } from "./useCentralBackupActions";

export interface GiveBackDialogProps {
  copy: CentralCopy;
  /** The servers it may go to: the ones the caller sees. */
  servers: readonly string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The domain an application or volume copy restores to unless another is typed: the one it was made of. */
function originalDomain(copy: CentralCopy): string {
  return textOf(copy.meta, "domain") || copy.owner;
}

/**
 * Giving a copy back to a server and restoring it there, to the server it came from or to another. The central checks the
 * copy whole, the file travels through the tunnel and the server restores it with the manager of its kind, so what it
 * replaces and what it asks first are the ones of any restore. Over what is there it asks for the name to be typed; beside
 * it (another domain, another database name) it asks once, because nothing is lost.
 */
export function GiveBackDialog({ copy, servers, open, onOpenChange }: GiveBackDialogProps) {
  const t = useT();
  const { giveBack } = useCentralBackupActions();
  const [target, setTarget] = useState(copy.node);
  const [domain, setDomain] = useState(originalDomain(copy));
  const [database, setDatabase] = useState(textOf(copy.meta, "database") || copy.owner);
  const [newName, setNewName] = useState("");
  const [restoreEnv, setRestoreEnv] = useState(false);
  const [safety, setSafety] = useState(true);

  const kind = copy.kind;
  const withDomain = kind === "app" || kind === "volume";
  const withDatabase = kind === "database";
  const beside = (withDomain && domain.trim() !== originalDomain(copy)) || (withDatabase && newName.trim() !== "");
  const ready = (!withDomain || domain.trim() !== "") && (!withDatabase || database.trim() !== "");
  const names = servers.includes(target) ? servers : [...servers, target];

  const submit = async (): Promise<void> => {
    const place: Record<string, string> = {};
    if (withDomain) place["domain"] = domain.trim();
    if (withDatabase) {
      place["database"] = database.trim();
      if (newName.trim() !== "") place["new_name"] = newName.trim();
    }
    const options: Record<string, boolean> = {};
    if (kind === "app") options["restore_env"] = restoreEnv;
    if (kind === "volume" || kind === "database") options["safety_backup"] = safety;
    // design-exception: write-without-permission the dialog is opened only from a row drawn for backups.manage, and the server decides again
    await giveBack.mutateAsync({
      node: copy.node,
      kind,
      owner: copy.owner,
      server: target,
      body: { item: copy.id, target_node: target, target: place, options },
    });
  };

  const common = {
    open,
    onOpenChange,
    title: t("centralBackups.give.title", { owner: copy.owner }),
    description: (
      // The dialog draws its description inside a paragraph: spans, never paragraphs in it.
      <>
        <span className="block">{t("centralBackups.give.description")}</span>
        <span className="mt-2 block">
          {t.rich("centralBackups.give.source", {
            owner: <Mono tone="default">{copy.owner}</Mono>,
            when: <RelativeTime value={copy.created_at} />,
            server: <Mono tone="default">{copy.node}</Mono>,
          })}
        </span>
        {kind === "noust" ? <span className="mt-2 block">{t("centralBackups.give.noust")}</span> : null}
      </>
    ),
    actionLabel: t("centralBackups.give.action"),
    destructive: !beside && kind !== "noust",
    ready,
    size: "md" as const,
    onConfirm: submit,
  };

  const body = (
    <div className="flex flex-col gap-4">
      <Field label={t("centralBackups.give.target")} nativeLabel={false} {...(target !== copy.node ? { description: t("centralBackups.give.otherServer") } : {})}>
        <Select
          value={target}
          onValueChange={setTarget}
          options={names.map((name) => ({ value: name, label: name === copy.node ? t("centralBackups.give.targetSame", { server: name }) : name }))}
        />
      </Field>
      {withDomain ? (
        <Field label={t("centralBackups.give.domain")} description={t("centralBackups.give.domainHelp")}>
          <Input mono value={domain} onValueChange={setDomain} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
      ) : null}
      {withDatabase ? (
        <>
          <Field label={t("centralBackups.give.database")} description={t("centralBackups.give.databaseHelp")}>
            <Input mono value={database} onValueChange={setDatabase} autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
          <Field label={t("centralBackups.give.newName")} description={t("centralBackups.give.newNameHelp")} optional>
            <Input mono value={newName} onValueChange={setNewName} autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
        </>
      ) : null}
      {kind === "app" ? <Checkbox checked={restoreEnv} onCheckedChange={setRestoreEnv} label={t("centralBackups.give.env")} description={t("centralBackups.give.envHelp")} /> : null}
      {kind === "volume" || kind === "database" ? <Checkbox checked={safety} onCheckedChange={setSafety} label={t("centralBackups.give.safety")} description={t("centralBackups.give.safetyHelp")} /> : null}
    </div>
  );

  // Over what is there it asks for the name to be typed; beside it, or a copy of Noust's own state that is only put there, once.
  return kind === "noust" || beside ? (
    <ConfirmDialog friction="simple" {...common}>
      {body}
    </ConfirmDialog>
  ) : (
    <ConfirmDialog friction="type" confirmText={withDatabase ? database.trim() || copy.owner : withDomain ? domain.trim() || copy.owner : copy.owner} {...common}>
      {body}
    </ConfirmDialog>
  );
}
