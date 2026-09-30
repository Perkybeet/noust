/**
 * A confirmation whose question needs more than a sentence: an sshd fix with its before and
 * after, a firewall change with the ports it protects, a cleanup with what it deletes.
 *
 * It keeps ConfirmDialog's rules (docs/DESIGN.md 6.2): `simple` asks once and opens on Cancel,
 * so Enter never acts by accident; `type` asks for the host name, which makes the operator read
 * which server they are about to change; the server is named on a fleet; a failure stays in the
 * dialog, verbatim, and the dialog stays open. The body scrolls, so a long plan still fits.
 */

import { useId, useRef, useState } from "react";
import type { ReactNode } from "react";

import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { ServerErrorBlock } from "./errors";

export interface ActionDialogProps {
  title: string;
  /** One sentence: what happens. */
  description: string;
  /** The plan: what changes, what it protects, what it deletes. */
  children?: ReactNode;
  /** The verb and its object: "Turn on the firewall". */
  actionLabel: string;
  /** For what takes something away (the danger button); a fix or an install is not. */
  destructive?: boolean;
  /** Typing friction: the text to type, the host name. Absent: one question. */
  confirmText?: string | undefined;
  /** Runs the action; the dialog closes when it resolves and shows the error when it rejects. */
  onConfirm: () => Promise<void>;
  onClose: () => void;
  size?: "sm" | "md" | "lg";
  /** Nothing to do (the plan says none of it can run from here): the action cannot be pressed. */
  disabled?: boolean;
}

export function ActionDialog({ title, description, children, actionLabel, destructive = false, confirmText, onConfirm, onClose, size = "md", disabled = false }: ActionDialogProps) {
  const t = useT();
  const { node } = useNode();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const formId = useId();
  const typing = confirmText !== undefined && confirmText !== "";
  const matches = !typing || typed.trim() === confirmText;

  const submit = async (): Promise<void> => {
    if (!matches || pending || disabled) return;
    setPending(true);
    setFailure(null);
    try {
      await onConfirm();
      setPending(false);
      onClose();
    } catch (error: unknown) {
      setPending(false);
      setFailure(error);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
      size={size}
      title={title}
      description={description}
      initialFocus={typing ? inputRef : cancelRef}
      footer={
        <>
          <Button ref={cancelRef} disabled={pending} onClick={onClose}>
            {t("server.dialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant={destructive ? "danger" : "primary"} disabled={!matches || disabled} loading={pending}>
            {actionLabel}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {node !== null ? <p className="text-13 text-fg-muted">{t.rich("server.dialog.onServer", { server: <Mono>{node}</Mono> })}</p> : null}
        {children}
        {typing ? (
          <Field label={t.rich("server.dialog.typeToConfirm", { value: <Mono>{confirmText}</Mono> })}>
            <Input ref={inputRef} mono value={typed} onValueChange={(value: string) => setTyped(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} disabled={pending} />
          </Field>
        ) : null}
        {failure !== null ? <ServerErrorBlock live compact error={failure} title={t("server.dialog.failed")} /> : null}
      </form>
    </Dialog>
  );
}
