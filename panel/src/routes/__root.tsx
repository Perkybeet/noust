import type { QueryClient } from "@tanstack/react-query";
import { Link, Outlet, createRootRouteWithContext, retainSearchParams } from "@tanstack/react-router";

import { useDocumentTitle } from "../app/documentTitle";
import { getLocale } from "../app/locale";
import { validateNodeSearch } from "../app/nodeRoute";
import { ReportHost } from "../features/report/ReportHost";
import { useT } from "../i18n";
import { loadRouteNamespaces } from "../i18n/routeNamespaces";

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  // Before any route loads, and on every navigation and every hover that preloads one: the texts
  // of the page about to be drawn are here, so it never opens in English or in keys. Nothing is
  // returned when they already are (every visit after the idle preload), so nothing waits.
  beforeLoad: ({ location }) => loadRouteNamespaces(getLocale(), location),
  // The selected server (app/nodeRoute.ts): declared here so every route has it, and kept by
  // every navigation that does not set it, so each link stays on the server it was built on.
  validateSearch: validateNodeSearch,
  search: { middlewares: [retainSearchParams(["node"])] },
  component: RootLayout,
  notFoundComponent: NotFound,
});

/**
 * What every address is drawn in. The report of a problem is mounted here and not in the shell: the screen of a page
 * that broke can replace the whole shell (an error in what a route loads is drawn by the console's layout in a bare
 * page), and the button that report is opened from has to find its dialog there too.
 */
function RootLayout() {
  return (
    <>
      <Outlet />
      <ReportHost />
    </>
  );
}

function NotFound() {
  const t = useT();
  useDocumentTitle(t("shell.notFound.title"));
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-2 px-6">
      <h1 className="display text-32">{t("shell.notFound.title")}</h1>
      <p className="text-14 text-fg-muted">
        {t.rich("shell.notFound.body", {
          overview: (
            <Link to="/" className="font-medium text-accent-fg underline underline-offset-2">
              {t("shell.notFound.overviewLink")}
            </Link>
          ),
        })}
      </p>
    </main>
  );
}
