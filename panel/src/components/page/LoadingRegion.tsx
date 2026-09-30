import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Spinner } from "../ui/Spinner";

/** How long a region shows only its skeleton before it also says, visibly, what it is reading. */
export const SLOW_AFTER_MS = 2000;

export interface LoadingRegionProps {
  /** What is loading, as a sentence: "Leyendo SSH". Read out at once; shown once it is slow. */
  label: string;
  /** The skeleton, shaped like the content. */
  children: ReactNode;
  /** Layout of the skeleton (a grid, a column), which the caption sits above. */
  className?: string;
}

/**
 * A region still loading: its skeleton, `aria-busy`, and what it is reading.
 *
 * Under two seconds the skeleton says enough. Some views read the machine (sshd, the firewall,
 * the journal) and take longer, and a skeleton that sat there for five seconds looked stuck; from
 * then on the region also says, with a spinner, what it is doing. Screen readers hear the label
 * at once, so the visible copy is hidden from them.
 */
export function LoadingRegion({ label, children, className }: LoadingRegionProps) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <div aria-busy="true" className="flex min-w-0 flex-col gap-3">
      <span className="sr-only">{label}</span>
      {slow ? (
        <p aria-hidden="true" data-slot="loading-caption" className="flex items-center gap-2 text-12 text-fg-muted">
          <Spinner size={12} />
          {label}
        </p>
      ) : null}
      <div className={cx("min-w-0", className)}>{children}</div>
    </div>
  );
}
