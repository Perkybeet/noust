import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { rolloutQuery } from "../../api/queries/rollout";
import type { PendingChanges } from "../../api/queries/runtime";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { PendingDialog } from "./PendingChanges";

/** The chip is a control around a small pill: the pill says the state, the control opens what it counts. */
const CHIP = "cursor-pointer rounded-pill hover:ring-1 hover:ring-border-strong";

/**
 * "3 not applied": the application is set to something it does not run with yet. It is a state
 * of the application, not of one tab, so it sits on the header's state line on every tab and
 * stays there whatever else the page says; pressing it opens what the settings are and what
 * applying them takes.
 */
export function NotAppliedChip({ domain, pending }: { domain: string; pending: PendingChanges }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const count = (pending.items ?? []).length;
  return (
    <>
      <button type="button" className={CHIP} onClick={() => setOpen(true)}>
        <StatusPill state="warning" size="sm" label={t("appPages.indicators.notApplied", { count })} />
      </button>
      <PendingDialog domain={domain} pending={pending} open={open} onOpenChange={setOpen} />
    </>
  );
}

/**
 * "Deploys paused" (or "frozen"): what deploys the app by itself is held, or a freeze refuses
 * the deploys people ask for. It leads to the Deployments tab, where the hold is told in the
 * words of whoever set it and can be lifted. Nothing is drawn when nothing holds the deploys, or
 * when the server is older than the setting.
 */
export function HoldChip({ domain }: { domain: string }) {
  const t = useT();
  const rollout = useQuery({ ...rolloutQuery(domain), retry: false });
  const data = rollout.data;
  if (data === undefined) return null;
  const label = data.freeze !== null ? t("appPages.indicators.frozen") : data.paused !== null ? t("appPages.indicators.paused") : null;
  if (label === null) return null;
  return (
    <Link to="/apps/$domain/deployments" params={{ domain }} className={CHIP}>
      <StatusPill state="warning" size="sm" label={label} />
    </Link>
  );
}
