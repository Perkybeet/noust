import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Card } from "../ui/Card";
import { CommandHint } from "./CommandHint";

export interface SettingsGroupProps {
  /** The subject of the rows: "Backups", "Notifications". */
  title: string;
  /** The icon of the object the rows are about, before the title. */
  icon?: LucideIcon;
  /** One sentence on the subject, when the title does not say it. Not a repeat of a row's help. */
  description?: ReactNode;
  /**
   * The state of the feature the group configures, a `StatusPill size="sm"` after the title. A
   * feature with a state of its own is the header of its card, not a coloured block.
   */
  status?: ReactNode;
  /** Controls for the whole group, aligned with the title: the button that turns a feature on. */
  actions?: ReactNode;
  /** The CLI command that does the same, once, at the foot of the group. */
  command?: string;
  /** Heading level of the title. */
  level?: 2 | 3 | 4;
  /** The rows: `Setting`s, or a row of your own with the same padding. */
  children: ReactNode;
  className?: string;
}

/**
 * The rows of one subject on one surface: a `Card` with no padding of its own whose children are
 * separated by a hairline. A subsection of Settings that saves with a Save button is a form of
 * `Field`s, not a group of rows: a `Setting` applies the moment it is moved and never shares a
 * surface with a Save button.
 */
export function SettingsGroup({ title, icon, description, status, actions, command, level = 3, children, className }: SettingsGroupProps) {
  return (
    <Card
      title={title}
      level={level}
      {...(icon !== undefined ? { icon } : {})}
      padding="none"
      {...(description !== undefined ? { description } : {})}
      {...(status !== undefined ? { status } : {})}
      {...(actions !== undefined ? { actions } : {})}
      {...(className !== undefined ? { className } : {})}
    >
      <div data-slot="settings-group" className="flex flex-col divide-y divide-border">
        {children}
        {command !== undefined ? <CommandHint command={command} className="px-5 py-3" /> : null}
      </div>
    </Card>
  );
}
