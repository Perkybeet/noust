import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Where a section's primary action goes: the settings' page header. It lives apart from the shell so that a
 * section that only needs the slot does not import the shell, whose navigation names every settings screen and
 * so makes the route wait for the texts of all of them.
 */
export const PrimarySlot = createContext<HTMLElement | null>(null);

/**
 * A settings section's one primary action ("Invite a person", "Create token"), put in the page's
 * header where every view has its primary (docs/DESIGN.md 4, "One primary action per view"),
 * although the header belongs to the layout the sections share. Renders nothing outside it.
 */
export function SettingsPrimaryAction({ children }: { children: ReactNode }) {
  const slot = useContext(PrimarySlot);
  return slot === null ? null : createPortal(children, slot);
}
