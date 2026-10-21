import { ICONS } from "../../../components/ui/icons";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";

/**
 * The icons an application's settings draw: the kit's own (components/ui/objectIcons.ts), under the
 * names these pages use. An object has one icon in the console (docs/DESIGN.md, 2.8, NV-19), so this
 * file chooses none: a new object or verb goes to the kit's map, and a page here reads it. None is a
 * state (I-5): the state of a feature is its pill. All are decorative.
 */
export const SETTINGS_ICONS = {
  ...OBJECT_ICONS,
  ...VERB_ICONS,
  remove: ICONS.delete,
  add: ICONS.add,
} as const;

export type SettingsIcon = keyof typeof SETTINGS_ICONS;
