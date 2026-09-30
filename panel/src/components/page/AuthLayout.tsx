import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Logo } from "../brand/Logo";

export interface AuthLayoutProps {
  /** The h1: "Sign in", "Unlock this central". */
  title: string;
  /** One sentence under it. */
  description?: ReactNode;
  /**
   * The machine this console runs on, first of all: nobody should type a token into the wrong
   * server. A string is set in mono; an element (a skeleton while it loads) as it is.
   */
  host?: ReactNode;
  /** A second line under the host: the product and its version. */
  subtitle?: ReactNode;
  /** The form: one field, one large button. */
  children: ReactNode;
  /** The way to do it from a terminal (CommandHint), under the form. */
  footer?: ReactNode;
  /**
   * `main` (the default) is the whole screen, as sign-in is; `div` draws it inside another
   * page, for the design gallery.
   */
  as?: "main" | "div";
  className?: string;
}

/**
 * T7, the screens before the console: sign in, a sealed central, the legal notice. One column
 * of 400px in the middle of the screen, the machine's name above everything.
 */
export function AuthLayout({ title, description, host, subtitle, children, footer, as: Element = "main", className }: AuthLayoutProps) {
  return (
    <Element className={cx("flex items-center justify-center bg-bg px-4 py-12 sm:px-6", Element === "main" && "min-h-dvh")}>
      <div data-template="auth" className={cx("flex w-full max-w-auth flex-col", className)}>
        <div data-slot="identity" className="mb-12 flex items-center gap-3">
          <Logo variant="icon" height={28} />
          {host !== undefined || subtitle !== undefined ? (
            <>
              <span aria-hidden="true" className="h-7 w-px bg-border" />
              <div className="flex min-w-0 flex-col gap-0.5">
                {typeof host === "string" ? (
                  <span translate="no" className="mono truncate text-13 font-medium text-fg">
                    {host}
                  </span>
                ) : (
                  host
                )}
                {subtitle !== undefined ? <span className="text-12 text-fg-faint">{subtitle}</span> : null}
              </div>
            </>
          ) : null}
        </div>
        <h1 tabIndex={-1} data-page-title="" className="title text-24 text-fg outline-none">
          {title}
        </h1>
        {description !== undefined ? <p className="mt-1.5 text-14 text-pretty text-fg-muted">{description}</p> : null}
        <div data-slot="content" className="mt-8">
          {children}
        </div>
        {footer !== undefined ? (
          <div data-slot="footer" className="mt-8">
            {footer}
          </div>
        ) : null}
      </div>
    </Element>
  );
}
