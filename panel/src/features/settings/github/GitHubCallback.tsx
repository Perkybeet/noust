import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useRouter } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError } from "../../../api/client";
import { addGitHubInstallation, convertGitHubManifest, githubKeys } from "../../../api/queries/github";
import { PageHeader } from "../../../app/PageHeader";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Spinner } from "../../../components/ui/Spinner";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { parseCallback } from "./github";
import type { CallbackRequest } from "./github";

type Actionable = Extract<CallbackRequest, { kind: "conversion" | "installation" }>;

function progressFor(t: T, kind: Actionable["kind"]): string {
  return kind === "conversion"
    ? t("settings.integrations.callback.progressConversion")
    : t("settings.integrations.callback.progressInstallation");
}

function failedFor(t: T, kind: Actionable["kind"]): string {
  return kind === "conversion"
    ? t("settings.integrations.callback.failedConversion")
    : t("settings.integrations.callback.failedInstallation");
}

function BackLink({ primary = false }: { primary?: boolean }) {
  const t = useT();
  return (
    <Link to="/settings/integrations" replace className={buttonClassName(primary ? "primary" : "secondary")}>
      {t("settings.integrations.callback.backToIntegrations")}
    </Link>
  );
}

function Panel({ children }: { children: ReactNode }) {
  return <div className="flex max-w-2xl min-w-0 flex-col gap-4 rounded-card border border-border bg-surface p-5 shadow-raised">{children}</div>;
}

/**
 * Where GitHub sends the browser back, for both halves of connecting it: after the App is
 * created (a one-time `code` to exchange for its credentials) and after it is installed on an
 * account (the `installation_id` to record). Shows what it is doing, the server's own words
 * when it fails, and returns to Settings > Integrations when it is done.
 */
export function GitHubCallback() {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  // Read once, and raw from the history: the router's own search string has been through its
  // JSON parsing, which turns a state like `1e5` into `100000`.
  const router = useRouter();
  const [request] = useState(() => parseCallback(router.history.location.search));
  const started = useRef(false);

  const finish = useMutation({
    mutationFn: async (next: Actionable): Promise<string> => {
      if (next.kind === "conversion") {
        const status = await convertGitHubManifest(next.code, next.state);
        return t("settings.integrations.callback.createdToast", { name: status.name ?? status.slug ?? "" }).trim();
      }
      const installation = await addGitHubInstallation(next.installationId);
      return next.setupAction === "update"
        ? t("settings.integrations.callback.updatedToast", { account: installation.account })
        : t("settings.integrations.callback.installedToast", { account: installation.account });
    },
    onSuccess: (message) => {
      toast.success(message);
      void queryClient.invalidateQueries({ queryKey: githubKeys.all });
      // Replace: the one-time code must not be one Back away from being posted again.
      void navigate({ to: "/settings/integrations", replace: true });
    },
  });

  useEffect(() => {
    // Once: the code GitHub sent can be exchanged a single time, and development's strict
    // mode runs every effect twice.
    if (started.current) return;
    if (request.kind !== "conversion" && request.kind !== "installation") return;
    started.current = true;
    finish.mutate(request);
  }, [request, finish]);

  let body: ReactNode;
  if (request.kind === "requested") {
    body = (
      <Panel>
        <div className="flex flex-col gap-1">
          <p className="text-14 font-medium text-fg">{t("settings.integrations.callback.waitingTitle")}</p>
          <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.callback.waitingDescription")}</p>
        </div>
        <div>
          <BackLink primary />
        </div>
      </Panel>
    );
  } else if (request.kind === "invalid") {
    body = (
      <Panel>
        <div className="flex flex-col gap-1">
          <p className="text-14 font-medium text-fg">{t("settings.integrations.callback.invalidTitle")}</p>
          <p className="text-13 text-pretty text-fg-muted">{t("settings.integrations.callback.invalidDescription")}</p>
        </div>
        <div>
          <BackLink primary />
        </div>
      </Panel>
    );
  } else if (finish.error instanceof ElevationCancelledError) {
    body = (
      <Panel>
        <p role="status" className="text-13 text-fg-muted">
          {finish.error.detail}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => finish.mutate(request)}>
            {t("settings.integrations.callback.confirmAndContinue")}
          </Button>
          <BackLink />
        </div>
      </Panel>
    );
  } else if (finish.isError) {
    body = (
      <div className="flex max-w-2xl min-w-0 flex-col gap-4">
        <ErrorBlock
          live
          error={finish.error}
          title={failedFor(t, request.kind)}
          hint={
            request.kind === "conversion"
              ? t("settings.integrations.callback.conversionHint")
              : t("settings.integrations.callback.installationHint")
          }
          onRetry={() => finish.mutate(request)}
          retrying={finish.isPending}
        />
        <div>
          <BackLink />
        </div>
      </div>
    );
  } else {
    body = (
      <Panel>
        <p role="status" className="flex items-center gap-2 text-13 text-fg">
          <Spinner size={14} className="text-warn" />
          {`${progressFor(t, request.kind)}…`}
        </p>
      </Panel>
    );
  }

  return (
    <>
      <PageHeader
        title={t("settings.integrations.callback.title")}
        breadcrumbs={[
          { label: t("settings.integrations.callback.breadcrumbSettings"), to: "/settings" },
          { label: t("settings.integrations.callback.breadcrumbIntegrations"), to: "/settings/integrations" },
        ]}
      />
      {body}
    </>
  );
}
