import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { cronPreviewQuery } from "../../api/queries/cron";

/** How long to wait after the schedule stops changing before asking the backend to preview it. */
const DEBOUNCE_MS = 350;

/**
 * Debounces a schedule string (an alias or a raw calendar expression) and previews it through
 * `POST /api/cron/preview`, keying the query by the debounced value: react-query keeps each
 * query key's result apart, so a keystroke made obsolete by a later one can never land after
 * it and show a stale calendar - see `cronPreviewQuery`.
 */
export function useCronPreview(schedule: string, onceAt: string | null = null) {
  // A one-time task previews its moment instead: the schedule is not what is being asked.
  const asked = onceAt ?? schedule;
  const [debounced, setDebounced] = useState(asked);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(asked), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [asked]);

  const trimmed = debounced.trim();
  return useQuery({ ...cronPreviewQuery(onceAt === null ? trimmed : "", onceAt === null ? null : trimmed), enabled: trimmed !== "" });
}
