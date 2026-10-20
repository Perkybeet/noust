import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";

import { Button } from "./Button";

export interface DisclosureProps {
  /** What the hidden part holds: "More options". */
  label: string;
  /** Opened from the start: a form whose advanced options already differ from the defaults. */
  defaultOpen?: boolean;
  /**
   * `plain` is a button and its region, for the options of a form. `card` is the same in a
   * bordered surface of its own, for a part of a page or a dialog that most operators skip (the
   * ten places that drew a `<details>` with a border).
   */
  variant?: "plain" | "card";
  /**
   * `card` only: one line after the label that says what is inside without opening it ("3 of 12
   * changed", "Using the defaults"). It is part of the button's name.
   */
  summary?: ReactNode;
  children: ReactNode;
}

/**
 * Options most operators never change, folded under one button so a form shows its few
 * decisions first. The button says whether it is open (aria-expanded) and which region it
 * controls. Not for a section most operators need (a `Card`, a subsection), nor for help,
 * which stays visible under its control. Never a `<details>`: one implementation of "folded".
 */
export function Disclosure({ label, defaultOpen = false, variant = "plain", summary, children }: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  const chevron = open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />;

  if (variant === "card") {
    return (
      <div data-slot="disclosure-card" className="min-w-0 rounded-card border border-border bg-surface">
        <div className="p-1">
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={open}
            aria-controls={id}
            icon={chevron}
            onClick={() => setOpen((current) => !current)}
            className="w-full justify-start"
          >
            <span className="flex min-w-0 flex-1 items-baseline gap-2 text-left">
              <span>{label}</span>
              {summary !== undefined ? (
                <>
                  {/* A space for the name the button is given: the layout's gap is not text. */}{" "}
                  <span className="truncate text-12 font-normal text-fg-muted">{summary}</span>
                </>
              ) : null}
            </span>
          </Button>
        </div>
        <div id={id} hidden={!open} className="px-4 pb-4">
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          aria-controls={id}
          icon={chevron}
          onClick={() => setOpen((current) => !current)}
          className="-ml-2.5"
        >
          {label}
        </Button>
      </div>
      <div id={id} hidden={!open}>
        {children}
      </div>
    </div>
  );
}
