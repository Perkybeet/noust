import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { ErrorBlock } from "../../../components/page/QueryState";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Skeleton } from "../../../components/ui/Skeleton";
import { isElevated } from "../useDeleteApp";

/**
 * The rows of a `SettingsGroup` while what they show is read, at the height they will have: each
 * entry of `rows` is a row and says how many lines of help it will carry (a row is 44px and every
 * line of help adds 16), so the groups under it do not move when the answer arrives. Most rows
 * carry a line or two (a sentence in Spanish wraps where English does not), and a row that says a
 * risk or a list carries three. `label` is a sentence ("Loading the delivery settings").
 */
export function RowLoading({ label, rows = [1] }: { label: string; rows?: readonly number[] }) {
  return (
    <LoadingRegion label={label} className="flex flex-col divide-y divide-border">
      {rows.map((lines, index) => (
        <div key={index} className="flex flex-col px-5 py-3">
          <Skeleton className="my-0.5 h-4 w-1/3" />
          {Array.from({ length: lines }, (_, line) => (
            <Skeleton key={line} className="my-0.5 h-3 w-2/3" />
          ))}
        </div>
      ))}
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
