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
import { parseCallback } from "./github";
import type { CallbackRequest } from "./github";

const BREADCRUMBS = [
  { label: "Settings", to: "/settings" },
  { label: "Integrations", to: "/settings/integrations" },
] as const;

type Actionable = Extract<CallbackRequest, { kind: "conversion" | "installation" }>;

const PROGRESS: Record<Actionable["kind"], string> = {
  conversion: "Finishing the App with GitHub's answer",
  installation: "Recording the installation",
};

const FAILED: Record<Actionable["kind"], string> = {
  conversion: "The GitHub App was not created on this server",
  installation: "The installation was not recorded",
};

function BackLink({ primary = false }: { primary?: boolean }) {
  return (
    <Link to="/settings/integrations" replace className={buttonClassName(primary ? "primary" : "secondary")}>
      Back to integrations
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
        return `Created the GitHub App ${status.name ?? status.slug ?? ""}`.trim();
      }
      const installation = await addGitHubInstallation(next.installationId);
      return next.setupAction === "update"
        ? `Updated the installation on ${installation.account}`
        : `Installed the GitHub App on ${installation.account}`;
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
          <p className="text-14 font-medium text-fg">Waiting for an organization owner</p>
          <p className="text-13 text-pretty text-fg-muted">
            GitHub sent the installation to the organization's owners for approval. Once one of them approves it, sync
            the installations in Settings, Integrations.
          </p>
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
          <p className="text-14 font-medium text-fg">Nothing to finish here</p>
          <p className="text-13 text-pretty text-fg-muted">
            GitHub sends the browser to this page after creating or installing the App, with what it needs in the
            address. This address carries neither, so there is nothing to record. Start from Settings, Integrations.
          </p>
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
            Confirm and continue
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
          title={FAILED[request.kind]}
          hint={
            request.kind === "conversion"
              ? "GitHub's code works once and for an hour, and the creation must finish within ten minutes of starting. Start again from Settings, Integrations."
              : "Check that the App was installed from this server's App page, then sync the installations."
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
          {`${PROGRESS[request.kind]}…`}
        </p>
      </Panel>
    );
  }

  return (
    <>
      <PageHeader title="Connecting GitHub" breadcrumbs={BREADCRUMBS} />
      {body}
    </>
  );
}
