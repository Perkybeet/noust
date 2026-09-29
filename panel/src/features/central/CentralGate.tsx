import { useState } from "react";
import type { ReactNode } from "react";

import { continuedLocked, rememberContinuedLocked, useCentral } from "./central";
import { CentralLockScreen } from "./CentralLockScreen";

/**
 * Stands between sign-in and the console on a sealed central: while its secrets are locked,
 * the lock screen comes first. The operator may go on without unlocking (the console works on
 * this machine; its servers say they are locked), and that choice holds for the tab.
 */
export function CentralGate({ children }: { children: ReactNode }) {
  const central = useCentral();
  const [continued, setContinued] = useState(continuedLocked);
  if (central.locked && !continued) {
    return (
      <CentralLockScreen
        onContinue={() => {
          rememberContinuedLocked();
          setContinued(true);
        }}
      />
    );
  }
  return children;
}
