import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { OverviewAttentionItem } from "../../api/queries/overview";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Mono } from "../../components/ui/Mono";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { DetailLine, SeverityGlyph, itemTitle, originLabel, reasonDetail, reasonText, validUntilText } from "../overview/attentionWords";
import { attentionAction, attentionTarget } from "../overview/attentionTarget";
import { AttentionActionButton } from "./AttentionActionButton";
import { attentionIcon } from "./attentionIcons";
import { useRowPress } from "./useRowPress";

/** Where a row was last heard of: the first of its reasons that says when. */
function whenOf(item: OverviewAttentionItem): string | null {
  return item.reasons.find((reason) => reason.when)?.when ?? null;
}

/**
 * One row of what needs the operator: what it is about and what is wrong, where it comes from (a state, an alert,
 * a security finding) and one button that does the first thing about it. The whole row opens the screen that deals
 * with it (item 161): a press anywhere on it leads there. Where the button leads to that same screen it is the row's one
 * link, for a keyboard; where the button does something else (taking an alert), the name is the link and is stretched
 * over the row, above which the button stays a control of its own. The button is in the row at every width: a phone has
 * no hover to reveal it.
 */
export function AttentionRow({ t, item, onNavigate }: { t: T; item: OverviewAttentionItem; onNavigate?: (() => void) | undefined }) {
  const target = attentionTarget(item);
  const buttonLeads = attentionAction(item)?.mode === "open";
  const press = useRowPress(buttonLeads ? target : null, { onNavigate });
  const when = whenOf(item);
  const severity = item.severity === "fail" ? "fail" : "warn";
  // The system's own words come from whichever reason has them: a service that failed has none, the deploy that left it so does.
  const reason = item.reasons.find((candidate) => typeof candidate.detail === "string" && candidate.detail !== "") ?? item.reasons[0];
  const said = reason === undefined ? null : reasonDetail(t, reason);
  const detail = said ?? (typeof reason?.params?.["valid_until"] === "string" ? { text: validUntilText(t, reason.params["valid_until"]), sentence: false } : null);
  const title = itemTitle(t, item);
  const name: ReactNode = title.mono ? <Mono tone="default">{title.text}</Mono> : title.translate ? title.text : <span translate="no">{title.text}</span>;
  const heading = <span className="block min-w-0 truncate text-13 font-medium text-fg group-hover:underline">{name}</span>;
  return (
    <li
      data-severity={severity}
      data-origin={item.origin ?? "state"}
      {...(press !== undefined ? { onClick: press } : {})}
      className={cx("group relative flex min-w-0 gap-3 px-4 py-3", target !== null && "hover:bg-surface-hover", press !== undefined && "cursor-pointer")}
    >
      <span className="flex h-5 shrink-0 items-center">
        <SeverityGlyph severity={severity} />
        <span className="sr-only">{severity === "fail" ? t("overview.attention.srFailure") : t("overview.attention.srWarning")}</span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="flex min-w-0 items-baseline gap-2">
            {target === null || buttonLeads ? (
              heading
            ) : (
              <Link
                to={target.to as never}
                {...(target.params ? { params: target.params as never } : {})}
                {...(target.search ? { search: target.search as never } : {})}
                {...(onNavigate ? { onClick: onNavigate } : {})}
                className="block min-w-0 truncate rounded-chip after:absolute after:inset-0"
              >
                {heading}
              </Link>
            )}
            <span className="flex shrink-0 items-baseline gap-2 text-12 text-fg-faint">
              <span data-slot="origin">{originLabel(t, item.origin)}</span>
              {when ? <RelativeTime value={when} /> : null}
            </span>
          </span>
          {item.reasons.slice(0, 2).map((each, index) => (
            <span key={`${each.code}-${String(index)}`} className={cx("text-13 text-pretty", each.severity === "fail" ? "text-fg" : "text-fg-muted")}>
              {reasonText(t, each)}
            </span>
          ))}
          {/* The system's words, verbatim and wrapped, as everywhere. */}
          {detail !== null ? <DetailLine detail={detail} /> : null}
        </div>
        <div className="relative z-10 flex justify-end">
          <AttentionActionButton item={item} onNavigate={onNavigate} iconFor={attentionIcon} />
        </div>
      </div>
    </li>
  );
}

/** The rows of the list, one under the other, divided by hairlines. */
export function AttentionRows({ t, items, label, onNavigate }: { t: T; items: readonly OverviewAttentionItem[]; label: string; onNavigate?: (() => void) | undefined }) {
  return (
    <ul aria-label={label} className="divide-y divide-border">
      {items.map((item) => (
        <AttentionRow key={item.id} t={t} item={item} onNavigate={onNavigate} />
      ))}
    </ul>
  );
}
