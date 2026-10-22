/**
 * What the new-application wizard keeps while the operator is away connecting GitHub, in this
 * tab's session storage: which way in was chosen, the source and the branch typed, and the step.
 * Never a variable, a secret or anything else the operator filled in beyond that: a draft is for
 * coming back to the same place, not for restoring a form, and it is gone with the tab.
 */

const KEY = "noust.newApp.draft";

/** The ways in a draft may name; anything else in storage is not a draft of this wizard. */
const MODES = ["github", "gitlab", "manual", "image", "compose", "recipe", "import", "adopt"] as const;

export type DraftMode = (typeof MODES)[number];

export interface WizardDraft {
  mode: DraftMode;
  source: string;
  branch: string;
  /** Always the first step: connecting GitHub is something that happens there. */
  step: "source";
}

/** Longest value kept: a repository address, not a document. */
const MAX_FIELD = 2_000;

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    // A browser that refuses storage (a locked-down profile) simply has no drafts.
    return null;
  }
}

/** Remembers where the operator was, before they leave the console to connect GitHub. */
export function saveDraft(draft: Pick<WizardDraft, "mode" | "source" | "branch">): void {
  const value: WizardDraft = {
    mode: draft.mode,
    source: draft.source.slice(0, MAX_FIELD),
    branch: draft.branch.slice(0, MAX_FIELD),
    step: "source",
  };
  try {
    storage()?.setItem(KEY, JSON.stringify(value));
  } catch {
    // Full or refused: the operator comes back to an empty wizard, as before drafts existed.
  }
}

/** The draft, or null when there is none or what is stored is not one. It is not removed: see `clearDraft`. */
export function readDraft(): WizardDraft | null {
  let raw: string | null;
  try {
    raw = storage()?.getItem(KEY) ?? null;
  } catch {
    return null;
  }
  if (raw === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const { mode, source, branch } = parsed as Record<string, unknown>;
  if (typeof mode !== "string" || !(MODES as readonly string[]).includes(mode)) return null;
  if (typeof source !== "string" || typeof branch !== "string") return null;
  return { mode: mode as DraftMode, source: source.slice(0, MAX_FIELD), branch: branch.slice(0, MAX_FIELD), step: "source" };
}

/** Whether the wizard is waiting for the operator to come back. */
export function hasDraft(): boolean {
  return readDraft() !== null;
}

/** Forgets the draft: it was used, or the wizard was left on purpose. */
export function clearDraft(): void {
  try {
    storage()?.removeItem(KEY);
  } catch {
    // Nothing to forget in a browser that keeps nothing.
  }
}
