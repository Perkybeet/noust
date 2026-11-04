import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "../ui/Button";
import type { ButtonProps } from "../ui/Button";
import { HelpPopover } from "../ui/HelpPopover";
import { Mono } from "../ui/Mono";
import { ICONS } from "../ui/icons";

/**
 * What the signed-in credential holds, as a question: whether it holds a permission. Only the offer: the
 * server decides again on every call, so a stale answer costs a refusal and not a breach. Not yet known is a no,
 * so nothing that writes is drawn for a moment it should not be. A team's own roles are `useOrganization().can`.
 */
export function useCan(permission: string): boolean {
  const { data } = useQuery({ ...sessionQuery(), select: (session) => session.permissions?.includes(permission) ?? false });
  return data ?? false;
}

/** The role the credential signs in with, for a sentence that says what it cannot do; null for a token with none. */
function useRoleName(): string | null | undefined {
  const { data } = useQuery({ ...sessionQuery(), select: (session) => session.role ?? null });
  return data;
}

/**
 * Draws what only a permission allows, and nothing otherwise. This is the form of an action in a row, a menu or a
 * toolbar: a disabled button repeated on every row is noise, and the page says once, at the top, that it can only
 * be read (`ReadOnlyLine`). The one action that is the page's own is a `GuardedButton`, which stays.
 */
export function Can({ permission, children }: { permission: string; children: ReactNode }) {
  return useCan(permission) ? <>{children}</> : null;
}

export type GuardedButtonProps = ButtonProps & {
  /** The permission the action takes, as the server names it (`apps.manage`): the declaration the design rules count. */
  permission: string;
  /** Why it is not available, when "it takes this permission" is not all there is to say (a page's own rule). Already translated. */
  reason?: ReactNode;
};

/**
 * The page's own primary action, for a person whose role cannot do it: it stays where it is, disabled, with a lock
 * instead of its icon and a "?" that says why one press away, because it tells a reader that the thing exists and that
 * somebody can do it. Where the role may, it is the `Button` it wraps, unchanged.
 */
export function GuardedButton({ permission, reason, children, ...button }: GuardedButtonProps) {
  const t = useT();
  const can = useCan(permission);
  if (can) return <Button {...button}>{children}</Button>;
  return (
    <span data-slot="guarded-action" className="inline-flex items-center gap-1">
      <Button {...button} variant="secondary" disabled icon={<ICONS.locked aria-hidden="true" />}>
        {children}
      </Button>
      <HelpPopover label={t("common.readOnly.whyLabel")} title={t("common.readOnly.whyTitle")}>
        <p className="text-13 text-fg-muted">{reason ?? t.rich("common.readOnly.why", { permission: <Mono key="permission">{permission}</Mono> })}</p>
      </HelpPopover>
    </span>
  );
}

/**
 * One line at the top of a page the role can read and not change: said once, so the page does not have to say it in
 * every row. Draws nothing where the role may, and nothing while the session is not known.
 */
export function ReadOnlyLine({ permission, className }: { permission: string; className?: string }) {
  const t = useT();
  const can = useCan(permission);
  const role = useRoleName();
  if (can || role === undefined) return null;
  return (
    <p data-slot="read-only" className={cx("flex max-w-measure items-start gap-2 text-13 text-pretty text-fg-muted", className)}>
      <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fg-faint" />
      <span>
        {role === null
          ? t.rich("common.readOnly.lineNoRole", { permission: <Mono key="permission">{permission}</Mono> })
          : t.rich("common.readOnly.line", { role: <span key="role" translate="no" className="font-medium text-fg">{role}</span>, permission: <Mono key="permission">{permission}</Mono> })}
      </span>
    </p>
  );
}
