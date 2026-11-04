import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef } from "react";

import { Card } from "../../components/ui/Card";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Progress } from "../../components/ui/Progress";
import { stateTextClass, StatusGlyph } from "../../components/ui/StatusPill";
import { useNavLayout } from "../../app/navLayout";
import { useNavScope } from "../../app/useNavScope";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { useNode } from "../../nodes/useNode";
import { placeOf } from "./model";
import type { Task } from "./model";
import { unpin, useTaskState } from "./store";
import { stepOf, titleOf, viewOf } from "./words";

/** The custom property the toasts raise themselves by, so they sit above the chips and never under them. */
export const PINNED_HEIGHT_VAR = "--tasks-pinned-height";

function Chip({ task }: { task: Task }) {
  const t = useT();
  const title = titleOf(task, t.locale);
  const view = viewOf(task.state, t.locale);
  const step = stepOf(task, t.locale);
  const place = placeOf(task);
  const running = task.state === "running" && task.progress !== null;
  const percent = task.progress ?? 0;
  const line = running ? (step === null || step === "" ? `${String(percent)} %` : t("tasks.pinned.progress", { percent, step })) : (step ?? view.label);
  return (
    <Card as="li" padding="none" className="flex w-80 max-w-full items-center gap-2.5 py-2 pr-1.5 pl-3">
      <span className={stateTextClass(view.state)}>
        <StatusGlyph state={view.state} />
        <span className="sr-only">{view.label}</span>
      </span>
      <Link
        to={place.to as never}
        {...(place.params !== undefined ? { params: place.params as never } : {})}
        {...(place.search !== undefined ? { search: place.search as never } : {})}
        className="min-w-0 flex-1 rounded-control"
      >
        <span className="block truncate text-13 font-medium text-fg">{title}</span>
        <span className={cx("block truncate text-12", task.state === "failed" ? stateTextClass("failed") : "text-fg-muted")}>{line}</span>
      </Link>
      {running ? <Progress layout="inline" showValue={false} value={task.progress} label={title} className="w-12 shrink-0" /> : null}
      <IconButton label={t("tasks.row.unpin", { title })} icon={<ICONS.close />} size="sm" onClick={() => { unpin(task.key); }} />
    </Card>
  );
}

/**
 * The tasks a person pinned, as small chips at the foot of every page (like a browser's downloads): what it is, how far
 * it has got and a press away from its page. A chip that ends stays with its result until it is let go, so the end is not
 * missed. Nothing is pinned by itself, and a chip of a server other than the one on screen waits for it (it is not being heard).
 */
export function PinnedTasks() {
  const t = useT();
  const state = useTaskState();
  const { node } = useNode();
  const scope = useNavScope();
  const { phone } = useNavLayout();
  const chips = useMemo(
    () =>
      state.pinned.flatMap((key) => {
        const task = state.tasks.get(key);
        return task !== undefined && (scope === "fleet" || task.server === node) ? [task] : [];
      }),
    [state.pinned, state.tasks, node, scope],
  );
  const list = useRef<HTMLUListElement>(null);

  // The toasts rise above the chips by their height, which is the chips' to say: nothing here knows how tall they are.
  useEffect(() => {
    const root = document.documentElement;
    const element = list.current;
    if (element === null || phone) {
      root.style.removeProperty(PINNED_HEIGHT_VAR);
      return;
    }
    const measure = (): void => {
      root.style.setProperty(PINNED_HEIGHT_VAR, `${String(element.offsetHeight + 8)}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => {
      observer.disconnect();
      root.style.removeProperty(PINNED_HEIGHT_VAR);
    };
  }, [chips.length, phone]);

  if (chips.length === 0) return null;
  return (
    <ul ref={list} aria-label={t("tasks.pinned.region")} className={cx("fixed right-4 z-sticky flex flex-col items-end gap-2", phone ? "bottom-20 left-4" : "bottom-4")}>
      {chips.map((task) => (
        <Chip key={task.key} task={task} />
      ))}
    </ul>
  );
}
