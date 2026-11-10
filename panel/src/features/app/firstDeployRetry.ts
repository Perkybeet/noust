import type { FirstDeploy } from "../../api/queries/firstDeploys";
import type { WizardDraft } from "../new-app/draft";
import type { NewAppSearch } from "../new-app/search";

/** How the wizard is opened to try a failed first deploy again: what its draft keeps, and the address it is opened at. */
export interface Retry {
  draft: Pick<WizardDraft, "mode" | "source" | "branch" | "domain">;
  search: Pick<NewAppSearch, "start" | "recipe"> & { resume: 1 };
}

/**
 * What "Try again" opens: the wizard on the source, the branch and the domain the failed deploy was asked with, which
 * is all the server kept (a variable's value is not: it may have been a secret). A recipe opens on its own install
 * form, where its domain is asked; a typed Compose document opens empty, because the document is not kept.
 */
export function retryOf(attempt: FirstDeploy): Retry {
  const domain = attempt.domain;
  const source = attempt.source ?? "";
  const branch = attempt.branch ?? "";
  switch (attempt.origin) {
    case "recipe":
      return {
        draft: { mode: "recipe", source: "", branch: "", domain },
        search: { start: "recipe", ...(attempt.recipe !== null && attempt.recipe !== undefined ? { recipe: attempt.recipe } : {}), resume: 1 },
      };
    case "image":
      return { draft: { mode: "image", source, branch: "", domain }, search: { resume: 1 } };
    case "compose":
      return { draft: { mode: "compose", source: "", branch: "", domain }, search: { resume: 1 } };
    default:
      return { draft: { mode: "manual", source, branch, domain }, search: { resume: 1 } };
  }
}

/**
 * Whether the address the server kept had a password or a token in it, which it masks and does not keep: the operator
 * has to type that part again, and the page says so.
 */
export function sourceWasMasked(attempt: FirstDeploy): boolean {
  return attempt.source !== null && attempt.source !== undefined && attempt.source.includes("***");
}
