import type { LucideIcon } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Card } from "./Card";

export interface ResourceFact {
  label: string;
  /** Already formatted; a system value is a `Mono`. */
  value: ReactNode;
}

export interface ResourceCardProps {
  /** What the thing is called, as the operator named it. */
  name: ReactNode;
  /** A system identifier (a database, a unit, a repository): it is not translated. */
  untranslated?: boolean;
  /** The icon of its object, bare and decorative. A real logo is `logo`. */
  icon?: LucideIcon;
  /** A real logo (`LogoTile`), for an application or an engine: the one tile in the console. */
  logo?: ReactNode;
  /** Its state, a `StatusPill size="sm"` after the name: colour, shape and word. */
  status?: ReactNode;
  /** Two or three facts, each a small label over its value. More than that is a table. */
  facts?: readonly ResourceFact[];
  /** The card's own actions: one small button, or a `RowMenu`. */
  actions?: ReactNode;
  /** Whatever must sit under the facts, in the card (an error the provider gave). */
  children?: ReactNode;
  /** Heading level of the name. */
  level?: 3 | 4;
  className?: string;
}

/**
 * A thing that exists, as a card: its icon or logo, its name, its state, two or three facts and what can be done with
 * it. For the few that are the point of a section (an app installed on GitHub, a provider account that was refused);
 * a long list of like things is a table, never a wall of these (docs/DESIGN.md G-2).
 */
export function ResourceCard({ name, untranslated = false, icon: Icon, logo, status, facts, actions, children, level = 3, className }: ResourceCardProps) {
  const headingId = useId();
  const Heading = `h${level}` as const;
  return (
    <Card as="article" padding="none" labelledBy={headingId} className={cx("h-full", className)}>
      <div data-slot="resource-card" className="flex min-w-0 items-start gap-3 p-4">
        {logo !== undefined ? <div className="shrink-0">{logo}</div> : Icon !== undefined ? <Icon aria-hidden="true" className="mt-0.5 size-icon-lg shrink-0 text-fg-muted" /> : null}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <Heading id={headingId} {...(untranslated ? { translate: "no" as const } : {})} className="title min-w-0 text-14 break-words text-fg">
              {name}
            </Heading>
            {status}
          </div>
          {facts !== undefined && facts.length > 0 ? (
            <dl className="flex flex-wrap gap-x-8 gap-y-2">
              {facts.map((fact) => (
                <div key={fact.label} className="min-w-0">
                  <dt className="text-12 text-fg-muted">{fact.label}</dt>
                  <dd className="min-w-0 text-13 break-words text-fg">{fact.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {children}
        </div>
        {actions !== undefined ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </div>
    </Card>
  );
}
