import { lazy, Suspense } from "react";

import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ICONS } from "../../components/ui/icons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { saveDraft } from "./draft";
import type { SourceForm } from "./wizard";

// The form is drawn only where this server has no GitHub App: its code and its texts wait until then.
const CreateGitHubApp = lazy(() => import("../settings/github/CreateGitHubApp").then((module) => ({ default: module.CreateGitHubApp })));

/**
 * "From GitHub" on a server that has no GitHub App yet: the one form that connects it, here and
 * not in Settings. Pressing it keeps where the wizard was (the way in, the source and the branch,
 * never a variable) in this tab, and GitHub's round trip ends back in the wizard with the
 * repositories to choose from. `error` is Continue's answer while there is nothing to continue with.
 */
export function ConnectGitHub({ form, error }: { form: SourceForm; error: string | undefined }) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <Suspense
        fallback={
          <LoadingRegion label={t("newApp.github.connecting")}>
            <Skeleton className="h-48 w-full rounded-card" />
          </LoadingRegion>
        }
      >
        <CreateGitHubApp submitLabel={t("newApp.github.connect")} onStart={() => saveDraft({ mode: "github", source: form.source, branch: form.branch })} />
      </Suspense>
      {error !== undefined ? (
        <p role="alert" className="flex items-start gap-1.5 text-13 text-fail">
          <ICONS.error aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0" />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}
