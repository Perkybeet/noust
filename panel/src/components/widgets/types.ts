import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import type { Placed, Settings, SizeKey } from "./model";

/** What a panel follows: the server chosen in the selector, or every server of the fleet. */
export type PanelScope = "server" | "fleet";

/**
 * What the grid draws of one widget: the head (an icon, the title, and a link to where the whole list
 * is), the sizes it takes and its body for the size it has. The grid knows nothing of what a widget is
 * about, so a test can arrange widgets that are only words.
 */
export interface WidgetFace {
  /** Already in the console's language. */
  title: string;
  icon: LucideIcon;
  sizes: readonly SizeKey[];
  /** The link in the head ("All", "See the 7"); not drawn while a panel is being arranged. */
  link?: ReactNode;
  /** The body, for a size. It fills the room the widget has and reads that room itself (`widget-body`). */
  body: (size: SizeKey) => ReactNode;
}

/** The face of the widget a placed item stands for, or null when this console has no such widget. */
export type FaceOf = (item: Placed) => WidgetFace | null;

/** What every widget's body is given. */
export interface WidgetBodyProps {
  /** The size the widget has now: the body reads the room it has too (`widget-body`), this is for what only the size can say. */
  size: SizeKey;
  scope: PanelScope;
  /** What the person set on this widget: the metric of a chart. */
  settings: Settings;
  /** Said by a widget that has settings of its own, so the choice is kept with the panel. */
  onSettings?: (next: Settings) => void;
}
