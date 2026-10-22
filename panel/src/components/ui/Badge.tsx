import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export type BadgeTone = "neutral" | "accent";

export interface BadgeProps {
  children: ReactNode;
  /** Neutral, or accent for something selected or staged. A state is a StatusPill: colour always means something. */
  tone?: BadgeTone;
  /** Set the content as a system value (runtime version, branch). */
  mono?: boolean;
  className?: string;
}

const TONES: Record<BadgeTone, string> = {
  neutral: "border-border bg-bg-sunken text-fg-muted",
  accent: "border-transparent bg-accent-soft text-accent-fg",
};

/** A short attribute: a type, a count, a version. A state is a StatusPill, never a Badge. */
export function Badge({ children, tone = "neutral", mono = false, className }: BadgeProps) {
  return (
    <span
      className={cx(
        "inline-flex h-5 shrink-0 items-center gap-1 rounded-chip border px-1.5 text-12 leading-none font-medium whitespace-nowrap",
        mono && "mono font-normal",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
