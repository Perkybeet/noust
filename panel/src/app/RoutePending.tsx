import { LoadingRegion } from "../components/page/LoadingRegion";
import { Skeleton } from "../components/ui/Skeleton";
import { useT } from "../i18n";

/**
 * What the router draws once a route has kept it waiting for a second (`defaultPendingMs`):
 * the session being read, a page's code on its way. Until then the previous page stays where it
 * is and nothing blinks; after it, the operator sees that the console is working and not stuck
 * (design 6.6). Inside the shell it takes the page's place; `framed` is the same shape for the
 * moments there is no shell yet (the first load), with the page's own margins.
 */
export function RoutePending({ framed = false }: { framed?: boolean }) {
  const t = useT();
  const region = (
    <LoadingRegion label={t("shell.pending.label")} className="flex flex-col gap-8">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-64 w-full rounded-card" />
    </LoadingRegion>
  );
  return framed ? <div className="mx-auto w-full max-w-page px-4 pt-6 pb-16 sm:px-6 lg:px-8 lg:pt-8">{region}</div> : region;
}

/** The framed variant as a component of its own: a route option takes a component, not props. */
export function FramedRoutePending() {
  return <RoutePending framed />;
}
