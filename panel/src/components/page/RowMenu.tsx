import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { IconButton } from "../ui/IconButton";
import { ICONS } from "../ui/icons";
import { Menu } from "../ui/Menu";

export interface RowMenuProps {
  /** What the actions are for, as the row names it: "shop.example.com", "API_KEY". Not translated. */
  name: string;
  /** `MenuItem`s, seven at most, what destroys last after a `MenuSeparator`. */
  children: ReactNode;
  align?: "start" | "center" | "end";
}

/**
 * The menu at the end of a row: three dots, named "Actions for {name}" in both languages, so
 * that a screen reader on a list of twenty rows hears which row a menu belongs to. The button
 * carries no tooltip: its name is already on screen as the row it sits in.
 */
export function RowMenu({ name, children, align = "end" }: RowMenuProps) {
  const t = useT();
  return (
    <Menu align={align} trigger={<IconButton label={t("common.rowActions", { name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      {children}
    </Menu>
  );
}
