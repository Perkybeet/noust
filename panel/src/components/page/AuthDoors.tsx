import { cloneElement } from "react";
import type { ReactElement } from "react";

export interface AuthDoor {
  /** Stable key of the door, and what a test or a page tells it apart by. */
  id: string;
  /** What is behind it, in two or three words: "Passkey", "Your company". */
  label: string;
  /** The glyph above the label, drawn bare. It is decoration: the label says it. */
  icon: ReactElement<{ className?: string; "aria-hidden"?: boolean }>;
  onSelect: () => void;
}

export interface AuthDoorsProps {
  /** Names the group for a screen reader: "Other ways to sign in". */
  label: string;
  doors: readonly AuthDoor[];
}

// A raised piece that is pressed (docs/DESIGN.md 2.6), tall enough for a thumb and the same width as its
// neighbours: three doors are three equal answers to "how do you want to get in", not a main one and two
// links. They open a way in the page's own place, so they are buttons. The focus ring is the global one.
const DOOR =
  "elev-raised flex min-w-0 flex-1 cursor-pointer flex-col items-center gap-1.5 rounded-control bg-surface-raised px-2 py-3 text-center text-12 font-medium text-fg select-none";

/**
 * The other ways in on the access screens (T7), under the main form: two or three equal doors, each
 * one a way that opens in the form's own place. Which doors there are is the server's to say (a
 * company's sign-in exists only where one is configured), so the row takes the width it is given
 * and shares it equally.
 */
export function AuthDoors({ label, doors }: AuthDoorsProps) {
  return (
    <nav aria-label={label} className="flex gap-2">
      {doors.map((door) => (
        <button key={door.id} type="button" className={DOOR} onClick={door.onSelect}>
          {cloneElement(door.icon, { "aria-hidden": true, className: "size-icon-lg text-fg-muted" })}
          {door.label}
        </button>
      ))}
    </nav>
  );
}
