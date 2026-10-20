import { useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { Suspense, lazy, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Drawer } from "../../components/ui/Drawer";
import { Popover } from "../../components/ui/Popover";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { SM_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { refreshAttention, useAttention } from "./useAttention";

// The panel is drawn when the bell is first pressed: the frame of every page only carries the bell.
const InboxPanel = lazy(() => import("./InboxPanel"));

/** What the panel's place holds while its code arrives: the room of three rows, so nothing jumps. */
function PanelPending() {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-3">
      <span className="sr-only">{t("inbox.needs.checking")}</span>
      {[0, 1, 2].map((row) => (
        <div key={row} aria-hidden="true" className="flex gap-3">
          <Skeleton className="mt-0.5 size-3.5 rounded-pill" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3 w-56 max-w-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * The bell: what needs the operator, from any page, two clicks away (the bell, then the item). The
 * count is the worst state's colour with its glyph and the number, as the sidebar's counters are, and
 * its name says it in words. A popover from `sm`, a drawer on a phone, where a popover over a
 * 390-pixel page leaves no page to look at.
 */
export function InboxButton() {
  const t = useT();
  const wide = useMediaQuery(SM_UP);
  const queryClient = useQueryClient();
  const [open, setOpenState] = useState(false);
  const setOpen = (next: boolean): void => {
    setOpenState(next);
    if (next) refreshAttention(queryClient);
  };
  const { items, total } = useAttention();
  const worst = items[0]?.severity === "fail" ? "failed" : "warning";
  const bell = (
    <Button
      variant="ghost"
      aria-label={total > 0 ? t("shell.inbox.needs", { count: total }) : t("shell.inbox.clear")}
      className="px-2"
      data-inbox={total > 0 ? "needs" : "clear"}
      {...(wide ? {} : { onClick: () => setOpen(true) })}
    >
      <Bell aria-hidden="true" />
      {total > 0 ? (
        <span className={cx("inline-flex items-center gap-1 text-12 font-medium", worst === "failed" ? "text-fail" : "text-warn")}>
          <StatusGlyph state={worst} size={10} />
          <span aria-hidden="true" className="tabular-nums">
            {total}
          </span>
        </span>
      ) : null}
    </Button>
  );
  const panel = (
    <Suspense fallback={<PanelPending />}>
      <InboxPanel onNavigate={() => setOpen(false)} />
    </Suspense>
  );
  if (!wide) {
    return (
      <>
        {bell}
        <Drawer open={open} onOpenChange={setOpen} title={t("shell.inbox.title")}>
          {open ? panel : null}
        </Drawer>
      </>
    );
  }
  return (
    <Popover open={open} onOpenChange={setOpen} align="end" trigger={bell} title={t("shell.inbox.title")} className="w-96">
      {panel}
    </Popover>
  );
}
