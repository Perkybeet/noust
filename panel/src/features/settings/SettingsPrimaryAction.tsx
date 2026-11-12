import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import { HeaderActions, HeaderSlot } from "../server/headerSlot";

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
  const header = useContext(HeaderSlot);
  if (slot !== null) return createPortal(children, slot);
  // The manifest and the console are pages of the machine now: their one action goes to the header of that page.
  return header === undefined ? null : <HeaderActions>{children}</HeaderActions>;
}

/**
 * Where a section's master switch goes: the settings' page header, on the line of the title and right after it. A
 * separate slot from the primary action's, because they are not the same thing: the switch governs what the page
 * holds and sits beside its name, and the action is the one thing the page does.
 */
export const MasterSlot = createContext<HTMLElement | null>(null);

/**
 * A settings section's master switch (`SectionSwitch`), put in the page's header beside the title, although the header
 * belongs to the layout the sections share (rule 106). Renders nothing outside it.
 */
export function SettingsMasterSwitch({ children }: { children: ReactNode }) {
  const slot = useContext(MasterSlot);
  return slot === null ? null : createPortal(children, slot);
}
