import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Logo } from "../brand/Logo";
import { NoustSide } from "../illustrations/NoustSide";

export interface AuthLayoutProps {
  /** The h1: "Sign in", "Unlock this central". */
  title: string;
  /** One sentence under it. */
  description?: ReactNode;
  /**
   * The sentence of the hollow, under the drawing, for the wide slab: what Noust is for, in a line. A
   * phone has room for the drawing and not for this.
   */
  tagline?: ReactNode;
  /**
   * The one line an operator chose to show on these screens (`auth.login_label`), and nothing else of
   * the server: never its name, its version or what it runs. A string is set in mono.
   */
  label?: ReactNode;
  /**
   * The way back, above the title, when the screen is a step away from another (a way in the
   * form's place, with a link to the password).
   */
  back?: ReactNode;
  /** The form. */
  children: ReactNode;
  /** The way to do it from a terminal (CommandHint), under the form, inside the slab. */
  footer?: ReactNode;
  /** The licence notice, under the slab and outside it: it belongs to the page, not to the form. */
  legal?: ReactNode;
  /**
   * `main` (the default) is the whole screen, as sign-in is; `div` draws it inside another
   * page, for the design gallery.
   */
  as?: "main" | "div";
  className?: string;
}

/**
 * T7, the screens before the console: sign in and the checks around it, the first account, an
 * invitation, a new password, a sealed central. One wide slab in the middle of the screen. On the
 * left, carved in as a hollow, the identity: the logo, a noust drawn from the side and a sentence;
 * on the right the form, with its title. Every one of these screens is this slab, so a person who
 * has seen one has seen them all. Below 1024px the hollow becomes a band of drawing across the top
 * of the slab and the form follows it.
 */
export function AuthLayout({ title, description, tagline, label, back, children, footer, legal, as: Element = "main", className }: AuthLayoutProps) {
  const whole = Element === "main";
  const Legal = whole ? "footer" : "div";
  return (
    <div className={cx("flex flex-col items-center bg-bg px-3 py-4 sm:px-6 lg:px-8 lg:py-10", whole && "min-h-dvh justify-center")}>
      <Element
        data-template="auth"
        className={cx("elev-seated flex w-full max-w-auth flex-col overflow-hidden rounded-wall border border-border bg-surface lg:flex-row", className)}
      >
        <div data-slot="identity" className="elev-carved-fine m-2 flex flex-col gap-2 rounded-card bg-bg-sunken px-4 pt-4 pb-3 lg:m-3 lg:flex-11 lg:gap-0 lg:p-8">
          {/*
            18px, on purpose: the drawing is 36 units tall, so it is drawn at exactly half, and the mark's axis of
            symmetry (x = 70) lands on a whole pixel at 1x (35px), on a whole pixel at 2x and 3x, and on a pixel's
            middle at 1.5x. Mirror stones then fall on mirror pixels and the two stacks are the same on screen; at
            20 or 16px the axis falls at .89 or .11 of a pixel and one stack is drawn a little softer than the other.
          */}
          <Logo variant="wordmark" height={18} />
          <NoustSide className="mx-auto h-auto w-full sm:max-w-dialog-md lg:my-auto lg:max-w-none lg:py-6" />
          {tagline !== undefined ? <p className="title hidden text-18 text-fg lg:block">{tagline}</p> : null}
          {label !== undefined ? (
            <p data-slot="label" className="text-12 text-fg-faint lg:mt-3">
              {typeof label === "string" ? (
                <span translate="no" className="mono">
                  {label}
                </span>
              ) : (
                label
              )}
            </p>
          ) : null}
        </div>
        <div data-slot="form" className="flex flex-col justify-center px-5 pt-3 pb-6 lg:flex-10 lg:px-10 lg:py-10">
          {back !== undefined ? (
            <div data-slot="back" className="mb-3">
              {back}
            </div>
          ) : null}
          <h1 tabIndex={-1} data-page-title="" className="display text-32 text-fg outline-none">
            {title}
          </h1>
          {description !== undefined ? <p className="mt-1.5 text-14 text-pretty text-fg-muted">{description}</p> : null}
          <div data-slot="content" className="mt-6">
            {children}
          </div>
          {footer !== undefined ? (
            <div data-slot="footer" className="mt-8">
              {footer}
            </div>
          ) : null}
        </div>
      </Element>
      {legal !== undefined ? <Legal className="mt-5 w-full max-w-auth px-2 lg:mt-8 lg:px-0">{legal}</Legal> : null}
    </div>
  );
}
