import type { ReactNode } from "react";

import { PageHeader } from "../../app/PageHeader";
import type { PageHeaderProps } from "../../app/PageHeader";
import { cx } from "../../lib/cx";

export interface DetailPageProps {
  /**
   * The resource's header, the same on every tab: breadcrumbs, the name (mono for a system
   * identifier), its state, its facts, and its actions.
   */
  header: PageHeaderProps;
  /**
   * When the resource is broken: a Notice `variant="banner"` with the cause and the fix,
   * between the header and the tabs, so it is seen on every tab and not only on the first.
   */
  banner?: ReactNode;
  /** The job the operator just started on it (JobProgress), on every tab. */
  job?: ReactNode;
  /** The sections as URL tabs (LinkTabs). */
  tabs?: ReactNode;
  /** The active tab (an Outlet). It never repeats the page's or the tab's title in an h2. */
  children: ReactNode;
  /**
   * A second, narrower column beside the content from 1024px, and under it on a narrower window: what is read next to the main thing
   * and never instead of it (a container's consumption and what it touches, beside its logs). A page whose sections are all of
   * one weight has none.
   */
  aside?: ReactNode;
  /**
   * How wide the page is, header and content together: `page` (the shell's column, the default) or `settings` (the
   * width of a settings subsection, `--width-settings`). A header is as wide as its content, so the title and its
   * actions end where the content does and never at the edge of the window, a screen away from what they govern
   * (owner item 109: the master switch of Notifications at the far right of a page 896px wide).
   */
  measure?: "page" | "settings";
  className?: string;
}

/**
 * T2, one resource with tabs: an application, a database, a service, a site, a server. The
 * header and the tabs are the layout route's, so they keep their place and height while the
 * tabs change beneath them.
 */
export function DetailPage({ header, banner, job, tabs, children, aside, measure = "page", className }: DetailPageProps) {
  const between = banner !== undefined || job !== undefined || tabs !== undefined;
  return (
    <div data-template="detail" data-measure={measure} className={cx("flex min-w-0 flex-col", measure === "settings" && "max-w-settings", className)}>
      <div data-slot="header">
        <PageHeader {...header} flush />
      </div>
      {between ? (
        <div className="mt-4 flex min-w-0 flex-col gap-4">
          {banner !== undefined ? <div data-slot="banner">{banner}</div> : null}
          {job !== undefined ? <div data-slot="job">{job}</div> : null}
          {tabs !== undefined ? <div data-slot="tabs">{tabs}</div> : null}
        </div>
      ) : null}
      {aside !== undefined ? (
        // design-exception: arbitrary-grid the second column of the template: the two share the width in one fixed proportion, which only the template knows
        <div data-slot="content" className="mt-8 grid min-w-0 items-start gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <div className="min-w-0">{children}</div>
          <div data-slot="aside" className="min-w-0">
            {aside}
          </div>
        </div>
      ) : (
        <div data-slot="content" className="mt-8 min-w-0">
          {children}
        </div>
      )}
    </div>
  );
}
