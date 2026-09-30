import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import type { FormPart } from "../app/settings/formParts";
import { splitConfigErrors, splitErrors } from "./formErrors";

export type FormValues = Record<string, string | boolean>;

export interface SettingsFormOptions<V extends FormValues> {
  /** What the server holds now, as form values; undefined while it loads. */
  server: V | undefined;
  /** The fields, named as the API names them, so a 422's `fields` lands beside the right one. */
  names: readonly (keyof V & string)[];
  /** The form's only field: a refusal that names no field is then about it. */
  soleField?: keyof V & string;
  /**
   * Dotted configuration key to the field it is about: a typed endpoint answers a value
   * `Config.set` refuses with a 400 whose message starts with the key (`web.public_url must
   * be...`), and that message then goes beside its field.
   */
  configKeys?: Readonly<Record<string, keyof V & string>>;
  /**
   * Writes the values. It resolves once the server accepted them and the cached answer holds
   * the saved values, so the form never flashes back to the old ones.
   */
  save: (values: V) => Promise<void>;
  /** Whether the write is behind sudo mode: every configuration write is. */
  elevated?: boolean;
}

export interface SettingsForm<V extends FormValues> {
  values: V | undefined;
  set: <K extends keyof V & string>(name: K, value: V[K]) => void;
  /** Fields whose value differs from the server's, in form order. */
  changed: readonly (keyof V & string)[];
  dirty: boolean;
  pending: boolean;
  fieldErrors: Partial<Record<keyof V & string, string>>;
  /** A refusal that names no field of this form: shown above its fields, verbatim. */
  formError: unknown;
  discard: () => void;
  /** This form as one part of its subsection's save bar (features/app/settings/formParts). */
  part: FormPart;
}

/** Two values of a field are the same when they read the same: a typed value is trimmed. */
function same(a: string | boolean | undefined, b: string | boolean | undefined): boolean {
  if (typeof a === "string" && typeof b === "string") return a.trim() === b.trim();
  return a === b;
}

/**
 * One group of settings as a form: the operator's edits over the server's values, what is
 * dirty, and the server's verdict placed beside each field. A subsection holds several of them
 * (each written by its own endpoint) and saves them together from its one SaveBar through
 * `part`. The server is the one that validates; nothing here second-guesses it.
 */
export function useSettingsForm<V extends FormValues>({
  server,
  names,
  soleField,
  configKeys,
  save,
  elevated = true,
}: SettingsFormOptions<V>): SettingsForm<V> {
  const [draft, setDraft] = useState<Partial<V>>({});
  // A field's error is hidden once it is edited: the message is about the value that was sent.
  const [edited, setEdited] = useState<ReadonlySet<string>>(new Set());
  const mutation = useMutation({
    mutationFn: save,
    onSuccess: () => {
      setDraft({});
    },
    onSettled: () => {
      setEdited(new Set());
    },
  });

  const values: V | undefined = server === undefined ? undefined : { ...server, ...draft };
  const changed = server === undefined ? [] : names.filter((name) => name in draft && !same(draft[name], server[name]));
  const split = configKeys === undefined ? splitErrors(mutation.error, names, soleField) : splitConfigErrors(mutation.error, names, configKeys);
  const fieldErrors: Partial<Record<keyof V & string, string>> = {};
  for (const name of names) {
    const message = split.fields[name];
    if (message !== undefined && !edited.has(name)) fieldErrors[name] = message;
  }

  const discard = (): void => {
    setDraft({});
    setEdited(new Set());
    mutation.reset();
  };

  return {
    values,
    set: (name, value) => {
      setDraft((current) => ({ ...current, [name]: value }));
      setEdited((current) => new Set([...current, name]));
    },
    changed,
    dirty: changed.length > 0,
    pending: mutation.isPending,
    fieldErrors,
    formError: split.form,
    discard,
    part: {
      changes: changed.length,
      elevated,
      check: () => true,
      save: async () => {
        if (values === undefined) return;
        await mutation.mutateAsync(values);
      },
      discard,
    },
  };
}
