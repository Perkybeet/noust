import type { QueryClient } from "@tanstack/react-query";
import { Link, Outlet, createRootRouteWithContext, retainSearchParams } from "@tanstack/react-router";

import { useDocumentTitle } from "../app/documentTitle";
import { validateNodeSearch } from "../app/nodeRoute";
import { useT } from "../i18n";

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  // The selected server (app/nodeRoute.ts): declared here so every route has it, and kept by
  // every navigation that does not set it, so each link stays on the server it was built on.
  validateSearch: validateNodeSearch,
  search: { middlewares: [retainSearchParams(["node"])] },
  component: Outlet,
  notFoundComponent: NotFound,
});

function NotFound() {
  const t = useT();
  useDocumentTitle(t("shell.notFound.title"));
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-2 px-6">
      <h1 className="title text-24">{t("shell.notFound.title")}</h1>
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
