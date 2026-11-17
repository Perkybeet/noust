/**
 * What the sign-in page knows of the first start of the server it is on. While a server has no account the session says so
 * (`accounts_exist: false`) and says where the access token was shown (`first_start.token_shown_by`: by the installer, or in a
 * container's log); once an account exists both are gone and the page can reveal no more, so it says what it always said.
 * Nothing here is worked out from the machine: the server says it.
 */

import type { SessionInfo } from "../../api/queries/auth";

/** Where the token was shown the first time: the installer's box, a container's log, or nowhere the page may say (there are accounts). */
export type TokenShownBy = "installer" | "container_log" | null;

/** Whether the server has no account yet, so the access token is how anybody gets in. */
export function hasNoAccounts(session: SessionInfo | undefined): boolean {
  return session?.accounts_exist === false;
}

/** Where the token was shown, as the server says it while it has no account; null when it says nothing. */
export function tokenShownBy(session: SessionInfo | undefined): TokenShownBy {
  return session?.first_start?.token_shown_by ?? null;
}

/**
 * The command that issues a new token, where the token was shown: inside a container it is run through `docker exec`, and the first
 * start's log says which container is the central's (no name of one is written here: it is the operator's).
 */
export function newTokenCommand(shownBy: TokenShownBy): string {
  return shownBy === "container_log" ? "docker exec -it <container> noust web token --new" : "noust web token --new";
}
