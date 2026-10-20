import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { ErrorBlock } from "../../../components/page/QueryState";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Skeleton } from "../../../components/ui/Skeleton";
import { isElevated } from "../useDeleteApp";

/**
 * A row of a `SettingsGroup` while what it shows is read: the row's own height, so nothing moves
 * when the answer arrives. `label` is a sentence ("Loading the delivery settings").
 */
export function RowLoading({ label }: { label: string }) {
  return (
    <LoadingRegion label={label} className="px-5 py-3">
      <Skeleton className="h-5 w-1/3" />
    </LoadingRegion>
  );
}

/** A row of a `SettingsGroup` whose read failed: the failure verbatim, compact, with the way to read again. */
export function RowError({ title, error, onRetry, retrying }: { title: string; error: unknown; onRetry: () => void; retrying: boolean }) {
  return (
    <div className="px-5 py-3">
      <ErrorBlock compact error={error} title={title} onRetry={onRetry} retrying={retrying} />
    </div>
  );
}

/** A row that holds something a person reads and does not set: the same padding as a `Setting`. */
export function PlainRow({ children }: { children: ReactNode }) {
  return <div className="px-5 py-3">{children}</div>;
}

/**
 * Whether pressing a switch that needs sudo mode would ask "Confirm it's you" now: true until the
 * session is confirmed. The row says so before the press (`Setting elevated`, SW-7).
 */
export function useAsksItsYou(): boolean {
  const session = useQuery(sessionQuery());
  return session.data !== undefined && !isElevated(session.data);
}
