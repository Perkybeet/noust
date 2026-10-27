import { useId } from "react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Switch } from "./Switch";

interface SectionSwitchBase {
  /** What the switch turns on or off, as a name that never changes with the state: "Alerts", "Previews". Already translated. */
  label: string;
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  /** The change is being applied: the switch keeps its value and says it is working. */
  busy?: boolean;
  className?: string;
}

export type SectionSwitchProps =
  | (SectionSwitchBase & { disabled?: false })
  /** A switch that cannot be moved always says why, next to it, with what is missing. */
  | (SectionSwitchBase & { disabled: true; reason: ReactNode });

/**
 * The master switch of a whole section (owner item 106): one switch in the header of the page,
 * beside its action, that turns the area on or off ("Alerts", "Backups"). It is the same switch
 * as a setting's, at the head of the page, with its name beside it; the list under it, when the
 * section is off, is dimmed (`SwitchedRegion`) so a page of rules that do nothing does not look as
 * if it did.
 */
export function SectionSwitch(props: SectionSwitchProps) {
  const { label, checked, onCheckedChange, busy = false, className } = props;
  const labelId = useId();
  const reasonId = useId();
  const disabled = props.disabled === true;
  return (
    <div data-slot="section-switch" className={cx("flex items-center gap-2.5", className)}>
      <span id={labelId} className="text-13 font-semibold text-fg">
        {label}
      </span>
      <Switch
        aria-labelledby={labelId}
        {...(disabled ? { "aria-describedby": reasonId } : {})}
        checked={checked}
        onCheckedChange={onCheckedChange}
        busy={busy}
        disabled={disabled}
      />
      {disabled ? (
        <span id={reasonId} className="text-12 text-fg-muted">
          {props.reason}
        </span>
      ) : null}
    </div>
  );
}

export interface SwitchedRegionProps {
  /** Whether the section is on. */
  on: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * What a section switch governs, drawn off when it is: dimmed, because an inactive thing is
 * the one case in which fading is the message (WCAG exempts inactive components from contrast),
 * and still readable and still reachable, so a person can see what is there before turning it on.
 */
export function SwitchedRegion({ on, children, className }: SwitchedRegionProps) {
  return (
    <div data-slot="switched-region" data-state={on ? "on" : "off"} className={cx("min-w-0 transition-opacity duration-(--duration-move) ease-(--ease-standard)", !on && "opacity-60", className)}>
      {children}
    </div>
  );
}
