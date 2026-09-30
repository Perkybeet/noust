/**
 * What the server's settings subsections (General, Notifications) are built from: refreshing
 * the configuration after a write, a form's failure that is about no one field, and the shape
 * of a card still loading.
 */

import { useQueryClient } from "@tanstack/react-query";

import { configKeys } from "../../api/queries/config";
import { ErrorBlock } from "../../components/page/QueryState";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { cx } from "../../lib/cx";

/**
 * Refreshes every configuration answer, the typed sections included, and resolves once they
 * are read again: a form that awaits it never flashes back to the value it just replaced.
 */
export function useRefreshConfig(): () => Promise<void> {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: configKeys.all });
}

/** A refusal that names no field of the form, above its fields: the server's words, verbatim. */
export function FormFailure({ error, title }: { error: unknown; title: string }) {
  if (error === null || error === undefined) return null;
  return <ErrorBlock live compact error={error} title={title} />;
}

/**
 * A card of fields still loading, row for row: each row's fields side by side, each a label,
 * a control and a line of help, so what is below does not move when the settings arrive.
 *
 * @param rows How many fields each row holds.
 */
export function FieldsSkeleton({ rows, title }: { rows: readonly number[]; title?: string }) {
  return (
    <Card {...(title !== undefined ? { title } : {})}>
      <div aria-hidden="true" className="flex flex-col gap-5">
        {rows.map((columns, row) => (
          <div key={row} className={cx("grid gap-5", columns > 1 && "sm:grid-cols-2")}>
            {Array.from({ length: columns }, (_, column) => (
              <div key={column} className="flex min-w-0 flex-col gap-1.5">
                <div className="flex h-5 items-center">
                  <Skeleton className="h-3 w-32" />
                </div>
                <Skeleton className="h-control-md w-full" />
                <div className="flex h-4 items-center">
                  <Skeleton className="h-2.5 w-56 max-w-full" />
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Card>
  );
}
