/** The address of Settings > Approval policy: which rows are shown, kept in the URL so that the view can be linked. */

export interface PolicySearch {
  view?: "changed";
}

/** Anything that is not "changed" is all the rows, which is the page without a filter. */
export function validatePolicySearch(raw: Record<string, unknown>): PolicySearch {
  return raw["view"] === "changed" ? { view: "changed" } : {};
}
