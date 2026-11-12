import { useNavigate } from "@tanstack/react-router";
import type { MouseEvent } from "react";

import type { AttentionTarget } from "../overview/attentionTarget";

/** What a press on a row must leave to the control it lands on. */
const CONTROLS = "a, button, input, select, textarea, summary, [role='button'], [role='link'], [role='menuitem']";

/**
 * A press anywhere on a row of what needs attention opens the screen that deals with it (item 161), for a pointer and a finger.
 * Where the row's button leads to that same screen, the row has no second link to it: two links to one place are one too many
 * for a keyboard, which has the button. A control inside the row keeps its own press, a text being selected is not a press, and
 * what opens in another tab with a modifier is the control's to do.
 *
 * Returns the handler for the row, or undefined where the row has nowhere to lead.
 */
export function useRowPress(target: AttentionTarget | null, options: { node?: string | null | undefined; onNavigate?: (() => void) | undefined } = {}): ((event: MouseEvent<HTMLElement>) => void) | undefined {
  const navigate = useNavigate();
  if (target === null) return undefined;
  const { node, onNavigate } = options;
  return (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (event.target instanceof Element && event.target.closest(CONTROLS) !== null) return;
    if ((window.getSelection()?.toString() ?? "") !== "") return;
    onNavigate?.();
    const search = target.search !== undefined || node !== undefined ? { ...target.search, node: node ?? undefined } : undefined;
    void navigate({ to: target.to as never, ...(target.params ? { params: target.params as never } : {}), ...(search !== undefined ? { search: search as never } : {}) });
  };
}
