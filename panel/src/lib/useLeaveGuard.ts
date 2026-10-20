import { useBlocker } from "@tanstack/react-router";
import { useCallback, useRef } from "react";

export interface LeaveGuard {
  /** A navigation is waiting for the operator to say whether to discard what is unsaved. */
  blocked: boolean;
  /** Keeps the operator here: the navigation is dropped. */
  stay: () => void;
  /** Lets the navigation go on, discarding what is unsaved. */
  leave: () => void;
  /**
   * Stands the guard down for as long as this component lives, for a page that is about to leave
   * on its own (the save went through and the page moves on, the application was deleted). Without
   * it the guard would ask about changes that no longer matter.
   */
  release: () => void;
}

/**
 * Asks once before a form with unsaved changes is left by navigation, and warns the browser
 * before the tab is closed or reloaded (DESIGN FM-3).
 *
 * It blocks a move to another page, not a change of search parameters on this one, and not the
 * redirect to the sign-in page when the session ends: nothing typed could be kept there. With no
 * changes it blocks nothing and costs a registration with the router. Must be inside the
 * router; a `SaveBar` places it where there is one.
 *
 * @param changes How many fields differ from what is saved.
 */
export function useLeaveGuard(changes: number): LeaveGuard {
  const dirty = changes > 0;
  const released = useRef(false);
  // The router registers its blocker again whenever these change, and a navigation that is waiting
  // for an answer is lost when it does: they change only when the form becomes dirty or clean.
  const shouldBlockFn = useCallback(
    ({ current, next }: { current: { pathname: string }; next: { pathname: string } }): boolean =>
      dirty && !released.current && next.pathname !== current.pathname && next.pathname !== "/login",
    [dirty],
  );
  const enableBeforeUnload = useCallback((): boolean => dirty && !released.current, [dirty]);
  const blocker = useBlocker({ shouldBlockFn, enableBeforeUnload, withResolver: true });
  return {
    blocked: blocker.status === "blocked",
    stay: () => {
      blocker.reset?.();
    },
    leave: () => {
      blocker.proceed?.();
    },
    release: () => {
      released.current = true;
    },
  };
}
