/**
 * Where a section of the machine puts the actions of its page: the header the layout draws, which is the same
 * for every section of an area and so cannot know them. A page says what it can do (check now, install the five,
 * add a key) and the layout puts it where every page of the console has its actions, at the right of the title
 * (docs/DESIGN.md, 4: the state beside the title, the action at the right), and not in a toolbar of its own
 * above the content, which is where the 4.1 sections had to keep them.
 */

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * `undefined` where there is no layout to draw the actions (a section drawn alone, in a test or the gallery): they
 * stay where they are. `null` where the layout will, until its slot is in the page.
 */
export const HeaderSlot = createContext<HTMLElement | null | undefined>(undefined);

/**
 * A section's actions, put in its page's header. Drawn in place when there is no header to put them in, so a section
 * rendered alone never loses its buttons.
 */
export function HeaderActions({ children }: { children: ReactNode }) {
  const slot = useContext(HeaderSlot);
  if (slot === undefined) return <div className="flex flex-wrap items-center gap-2">{children}</div>;
  return slot === null ? null : createPortal(children, slot);
}
