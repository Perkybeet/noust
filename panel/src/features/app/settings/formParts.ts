/**
 * One form per subsection, saved from its one SaveBar (docs/DESIGN.md, T3), even when what it
 * holds is stored by several endpoints: the Deploys subsection keeps its startup check, how
 * many versions it keeps and how long the old version stays up in three places. Each of
 * those is a part: it knows how many of its fields changed, whether they can be sent, how to
 * send them and how to forget them. The save bar adds them up and saves the parts that
 * changed, one after the other; a part that fails keeps its changes and says why beside its
 * fields, and the others are saved all the same.
 */

import { useState } from "react";

import { ElevationCancelledError } from "../../../api/errors";
import { reportActionError } from "../../apps/useAppActions";
import { useConfirmItsYou } from "../useDeleteApp";

export interface FormPart {
  /** How many of its fields differ from what is stored. */
  changes: number;
  /** Marks the part as submitted, so its errors show; true when its values can be sent. */
  check: () => boolean;
  /** Sends it. Rejects when refused; the part keeps the refusal to show beside its fields. */
  save: () => Promise<void>;
  /** Back to what is stored, errors and outcomes forgotten. */
  discard: () => void;
  /** Whether the endpoint is behind sudo mode: asked once, up front, for the whole save. */
  elevated: boolean;
}

export interface SaveBarState {
  changes: number;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
}

/**
 * The save bar of a subsection made of parts. Save checks every part that changed first, so
 * every mistake shows at once; asks "Confirm it's you" once when any of them needs it; then
 * saves them in order.
 *
 * @param notStarted What failed, when the save could not start at all ("The deploy settings
 *   of shop.example.com were not saved").
 */
export function useSaveBar(parts: readonly (FormPart | null)[], notStarted: string): SaveBarState {
  const confirmItsYou = useConfirmItsYou();
  const [saving, setSaving] = useState(false);
  const present = parts.filter((part): part is FormPart => part !== null);
  const changed = present.filter((part) => part.changes > 0);
  const changes = changed.reduce((sum, part) => sum + part.changes, 0);

  const onSave = (): void => {
    if (saving || changed.length === 0) return;
    // Every part is checked, not just up to the first that fails: each shows its own errors.
    const valid = changed.map((part) => part.check()).every(Boolean);
    if (!valid) return;
    const run = async (): Promise<void> => {
      if (changed.some((part) => part.elevated)) await confirmItsYou();
      for (const part of changed) {
        try {
          await part.save();
        } catch {
          // The part shows its refusal beside its fields; the next part is saved all the same.
        }
      }
    };
    setSaving(true);
    run()
      .catch((error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(notStarted, error);
      })
      .finally(() => {
        setSaving(false);
      });
  };

  const onDiscard = (): void => {
    for (const part of present) part.discard();
  };

  return { changes, saving, onSave, onDiscard };
}

/** How many of a draft's fields differ from what is stored, compared as typed (trimmed). */
export function changedFields<K extends string>(draft: Readonly<Record<K, string>>, stored: Readonly<Record<K, string>>): number {
  return (Object.keys(stored) as K[]).filter((key) => draft[key].trim() !== stored[key].trim()).length;
}

/**
 * Runs something behind sudo mode after asking "Confirm it's you" first, so that question never
 * opens on top of the dialog the action opens. Declining is not a failure; anything else that
 * stops it is reported with `failed` as its title.
 */
export function useSudoFirst(): (then: () => void, failed: string) => void {
  const confirmItsYou = useConfirmItsYou();
  return (then, failed) => {
    confirmItsYou().then(then, (error: unknown) => {
      if (!(error instanceof ElevationCancelledError)) reportActionError(failed, error);
    });
  };
}
