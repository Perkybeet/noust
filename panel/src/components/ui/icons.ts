import { Check, CircleAlert, CircleCheck, Copy, Ellipsis, ExternalLink, Info, LockKeyhole, Plus, Search, ShieldCheck, Trash2, TriangleAlert, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";

/**
 * The icons that carry a meaning, one icon per meaning (docs/DESIGN.md, "Icons"). A feature
 * takes a severity, "more actions" or "delete" from here, so the same idea never has two
 * drawings and one drawing never has two ideas. Object icons (a server, a database, a
 * navigation entry) are imported from lucide-react directly.
 *
 * The state of an entity (running, failed...) is not here: it is StatusGlyph's, a separate
 * vocabulary with its own shapes. These are the severity of a message and common verbs.
 */
export const ICONS = {
  /** A message that reports something went right. Never an entity's state. */
  success: CircleCheck,
  /** A message that asks for attention before something goes wrong. */
  warning: TriangleAlert,
  /** A message that reports a failure. */
  error: CircleAlert,
  /** A neutral message: no state, no colour. */
  info: Info,
  /** Something the operator cannot change here (sealed, owned by the central). */
  locked: LockKeyhole,
  /** Verified security: a valid certificate. Not "OK" in general. */
  verified: ShieldCheck,
  /** The overflow menu of a row or a header. */
  more: Ellipsis,
  /** Closes an overlay. */
  close: X,
  /** Deletes data Noust owns. */
  delete: Trash2,
  /** A link that leaves the console. */
  external: ExternalLink,
  copy: Copy,
  /** The confirmation after copying. */
  copied: Check,
  add: Plus,
  search: Search,
  /** Dismisses a message: the same drawing as close, a different verb for the label. */
  dismiss: X,
} as const satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

/** The five icon sizes (tokens.css `--icon-*`) as utilities: 12, 14, 16, 20, 24 px. */
export const ICON_SIZE = {
  xs: "size-icon-xs",
  sm: "size-icon-sm",
  md: "size-icon-md",
  lg: "size-icon-lg",
  xl: "size-icon-xl",
} as const;

export type IconSize = keyof typeof ICON_SIZE;
