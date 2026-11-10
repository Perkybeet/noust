import { useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Drawer } from "../../components/ui/Drawer";
import { Popover } from "../../components/ui/Popover";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { SM_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { useBellRequests } from "./bell";
import InboxPanel from "./InboxPanel";
import { refreshAttention, useAttention } from "./useAttention";

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
  // Asked from outside (the head of Home's "Needs attention", "See all"): it opens on what needs the operator, as a press would.
  const requested = useBellRequests();
  const asked = useRef(requested);
  useEffect(() => {
    if (requested === asked.current) return;
    asked.current = requested;
    setOpen(true);
  });
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
  const panel = <InboxPanel onNavigate={() => setOpen(false)} sheet={!wide} />;
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
