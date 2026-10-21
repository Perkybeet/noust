/**
 * The one toolbar of every log surface (docs/DESIGN.md, LG-1): the Logs page over the
 * applications, the same page over the system's journal, and an application's own Logs tab. The
 * query, the source, the range with "Live" as one of its choices, and a link to exactly this
 * view. The level chips, Follow, wrapping, copying and downloading are the viewer's, under it.
 *
 * It draws and decides nothing about where the lines come from: a surface hands it the state it
 * keeps in the URL and the controls that are its own (an application, a service), and turns what
 * comes back into a request. That is why the three surfaces cannot drift apart.
 */

import { CircleHelp } from "lucide-react";
import type { ReactNode } from "react";

import { FilterBar } from "../../components/page/FilterBar";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { CopyButton } from "../../components/ui/CopyButton";
import { IconButton } from "../../components/ui/IconButton";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Mono } from "../../components/ui/Mono";
import { Popover } from "../../components/ui/Popover";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";

export interface ToolbarChoice<V extends string> {
  value: V;
  label: string;
}

export interface LogToolbarProps<R extends string, S extends string = string> {
  /** Names the search landmark: "Filter the logs". */
  label: string;
  /** The box that asks the question, in the language of `./query.ts`. */
  query: {
    value: string;
    onChange: (query: string) => void;
    /** The box's accessible name: "Search the logs". */
    label: string;
    placeholder: string;
  };
  /** Where the lines come from, when the surface has more than one place to read them. */
  sources?: {
    label: string;
    value: S;
    options: readonly ToolbarChoice<S>[];
    onChange: (source: S) => void;
  };
  /** What narrows the source: an application, a service. Drawn as the surface made it. */
  picker?: ReactNode;
  /** How far back, with "live" among the choices. */
  range: {
    label: string;
    value: R;
    options: readonly ToolbarChoice<R>[];
    onChange: (range: R) => void;
    /** A stretch picked elsewhere replaces the range: the choice is not drawn while it holds. */
    hidden?: boolean;
  };
  /** The query as the server (or this page, for a stream) understood it. */
  understood?: string | undefined;
  /** Why the query cannot be read here, in the grammar's own words: shown instead of "Understood as". */
  error?: string | undefined;
  /** More secondary actions, before the link. */
  actions?: ReactNode;
}

/** What each part of the language means, in a table of examples: what is typed is the syntax, so it is not translated. */
const SYNTAX: readonly (readonly [string, "words" | "phrase" | "unit" | "app" | "level" | "not" | "or" | "group"])[] = [
  ["error timeout", "words"],
  ['"connection refused"', "phrase"],
  ["@unit:nginx", "unit"],
  ["@app:shop.example.com", "app"],
  ["@level:err", "level"],
  ["-debug", "not"],
  ["error OR fatal", "or"],
  ["(a OR b) c", "group"],
];

function QuerySyntax() {
  const t = useT();
  return (
    <Popover
      align="end"
      title={t("logs.syntax.title")}
      description={t("logs.syntax.intro")}
      className="w-96"
      trigger={<IconButton label={t("logs.syntax.label")} icon={<CircleHelp />} />}
    >
      <dl className="flex flex-col gap-2 text-13">
        {SYNTAX.map(([example, meaning]) => (
          <div key={example} className="flex min-w-0 flex-col gap-0.5">
            <dt>
              <Mono>{example}</Mono>
            </dt>
            <dd className="text-fg-muted">{t(`logs.syntax.${meaning}`)}</dd>
          </div>
        ))}
      </dl>
    </Popover>
  );
}

/** The toolbar of a log surface. */
export function LogToolbar<R extends string, S extends string = string>({ label, query, sources, picker, range, understood, error, actions }: LogToolbarProps<R, S>) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <FilterBar
        label={label}
        search={{ label: query.label, placeholder: query.placeholder, value: query.value, onChange: query.onChange }}
        filters={
          <>
            {sources !== undefined ? (
              <SegmentedControl<S> label={sources.label} value={sources.value} onValueChange={sources.onChange} options={sources.options} />
            ) : null}
            {picker}
            {range.hidden === true ? null : <Select<R> aria-label={range.label} value={range.value} onValueChange={range.onChange} options={range.options} />}
          </>
        }
        actions={
          <>
            {actions}
            <QuerySyntax />
            {/* Every surface keeps its state in the address, so the address is the link. */}
            <CopyButton value={() => window.location.href} label={t("logs.copyLink")} />
          </>
        }
      />
      {error !== undefined ? (
        <InlineMessage tone="error">{error}</InlineMessage>
      ) : understood !== undefined && understood !== "" ? (
        <p className="text-13 text-fg-muted">
          {t("logs.understood")} <Mono>{understood}</Mono>
        </p>
      ) : null}
    </div>
  );
}
