import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";

import { Button } from "./Button";

export interface DisclosureProps {
  /** What the hidden part holds: "More options". */
  label: string;
  /** Opened from the start: a form whose advanced options already differ from the defaults. */
  defaultOpen?: boolean;
  children: ReactNode;
}

/**
 * Options most operators never change, folded under one button so a form shows its few
 * decisions first. The button says whether it is open (aria-expanded) and which region it
 * controls. Not for a section most operators need (a `Card`, a subsection), nor for help,
 * which stays visible under its control.
 */
export function Disclosure({ label, defaultOpen = false, children }: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          aria-controls={id}
          icon={open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
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
