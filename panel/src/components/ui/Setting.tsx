import { useId, useState } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { ConfirmDialog } from "./ConfirmDialog";
import { InlineMessage } from "./InlineMessage";
import { Popover } from "./Popover";
import { Switch } from "./Switch";
import { textLinkClassName } from "./TextLink";
import { Tooltip } from "./Tooltip";
import { ICONS } from "./icons";

/** The question asked before a safeguard is turned off (DESIGN 6.2, `simple`). */
export interface SettingConfirmOff {
  title: string;
  /** What turning it off means, concretely. */
  description: ReactNode;
  /** The verb and its object: "Turn off the required backup". */
  actionLabel: string;
}

interface SettingBase {
  /**
   * A noun phrase that names the thing and never changes with its state: "Send notifications",
   * not "Notifications are on". The state is the control.
   */
  label: ReactNode;
  /**
   * One sentence of 18 words or fewer on what the setting does while it is on. It never starts
   * with the state ("Off: ...") and never repeats the card's description.
   */
  help?: ReactNode;
  /**
   * A `StatusPill size="sm"` after the label, for what the control alone cannot say: a setting
   * that is on and does not work says `warning` here ("On, no channel"), puts the sentence of the
   * problem in `help` with one link to the fix, and draws no block.
   */
  status?: ReactNode;
  /**
   * What can go wrong while it is on (or, for a safeguard, while it is off): one line of 14 words
   * or fewer under the help, with a warning glyph. Give it only while it is true.
   */
  risk?: ReactNode;
  /** The detail behind a "What changes" link next to the risk. */
  riskDetail?: ReactNode;
  /**
   * Pressing it will ask "Confirm it's you" (sudo mode): a key says so before the press. Give it
   * while the window is closed.
   */
  elevated?: boolean;
  /** Set by a policy the operator cannot change here: a lock and who sets it replace the help. */
  lockedBy?: ReactNode;
  /** Fields that only make sense while the setting is on; a setting with an `action` always shows them. */
  children?: ReactNode;
  className?: string;
}

/** A setting that takes effect the moment it is moved and can be undone in one step. */
interface SwitchSetting extends SettingBase {
  checked: boolean;
  /**
   * Applies the change. A direct press ignores what this returns, so the caller reports a failure
   * (`reportActionError`) and `checked` goes back by itself; the question of `confirmOff` waits for
   * the promise, stays open while it works and shows a failure verbatim.
   */
  onCheckedChange: (checked: boolean) => void | Promise<void>;
  /** The change is being applied: the switch keeps its value and draws its arc. */
  busy?: boolean;
  /** Turning it off removes a safeguard: one question first. */
  confirmOff?: SettingConfirmOff;
  action?: never;
}

/** What needs a plan (a dry run, a restart, a rewrite of units) is a button, not a switch. */
interface ActionSetting extends SettingBase {
  /** The control at the end of the row, usually `<Button size="sm">Turn on…</Button>`. */
  action: ReactNode;
  checked?: never;
  onCheckedChange?: never;
  busy?: never;
  confirmOff?: never;
}

/** A switch that cannot be pressed always says why, with a link to what is missing. */
type Availability = { disabled?: false | undefined; reason?: ReactNode } | { disabled: true; reason: ReactNode };

export type SettingProps = (SwitchSetting & Availability) | (ActionSetting & { disabled?: never; reason?: never });

/**
 * One setting as a row: its name, one sentence of help, and the control at the end. 44px tall
 * without help and 60px with one line; nothing in a row is taller than its text. Rows of one
 * subject live in a `SettingsGroup`.
 *
 * Not for a filter or a view mode (a `Checkbox` in a toolbar, or an `IconButton` with
 * `aria-pressed`), nor for a parameter of an action (a `Checkbox` in the action's form), nor for
 * anything saved with a Save button: a switch never shares a surface with one.
 */
export function Setting(props: SettingProps) {
  const { label, help, status, risk, riskDetail, elevated = false, lockedBy, children, className } = props;
  const t = useT();
  const ids = useId();
  const labelId = `${ids}-label`;
  const helpId = `${ids}-help`;
  const riskId = `${ids}-risk`;
  const [asking, setAsking] = useState(false);

  const isSwitch = props.checked !== undefined;
  const locked = lockedBy !== undefined;
  const unavailable = locked || props.disabled === true;
  // The reason a switch is off limits, or the policy behind it, takes the help's place: it is what
  // the operator needs to read now, and the row keeps its height.
  const helpLine = locked ? (
    <p id={helpId} className="flex items-start gap-1.5 text-12 text-fg-muted">
      <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-xs shrink-0" />
      <span className="min-w-0 text-pretty">{lockedBy}</span>
    </p>
  ) : props.disabled === true ? (
    <p id={helpId} className="max-w-measure-help text-12 text-pretty text-fg-muted">
      {props.reason}
    </p>
  ) : help !== undefined ? (
    <p id={helpId} className="max-w-measure-help text-12 text-pretty text-fg-muted">
      {help}
    </p>
  ) : null;
  const describedBy = [helpLine !== null ? helpId : null, risk !== undefined ? riskId : null].filter((id) => id !== null).join(" ");

  const toggle = (next: boolean): void => {
    if (!isSwitch) return;
    if (!next && props.confirmOff !== undefined) {
      setAsking(true);
      return;
    }
    void props.onCheckedChange(next);
  };

  const control = isSwitch ? (
    <Switch
      aria-labelledby={labelId}
      {...(describedBy !== "" ? { "aria-describedby": describedBy } : {})}
      checked={props.checked}
      onCheckedChange={toggle}
      busy={props.busy === true}
      disabled={unavailable}
    />
  ) : (
    props.action
  );

  return (
    <div data-slot="setting" {...(isSwitch ? { "data-checked": props.checked ? "" : undefined } : {})} className={cx("flex flex-col gap-3 px-5 py-3", className)}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <span id={labelId} className="text-13 font-medium text-fg">
              {label}
            </span>
            {status}
            {elevated ? (
              <Tooltip content={t("common.setting.elevated")}>
                <span role="img" aria-label={t("common.setting.elevated")} className="inline-flex text-fg-faint">
                  <ICONS.elevated aria-hidden="true" className="size-icon-sm" />
                </span>
              </Tooltip>
            ) : null}
          </div>
          {helpLine}
          {risk !== undefined ? (
            <div id={riskId}>
              <InlineMessage
                trailing={
                  riskDetail !== undefined ? (
                    <Popover
                      trigger={
                        <button type="button" className={textLinkClassName("inline")}>
                          {t("common.setting.whatChanges")}
                        </button>
                      }
                      title={t("common.setting.whatChanges")}
                      side="bottom"
                      align="start"
                    >
                      <div className="text-13 text-pretty text-fg-muted">{riskDetail}</div>
                    </Popover>
                  ) : undefined
                }
              >
                {risk}
              </InlineMessage>
            </div>
          ) : null}
        </div>
        {/* A small button is 28px tall; the row stays at the height of its text. */}
        <div className={cx("flex shrink-0 items-center", !isSwitch && "-my-1")}>{control}</div>
      </div>
      {children !== undefined && (!isSwitch || props.checked) ? <div data-slot="setting-fields">{children}</div> : null}
      {isSwitch && props.confirmOff !== undefined ? (
        <ConfirmDialog
          friction="simple"
          open={asking}
          onOpenChange={setAsking}
          title={props.confirmOff.title}
          description={props.confirmOff.description}
          actionLabel={props.confirmOff.actionLabel}
          onConfirm={async () => {
            await props.onCheckedChange(false);
          }}
        />
      ) : null}
    </div>
  );
}
