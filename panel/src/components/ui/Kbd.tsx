import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface KbdProps {
  children: ReactNode;
  /** `inverse` sits on an inverted surface such as a tooltip. */
  tone?: "default" | "inverse";
  className?: string;
}

/** One key of a keyboard shortcut. Render a combination as adjacent keys. */
export function Kbd({ children, tone = "default", className }: KbdProps) {
  return (
    <kbd
      className={cx(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-chip px-1 text-12 leading-none font-medium",
        tone === "default"
          ? "elev-seated bg-surface text-fg-muted"
          : "bg-bg/15 text-bg",
        className,
      )}
    >
      {children}
    </kbd>
  );
}
