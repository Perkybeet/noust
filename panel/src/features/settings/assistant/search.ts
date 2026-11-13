/**
 * The address of Settings > AI assistant: which step of turning it on the person is at, kept in the URL so that the browser's back
 * button goes to the step before and a reload comes back to the same place. It is the reader of the address, apart from the page,
 * so the route can load it with the first page without loading the page's code.
 */

export const STEPS = ["model", "data", "limits", "who"] as const;
export type Step = (typeof STEPS)[number];

export interface AssistantSearch {
  step?: Step;
}

/** Whatever is not one of the four steps is no step: the page the assistant is on. */
export function validateAssistantSearch(raw: Record<string, unknown>): AssistantSearch {
  const step = STEPS.find((candidate) => candidate === raw["step"]);
  return step === undefined ? {} : { step };
}
