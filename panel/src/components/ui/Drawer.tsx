import { Drawer as BaseDrawer } from "@base-ui/react/drawer";
import { X } from "lucide-react";
import type { ReactElement, ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { BACKDROP } from "./Dialog";
import { ResetPrimary } from "./demotePrimary";
import { IconButton } from "./IconButton";

export interface DrawerProps {
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  trigger?: ReactElement<Record<string, unknown>>;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  size?: "md" | "lg";
  /**
   * `bottom` is a sheet that rises from the foot of a phone, for a list that is a column on a wider
   * screen (the sections of an area, the areas themselves): it is dismissed by swiping down, and it
   * leaves faster than it came. The default is the panel from the right edge.
   */
  placement?: "right" | "bottom";
  /**
   * The content fills the body edge to edge and keeps its own scroll and its own foot, for a panel that is a place and not a
   * detail (the assistant's: a conversation that scrolls over a box that stays). On a sheet it is a tall one, so the content has
   * a height to fill. Without it the content is padded and scrolls as a whole.
   */
  flush?: boolean;
  /**
   * Where focus goes on close; defaults to the trigger. Return false from a function to leave
   * it alone, for a drawer whose action moved the operator to another page.
   */
  finalFocus?: BaseDrawer.Popup.Props["finalFocus"];
}

/**
 * A panel from the right edge for detail that keeps the page in view behind it: a deployment's
 * log, a unit file, a certificate. Swipe right to dismiss on touch screens. With `placement="bottom"`
 * it is a sheet from the foot of the screen (a phone's list of sections), dismissed by swiping down.
 */
export function Drawer({
  title,
  description,
  children,
  footer,
  trigger,
  open,
  defaultOpen,
  onOpenChange,
  size = "md",
  placement = "right",
  flush = false,
  finalFocus,
}: DrawerProps) {
  const t = useT();
  const sheet = placement === "bottom";
  return (
    <BaseDrawer.Root
      swipeDirection={sheet ? "down" : "right"}
      {...(open !== undefined ? { open } : {})}
      {...(defaultOpen !== undefined ? { defaultOpen } : {})}
      {...(onOpenChange ? { onOpenChange: (next: boolean) => onOpenChange(next) } : {})}
    >
      {trigger !== undefined ? <BaseDrawer.Trigger render={trigger} /> : null}
      <BaseDrawer.Portal>
        <BaseDrawer.Backdrop className={BACKDROP} />
        <BaseDrawer.Viewport className={cx("fixed inset-0 z-overlay flex", sheet ? "items-end" : "justify-end")}>
          <BaseDrawer.Popup
            {...(finalFocus !== undefined ? { finalFocus } : {})}
            className={cx(
              "elev-seated flex w-full flex-col border-border-strong text-fg outline-none",
              "transition-transform duration-(--duration-enter) ease-(--ease-enter) data-swiping:duration-0",
              "data-ending-style:duration-(--duration-fast) data-ending-style:ease-(--ease-exit)",
              sheet
                ? cx(
                    "max-h-sheet rounded-t-wall border-t bg-bg-column [transform:translateY(var(--drawer-swipe-movement-y))] data-starting-style:[transform:translateY(100%)] data-ending-style:[transform:translateY(100%)]",
                    flush && "h-sheet",
                  )
                : cx(
                    "h-dvh border-l bg-surface [transform:translateX(var(--drawer-swipe-movement-x))] data-starting-style:[transform:translateX(100%)] data-ending-style:[transform:translateX(100%)]",
                    size === "md" ? "sm:max-w-drawer-md" : "sm:max-w-drawer-lg",
                  ),
            )}
          >
            <ResetPrimary>
              {sheet ? <div aria-hidden="true" className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-pill bg-border-strong" /> : null}
              <header className={cx("flex items-start gap-4", sheet ? "px-4 pt-2 pb-1" : "border-b border-border px-5 py-4")}>
                <div className="min-w-0 flex-1">
                  <BaseDrawer.Title className={sheet ? "title truncate text-16 text-fg" : "display text-24 text-fg"}>{title}</BaseDrawer.Title>
                  {description !== undefined ? (
                    <BaseDrawer.Description className="mt-1 text-13 text-fg-muted">{description}</BaseDrawer.Description>
                  ) : null}
                </div>
                <BaseDrawer.Close
                  render={<IconButton label={t("common.dialog.close")} icon={<X />} size="sm" tooltip={false} className="-mr-2" />}
                />
              </header>
              <BaseDrawer.Content
                className={cx("min-h-0 flex-1", flush ? "flex flex-col overflow-hidden" : cx("overflow-y-auto scroll-thin scroll-stable", sheet ? "pb-4" : "px-5 py-4"))}
              >
                {children}
              </BaseDrawer.Content>
              {footer !== undefined ? (
                <footer className="flex items-center justify-end gap-2 border-t border-border bg-bg-sunken px-5 py-3">
                  {footer}
                </footer>
              ) : null}
            </ResetPrimary>
          </BaseDrawer.Popup>
        </BaseDrawer.Viewport>
      </BaseDrawer.Portal>
    </BaseDrawer.Root>
  );
}
