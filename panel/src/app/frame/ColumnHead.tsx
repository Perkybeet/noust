import { Link } from "@tanstack/react-router";
import { ChevronLeft, ChevronsUpDown } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Menu, MenuItem } from "../../components/ui/Menu";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";

/** The head of an area's column: where you are, in the display voice. */
export function ColumnHead({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="shrink-0 px-4 pt-4 pb-3">
      <p className="display min-w-0 truncate text-24 text-fg">{title}</p>
      {children}
    </div>
  );
}

/** One of the resource's siblings: the others of its kind, to jump to without going back to the list. */
export interface Sibling {
  key: string;
  /** A name the person gave (a domain, a database), never translated. */
  label: string;
  current: boolean;
  /** A glyph that says its state, drawn before the name. */
  glyph?: ReactNode;
  onChoose: () => void;
}

export interface ResourceHeadProps {
  /** The area it is entered from, as the way back: "Applications". */
  back: { label: string; to: string; search?: Record<string, unknown> };
  /** The resource's own name. */
  title: string;
  /** What the resource is, drawn before its name: its logo, or an object's icon. */
  mark?: ReactNode;
  icon?: LucideIcon;
  /** Its state, as a pill, and what it is in a line ("PostgreSQL 16.4"). */
  state?: ReactNode;
  kind?: ReactNode;
  /** The server it is on, on a fleet. */
  server?: string | null;
  siblings?: readonly Sibling[];
  /** Names the switch: "Switch application". */
  siblingsLabel?: string;
}

/**
 * The head of a resource's column (an application, a database, a server, a service, a team): the way
 * back to its area, its name with its state, and the switch to its siblings. The list under it is the
 * resource's sections, and the column slid to it from the area's.
 */
export function ResourceHead({ back, title, mark, icon: Icon, state, kind, server, siblings, siblingsLabel }: ResourceHeadProps) {
  const t = useT();
  return (
    <div className="shrink-0 px-3 pt-3 pb-3">
      <Link
        to={back.to}
        {...(back.search !== undefined ? { search: back.search as never } : {})}
        activeOptions={{ exact: true }}
        className="group -ml-0.5 inline-flex h-8 items-center gap-1 rounded-control pr-2 pl-1 text-13 font-medium text-fg-muted hover:bg-rail-hover hover:text-fg"
      >
        <ChevronLeft aria-hidden="true" className="size-icon-md text-fg-faint group-hover:text-fg-muted" />
        {back.label}
      </Link>
      <div className="mt-2 flex items-start gap-2.5 px-1">
        {mark !== undefined ? <span className="mt-0.5 flex shrink-0 items-center">{mark}</span> : Icon !== undefined ? <Icon aria-hidden="true" className="mt-2 size-icon-lg shrink-0 text-fg-faint" /> : null}
        <div className="min-w-0 flex-1">
          {/* The height of the switch to siblings, which arrives with their list: the name's row never grows when it does. */}
          <div className="flex min-h-7 items-center gap-1">
            <p translate="no" className="title min-w-0 flex-1 truncate text-16 text-fg">
              {title}
            </p>
            {siblings !== undefined && siblings.length > 1 ? (
              <Menu
                align="end"
                trigger={
                  <button
                    type="button"
                    aria-label={siblingsLabel ?? t("nav.column.switch")}
                    className="flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-control text-fg-faint hover:bg-surface-raised hover:text-fg"
                  >
                    <ChevronsUpDown aria-hidden="true" className="size-icon-sm" />
                  </button>
                }
              >
                {siblings.map((sibling) => (
                  <MenuItem key={sibling.key} onClick={sibling.onChoose} {...(sibling.glyph !== undefined ? { icon: sibling.glyph } : {})}>
                    <span translate="no" className={cx(sibling.current && "font-semibold")}>
                      {sibling.label}
                    </span>
                  </MenuItem>
                ))}
              </Menu>
            ) : null}
          </div>
          {state !== undefined || kind !== undefined ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
              {state}
              {kind !== undefined ? <span className="text-12 text-fg-muted">{kind}</span> : null}
            </div>
          ) : null}
          {server !== undefined && server !== null ? (
            <p translate="no" className="mt-1 flex min-w-0 items-center gap-1.5 text-13 text-fg-muted">
              <OBJECT_ICONS.server aria-hidden="true" className="size-icon-sm shrink-0" />
              <span className="min-w-0 truncate">{server}</span>
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
