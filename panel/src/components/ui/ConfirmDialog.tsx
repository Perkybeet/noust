import { AlertDialog } from "@base-ui/react/alert-dialog";
import { cloneElement, useCallback, useId, useRef, useState } from "react";
import type { MouseEvent, ReactElement, ReactNode, SyntheticEvent } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { describeError } from "../../lib/errors";
import { heldOf, reportHeld } from "../../lib/held";
import { useNamedServer } from "../../nodes/pageServer";
import { ErrorBlock } from "../page/QueryState";
import { Button } from "./Button";
import { BACKDROP, DialogFrame, MODAL_POPUP, MODAL_VIEWPORT } from "./Dialog";
import { Input } from "./Input";
import { Mono } from "./Mono";
import { toast } from "./toast";

/**
 * How much the operator is asked before an action runs, in proportion to what it can destroy
 * (docs/DESIGN.md, "Actions and confirmation"):
 *
 * - `none`: reversible, or its effect is visible at once (disable a job): it just runs.
 * - `simple`: one resource that can be made again, or an interruption that loses no data
 *   (stop an app, remove a schedule, revoke a token): one question, one button.
 * - `type`: data lost for good or a wide reach (delete an application, drop a database): the
 *   resource's name must be typed, which makes the operator read what they destroy.
 */
export type ConfirmFriction = "none" | "simple" | "type";

/** How wide the question is: a sentence (`sm`), or a plan with its before and after (`md`, `lg`). */
export type ConfirmSize = "sm" | "md" | "lg";

const WIDTHS: Record<ConfirmSize, string> = {
  sm: "sm:max-w-dialog-sm",
  md: "sm:max-w-dialog-md",
  lg: "sm:max-w-dialog-lg",
};

interface ConfirmDialogBase {
  title: string;
  /** What will happen, concretely: what is stopped, deleted, kept. */
  description: ReactNode;
  /** The verb on the action button: "Delete application", not "Confirm". */
  actionLabel: string;
  /** What the other button says: "Cancel" unless the question is about leaving ("Keep editing"). */
  cancelLabel?: string;
  destructive?: boolean;
  /** Runs the action. The dialog stays open, busy, until it settles; a rejection is shown verbatim. */
  onConfirm: () => Promise<void>;
  trigger?: ReactElement<Record<string, unknown>>;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /**
   * The server the action runs on, when the console manages several: named in the dialog, so
   * nobody deletes the right application on the wrong machine.
   */
  server?: string | null;
  /**
   * Options that change what the action does ("Also delete its files") or the plan it carries
   * (an sshd change with its before and after, the ports a firewall protects): shown between the
   * question and the name to type, never inside the description. Every option that destroys more
   * starts unchecked (docs/DESIGN.md, "Danger").
   */
  children?: ReactNode;
  /**
   * False while an option in `children` still lacks what the action needs (a target to restore
   * into), or when the plan says none of it can run from here: the action stays disabled, as it
   * does until the name is typed.
   */
  ready?: boolean;
  /**
   * The width: a plan wants `md` or `lg`. The body scrolls, so a long plan still leaves the
   * buttons in view.
   */
  size?: ConfirmSize;
  /**
   * Says a refusal in its own words before it is shown (a node that does not let this console
   * change how it is reached, a rejection with a list of what blocks it). It gets what the action
   * threw and returns what to show in its place; `features/server/errors.tsx` has the one the
   * Server area uses.
   */
  explainError?: (error: unknown) => unknown;
  /**
   * What goes under the failure, beside the system's words: the command to run on the node when
   * the refusal is one of access to the host. It gets the error as `explainError` left it.
   */
  failureAction?: (error: unknown) => ReactNode;
}

export type ConfirmDialogProps = ConfirmDialogBase &
  (
    | {
        /** Defaults to `type` whenever `confirmText` is given, as every 3.0 call site does. */
        friction?: "type";
        /** The exact text the operator must type, usually the resource name. */
        confirmText: string;
      }
    | { friction: "simple"; confirmText?: undefined }
    | { friction: "none"; confirmText?: undefined }
  );

/**
 * Confirmation for an action that destroys or interrupts something, with the friction its
 * reach deserves. A failure is shown in the dialog, verbatim, and the dialog stays open. It is
 * the only confirmation in the console: a plan with a before and after is its `children` and a
 * `size`, not a second dialog.
 */
export function ConfirmDialog(props: ConfirmDialogProps) {
  if (props.friction === "none") return <Immediate {...props} />;
  return <Confirmation {...props} />;
}

/**
 * `none`: the trigger runs the action, there is nothing to open (`open` is ignored); a failure
 * is a persistent error toast with the system's words verbatim.
 */
function Immediate({ title, onConfirm, trigger, onOpenChange, explainError }: ConfirmDialogBase) {
  const t = useT();
  const [pending, setPending] = useState(false);

  // A click is a discrete event: React commits `pending` before the next one arrives, so the
  // disabled trigger (and this guard) stop a double click from running the action twice.
  const run = useCallback(async (): Promise<void> => {
    if (pending) return;
    setPending(true);
    try {
      await onConfirm();
    } catch (error: unknown) {
      // A cancelled confirmation or a request left waiting is not a failure (lib/held.ts).
      if (reportHeld(error)) return;
      const described = describeError(explainError === undefined ? error : explainError(error));
      toast.error(title, {
        description: described.hint ?? t("common.confirmDialog.failed"),
        detail: described.detail,
        ...(described.output !== null ? { output: described.output } : {}),
      });
    } finally {
      setPending(false);
      onOpenChange?.(false);
    }
  }, [pending, onConfirm, onOpenChange, t, title, explainError]);

  if (trigger === undefined) return null;
  const own = trigger.props["onClick"] as ((event: MouseEvent) => void) | undefined;
  return cloneElement(trigger, {
    onClick: (event: MouseEvent) => {
      own?.(event);
      void run();
    },
    disabled: pending || trigger.props["disabled"] === true,
    "aria-busy": pending || undefined,
  });
}

function Confirmation({
  title,
  description,
  confirmText,
  friction = "type",
  actionLabel,
  cancelLabel,
  destructive = true,
  onConfirm,
  trigger,
  open,
  onOpenChange,
  server,
  children,
  ready = true,
  size = "sm",
  explainError,
  failureAction,
}: ConfirmDialogBase & { friction?: "simple" | "type"; confirmText?: string | undefined }) {
  const t = useT();
  const [internalOpen, setInternalOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  // What the action threw, as `explainError` left it; null while there is no failure.
  const [failure, setFailure] = useState<{ error: unknown } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputId = useId();

  const typing = friction === "type" && confirmText !== undefined;
  const isOpen = open ?? internalOpen;
  // Typed names are compared trimmed: a trailing space from a paste is not a different name.
  const matches = ready && (!typing || typed.trim() === confirmText);

  const setOpen = (next: boolean): void => {
    // A running action cannot be abandoned half-way from the keyboard or backdrop.
    if (!next && pending) return;
    if (open === undefined) setInternalOpen(next);
    onOpenChange?.(next);
    if (!next) {
      setTyped("");
      setFailure(null);
    }
  };

  const submit = async (event: SyntheticEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!matches || pending) return;
    setPending(true);
    setFailure(null);
    try {
      await onConfirm();
      setPending(false);
      if (open === undefined) setInternalOpen(false);
      onOpenChange?.(false);
      setTyped("");
    } catch (error: unknown) {
      setPending(false);
      const held = heldOf(error);
      if (held === null) {
        setFailure({ error: explainError === undefined ? error : explainError(error) });
        return;
      }
      // Not a failure. A cancelled "Confirm it's you" leaves the question open, to ask again
      // or cancel; a request filed for approval is on its way, so the question is answered.
      if (held.kind !== "cancelled") {
        reportHeld(error);
        if (open === undefined) setInternalOpen(false);
        onOpenChange?.(false);
        setTyped("");
      }
    }
  };

  // On a fleet the shell names the server a page is about, this one's included.
  const shownServer = useNamedServer(server);
  const named = shownServer !== null;
  // A simple confirmation with nothing more to say has no body at all: the question and the
  // buttons, nothing between them.
  const options = children !== undefined && children !== null && children !== false;
  const body =
    named || options || typing || failure !== null ? (
      <>
        {named ? (
          <p className="mb-3 text-13 text-fg-muted">
            {t.rich("common.confirmDialog.onServer", { server: <Mono tone="default">{shownServer}</Mono> })}
          </p>
        ) : null}
        {options ? <div className={cx("flex flex-col gap-3", typing && "mb-4")}>{children}</div> : null}
        {typing ? (
          <div className="flex flex-col gap-1.5">
            <label htmlFor={inputId} className="text-13 text-fg-muted">
              {t.rich("common.confirmDialog.typeToConfirm", {
                value: (
                  <span translate="no" className="mono rounded-chip bg-bg-sunken px-1 py-0.5 text-fg select-all">
                    {confirmText}
                  </span>
                ),
              })}
            </label>
            <Input
              id={inputId}
              ref={inputRef}
              mono
              value={typed}
              onValueChange={(value: string) => setTyped(value)}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              disabled={pending}
            />
          </div>
        ) : null}
        {failure !== null ? (
          <div className={cx("flex flex-col gap-2", (typing || options) && "mt-4")}>
            <ErrorBlock compact live error={failure.error} title={t("common.confirmDialog.couldNot")} />
            {failureAction?.(failure.error)}
          </div>
        ) : null}
      </>
    ) : undefined;

  return (
    <AlertDialog.Root open={isOpen} onOpenChange={(next: boolean) => setOpen(next)}>
      {trigger !== undefined ? <AlertDialog.Trigger render={trigger} /> : null}
      <AlertDialog.Portal>
        <AlertDialog.Backdrop className={BACKDROP} />
        <AlertDialog.Viewport className={MODAL_VIEWPORT}>
          {/* Typing starts in the name field; a single question starts on Cancel, so Enter
              never destroys anything by accident. */}
          <AlertDialog.Popup initialFocus={typing ? inputRef : cancelRef} className={cx(MODAL_POPUP, WIDTHS[size])}>
            <form onSubmit={(event) => void submit(event)} className="contents">
              <DialogFrame
                title={title}
                description={description}
                Title={AlertDialog.Title}
                Description={AlertDialog.Description}
                footer={
                  <>
                    <AlertDialog.Close
                      render={
                        <Button ref={cancelRef} disabled={pending}>
                          {cancelLabel ?? t("common.confirmDialog.cancel")}
                        </Button>
                      }
                    />
                    <Button type="submit" variant={destructive ? "danger" : "primary"} disabled={!matches} loading={pending}>
                      {actionLabel}
                    </Button>
                  </>
                }
              >
                {body}
              </DialogFrame>
            </form>
          </AlertDialog.Popup>
        </AlertDialog.Viewport>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
