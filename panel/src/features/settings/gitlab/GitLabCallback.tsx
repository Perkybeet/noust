import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useRouter } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError } from "../../../api/client";
import { finishGitLabSignIn, gitlabKeys } from "../../../api/queries/gitlab";
import { DetailPage } from "../../../components/page/DetailPage";
import { JobProgress } from "../../../components/page/JobProgress";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { parseGitLabCallback } from "./gitlab";

function BackLink({ primary = false }: { primary?: boolean }) {
  const t = useT();
  return (
    <Link to="/settings/integrations" replace className={buttonClassName(primary ? "primary" : "secondary")}>
      {t("gitlab.callback.back")}
    </Link>
  );
}

function Panel({ children }: { children: ReactNode }) {
  return (
    <Card className="max-w-2xl">
      <div className="flex min-w-0 flex-col gap-4">{children}</div>
    </Card>
  );
}

/**
 * Where GitLab sends the browser back after the operator approves the sign-in: the one-time
 * `code` is exchanged for the connection's tokens. Shows what it is doing, GitLab's and the
 * server's own words when it fails, and returns to Settings > Integrations when it is done.
 */
export function GitLabCallback() {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  // Read once, and raw from the history: the router's own search string has been through its
  // JSON parsing, which turns a state like `1e5` into `100000`.
  const router = useRouter();
  const [request] = useState(() => parseGitLabCallback(router.history.location.search));
  const started = useRef(false);

  const finish = useMutation({
    mutationFn: ({ code, state }: { code: string; state: string }) => finishGitLabSignIn(code, state),
    onSuccess: (connection) => {
      toast.success(t("gitlab.callback.done", { name: connection.name, host: connection.host }));
      void queryClient.invalidateQueries({ queryKey: gitlabKeys.all });
      // Replace: the one-time code must not be one Back away from being posted again.
      void navigate({ to: "/settings/integrations", replace: true });
    },
  });

  useEffect(() => {
    // Once: the code can be exchanged a single time, and development's strict mode runs
    // every effect twice.
    if (started.current || request.kind !== "finish") return;
    started.current = true;
    finish.mutate({ code: request.code, state: request.state });
  }, [request, finish]);

  let body: ReactNode;
  if (request.kind === "invalid") {
    body = (
      <Panel>
        <div className="flex flex-col gap-1">
          <p className="text-14 font-medium text-fg">{t("gitlab.callback.waitingTitle")}</p>
          <p className="text-13 text-pretty text-fg-muted">{t("gitlab.callback.waitingDescription")}</p>
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
          <Button
            variant="primary"
            onClick={() => {
              finish.mutate({ code: request.code, state: request.state });
            }}
          >
            {t("gitlab.callback.confirmAndContinue")}
          </Button>
          <BackLink />
        </div>
      </Panel>
    );
  } else if (finish.isError) {
    body = (
      <div className="flex max-w-2xl min-w-0 flex-col gap-4">
        <ErrorBlock live error={finish.error} title={t("gitlab.callback.failed")} hint={t("gitlab.callback.hint")} />
        <div>
          <BackLink />
        </div>
      </div>
    );
  } else {
    body = (
      <JobProgress state="running" title={t("gitlab.callback.progress")} className="max-w-2xl" />
    );
  }

  return (
    <DetailPage
      header={{
        title: t("gitlab.callback.title"),
        breadcrumbs: [
          { label: t("gitlab.callback.breadcrumbSettings"), to: "/settings" },
          { label: t("gitlab.callback.breadcrumbIntegrations"), to: "/settings/integrations" },
        ],
      }}
    >
      {body}
    </DetailPage>
  );
}
