import { useState } from "react";
import type { ReactNode } from "react";

import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Field } from "../ui/Field";
import { Input } from "../ui/Input";
import { Select } from "../ui/Select";

export type RestoreTarget = "replace" | "new";

export interface RestoreChoice {
  /** Over what exists now, or into a new place. */
  target: RestoreTarget;
  /** The name it goes to: what exists now when it replaces, what was typed for a new place. */
  name: string;
}

/**
 * The words of one restore, which the caller says in its own catalog: the dialog is the same for an
 * application and for a database and knows neither word, so a third thing that is restored needs a
 * set of words and no dialog.
 */
export interface RestoreWords {
  /** The label of the choice: "Restore into". */
  into: string;
  intoNew: string;
  intoNewHint: string;
  /** The choice of replacing, naming what is replaced: "shop.example.com, replacing what it holds". */
  intoReplace: string;
  intoReplaceHint: string;
  /** What replacing destroys and what is kept before it does: a sentence that names both. */
  replaceNote: ReactNode;
  /** The field of the new place's name. */
  newName: string;
  newNameHelp: string;
  nameInvalid: string;
  /** The verbs of the two buttons: "Restore over shop.example.com", "Restore into a new one". */
  actionReplace: string;
  actionNew: string;
}

export interface RestoreDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** The backup, named: what it is and when it was made, or where it is. */
  source: ReactNode;
  /** The server it runs on, when the console manages several: said in the dialog. */
  server?: string | null;
  /** What exists now and would be replaced, or null when nothing does (a server that never had it). */
  existing: string | null;
  /** Where it goes unless the person says otherwise: a new place is the one that touches nothing. */
  defaultTarget: RestoreTarget;
  /** A new place cannot be chosen (a store with one instance). */
  replaceOnly?: boolean;
  /** The name proposed for a new place; the person's own typing wins over it. */
  suggestion?: string;
  /** Whether a name for a new place is acceptable, as the server will judge it. */
  isValidName?: (name: string) => boolean;
  words: RestoreWords;
  /**
   * Options that change what the restore does, each starting unchecked: the dialog's children. A
   * function says them for where the restore goes, since an option may only make sense over what exists.
   */
  options?: ReactNode | ((target: RestoreTarget) => ReactNode);
  /** The caller has a reason to hold the action (an option still lacks what it needs). */
  held?: boolean;
  /** Starts the restore; a rejection is shown in the dialog, verbatim, and it stays open. */
  onRestore: (choice: RestoreChoice) => Promise<void>;
}

/**
 * Putting a backup back, for anything that is backed up (docs/DESIGN.md, DB-1): the one confirmation
 * of a restore. Into a new place by default where that is the safe thing, which touches nothing that
 * exists and asks one question; over what exists now, which says what is replaced and what is kept
 * first and asks for its name to be typed, because what it holds now is gone for good. The words are
 * the caller's (`RestoreWords`) and its own options are the dialog's children, so one component serves
 * an application, a database and whatever is restored next.
 */
export function RestoreDialog({
  open,
  onOpenChange,
  title,
  source,
  server = null,
  existing,
  defaultTarget,
  replaceOnly = false,
  suggestion = "",
  isValidName = (name) => name.trim() !== "",
  words,
  options,
  held = false,
  onRestore,
}: RestoreDialogProps) {
  // Nothing exists to replace: the only place to go is a new one. Nothing new can be made: replace.
  const forced: RestoreTarget | null = existing === null ? "new" : replaceOnly ? "replace" : null;
  const [chosen, setChosen] = useState<RestoreTarget>(defaultTarget);
  const [typed, setTyped] = useState<string | null>(null);
  const target = forced ?? chosen;
  const replacing = target === "replace" && existing !== null;
  const name = replacing ? existing : (typed ?? suggestion).trim();
  const nameOk = replacing || isValidName(name);

  return (
    <ConfirmDialog
      {...(replacing ? { friction: "type" as const, confirmText: existing } : { friction: "simple" as const })}
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setChosen(defaultTarget);
          setTyped(null);
        }
      }}
      size="md"
      title={title}
      description={source}
      {...(server !== null ? { server } : {})}
      actionLabel={replacing ? words.actionReplace : words.actionNew}
      destructive={replacing}
      // A name the server would refuse waits for a name it would not: the field says which.
      ready={nameOk && !held}
      onConfirm={async () => {
        await onRestore({ target: replacing ? "replace" : "new", name });
      }}
    >
      {forced === null ? (
        <Field label={words.into} nativeLabel={false}>
          <Select<RestoreTarget>
            value={target}
            onValueChange={setChosen}
            options={[
              { value: "new", label: words.intoNew, hint: words.intoNewHint },
              { value: "replace", label: words.intoReplace, hint: words.intoReplaceHint },
            ]}
          />
        </Field>
      ) : null}
      {replacing ? (
        <p className="text-13 text-pretty text-fg">{words.replaceNote}</p>
      ) : (
        <Field label={words.newName} description={words.newNameHelp} error={name !== "" && !nameOk ? words.nameInvalid : undefined}>
          <Input mono value={typed ?? suggestion} onValueChange={(value: string) => setTyped(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
      )}
      {typeof options === "function" ? options(replacing ? "replace" : "new") : options}
    </ConfirmDialog>
  );
}
