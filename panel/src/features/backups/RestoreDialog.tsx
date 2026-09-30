import { useId, useRef, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";

export interface RestoreRequest {
  /** Where to restore: the backup's own domain, or another the operator typed. */
  targetDomain: string;
  restoreEnv: boolean;
  /** Only asked for a local backup: its checksum is checked before anything is replaced. */
  verify: boolean;
}

export interface RestoreDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** What will be replaced, and what is lost. */
  description: ReactNode;
  /** The backup, named: its id and when it was made. */
  source: ReactNode;
  /** The domain the backup belongs to, when known: the target it proposes. */
  domain: string;
  /** Offers the integrity check first (a local backup's checksum). */
  offerVerify: boolean;
  /** The .env option's help: from this archive, or from the one downloaded. */
  envDescription: string;
  pending: boolean;
  error: unknown;
  onRestore: (request: RestoreRequest) => void;
  /** Forgets a previous attempt's failure when the dialog closes. */
  onReset: () => void;
}

/**
 * Putting a backup back over an application: data written since is lost for good, so the
 * operator types the target's name (friction "type", docs/DESIGN.md 6.2). The name to type is
 * the target itself, which doubles as where to restore: a backup may be replayed onto another
 * domain than the one it came from. One dialog for a local backup and for one on a
 * destination, which is downloaded first.
 */
export function RestoreDialog({
  open,
  onOpenChange,
  title,
  description,
  source,
  domain,
  offerVerify,
  envDescription,
  pending,
  error,
  onRestore,
  onReset,
}: RestoreDialogProps) {
  const t = useT();
  const formId = useId();
  const confirmRef = useRef<HTMLInputElement>(null);
  const [edited, setEdited] = useState<string | null>(null);
  const target = edited ?? domain;
  const [typed, setTyped] = useState("");
  const [restoreEnv, setRestoreEnv] = useState(true);
  const [verify, setVerify] = useState(true);
  const matches = target.trim() !== "" && typed === target;

  const close = (next: boolean): void => {
    if (!next && pending) return;
    onOpenChange(next);
    if (!next) {
      setEdited(null);
      setTyped("");
      setRestoreEnv(true);
      setVerify(true);
      onReset();
    }
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!matches || pending) return;
    onRestore({ targetDomain: target, restoreEnv, verify: offerVerify && verify });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={title}
      description={description}
      {...(domain !== "" ? { initialFocus: confirmRef } : {})}
      footer={
        <>
          <Button disabled={pending} onClick={() => close(false)}>
            {t("backups.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="danger" disabled={!matches} loading={pending}>
            {t("backups.restoreDialog.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-5">
        <p className="text-13 text-fg-muted">{source}</p>
        <Field label={t("backups.restoreDialog.restoreInto")} description={t("backups.restoreDialog.restoreIntoDescription")}>
          <Input mono value={target} onValueChange={setEdited} autoComplete="off" autoCapitalize="off" spellCheck={false} disabled={pending} />
        </Field>
        <div className="flex flex-col gap-3">
          <Checkbox checked={restoreEnv} onCheckedChange={setRestoreEnv} label={t("backups.restoreDialog.restoreEnv")} description={envDescription} />
          {offerVerify ? (
            <Checkbox
              checked={verify}
              onCheckedChange={setVerify}
              label={t("backups.restoreDialog.verifyFirst.label")}
              description={t("backups.restoreDialog.verifyFirst.description")}
            />
          ) : null}
        </div>
        <Field
          label={t.rich("backups.restoreDialog.typeToConfirm", {
            domain: <Mono>{target.trim() !== "" ? target : t("backups.restoreDialog.domainPlaceholder")}</Mono>,
          })}
        >
          <Input ref={confirmRef} mono value={typed} onValueChange={setTyped} autoComplete="off" autoCapitalize="off" spellCheck={false} disabled={pending} />
        </Field>
        {error !== null && error !== undefined ? <ErrorBlock live compact error={error} title={t("backups.restoreDialog.error")} /> : null}
      </form>
    </Dialog>
  );
}
