import { useMutation } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ElevationCancelledError, isApiError } from "../../../api/client";
import { startGitHubManifest } from "../../../api/queries/github";
import type { GitHubManifest } from "../../../api/queries/github";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Spinner } from "../../../components/ui/Spinner";
import { organizationProblem } from "./github";

/** What an App gives this server, in the operator's words. */
const BENEFITS: readonly string[] = [
  "Private repositories, cloned with short-lived tokens instead of a deploy key per repository.",
  "Deploys on every push to the branch an application follows.",
  "A preview deployment for every pull request.",
  "Deployment statuses on commits and pull requests, so GitHub shows what is live.",
];

/**
 * The form GitHub's manifest flow needs: GitHub creates an App from a manifest a signed-in
 * browser posts to it, so the console posts one, as a real form, to the URL the server named.
 * Built by React and submitted through its element: no HTML string is ever parsed.
 */
function ManifestForm({ manifest, onBlocked }: { manifest: GitHubManifest; onBlocked: (directive: string) => void }) {
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    // A browser that refuses the post says so only in this event; without it the page would
    // wait for GitHub forever.
    const listen = (event: SecurityPolicyViolationEvent): void => {
      if (event.effectiveDirective === "form-action") onBlocked(event.originalPolicy);
    };
    document.addEventListener("securitypolicyviolation", listen);
    form.current?.submit();
    return () => {
      document.removeEventListener("securitypolicyviolation", listen);
    };
  }, [onBlocked]);
  return (
    <form ref={form} method="post" action={manifest.post_url} hidden aria-hidden="true">
      <input type="hidden" name="manifest" value={JSON.stringify(manifest.manifest)} />
    </form>
  );
}

/**
 * Creating this server's GitHub App: what it is for, where it is created, and the one button
 * that starts GitHub's manifest flow. GitHub asks the operator to confirm the App's name, then
 * sends the browser back to the console's callback page, which finishes the job.
 */
export function CreateGitHubApp() {
  const [organization, setOrganization] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [blockedBy, setBlockedBy] = useState<string | null>(null);
  const onBlocked = useCallback((policy: string) => {
    setBlockedBy(policy);
  }, []);
  const start = useMutation({
    mutationFn: (name: string) =>
      startGitHubManifest({ origin: window.location.origin, ...(name !== "" ? { organization: name } : {}) }),
    onError: (error) => {
      if (isApiError(error) && error.fields !== null) {
        const message = error.fields["organization"] ?? error.fields["origin"];
        if (message !== undefined) setFieldError(message);
      }
    },
  });

  const { reset } = start;
  useEffect(() => {
    // Back from GitHub restores this page from the browser's cache as it was left, mid-way
    // through "Opening GitHub"; it starts over instead.
    const restored = (event: PageTransitionEvent): void => {
      if (event.persisted) reset();
    };
    window.addEventListener("pageshow", restored);
    return () => {
      window.removeEventListener("pageshow", restored);
    };
  }, [reset]);

  const manifest = start.data;
  // The server names github.com; anything else is not somewhere to send this browser.
  const postable = manifest?.post_url.startsWith("https://") === true;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const name = organization.trim();
    const problem = organizationProblem(name);
    setFieldError(problem);
    if (problem !== null) return;
    setBlockedBy(null);
    start.mutate(name);
  };

  const redirecting = start.isSuccess && postable && blockedBy === null;
  const failure = start.isError && !(start.error instanceof ElevationCancelledError) && fieldError === null ? start.error : null;

  return (
    <div className="flex min-w-0 flex-col gap-5 rounded-card border border-border bg-surface p-5 shadow-raised">
      <div className="flex flex-col gap-2">
        <p className="text-14 font-medium text-fg">Connect GitHub with an App of your own</p>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-13 text-pretty text-fg-muted marker:text-fg-faint">
          {BENEFITS.map((benefit) => (
            <li key={benefit}>{benefit}</li>
          ))}
        </ul>
        <p className="text-13 text-pretty text-fg-muted">
          The App is created in your GitHub account, or in the organization you name, and belongs to no one else. Its
          private key is kept on this server and never leaves it.
        </p>
      </div>

      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label="Organization"
          optional
          error={fieldError}
          description="Leave empty to create the App in your personal account. You must be an owner of the organization."
          className="sm:max-w-80"
        >
          <Input
            mono
            value={organization}
            onValueChange={(value: string) => {
              setOrganization(value);
              setFieldError(null);
              if (start.isError) start.reset();
            }}
            placeholder="your-org"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={start.isPending || redirecting}
          />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" loading={start.isPending || redirecting}>
            Create GitHub App
          </Button>
          <p className="text-12 text-fg-muted">GitHub opens to confirm the App's name, then brings you back here.</p>
        </div>
      </form>

      {redirecting ? (
        <p role="status" className="flex items-center gap-2 text-13 text-fg">
          <Spinner size={14} className="text-warn" />
          Opening GitHub…
        </p>
      ) : null}
      {manifest !== undefined && !postable ? (
        <ErrorBlock
          live
          error={{ detail: manifest.post_url }}
          title="The App was not created: the server named an address for GitHub that is not https"
          hint="The console only sends the App's manifest to an https address. Check the GitHub URL WASM is configured with."
        />
      ) : null}
      {start.error instanceof ElevationCancelledError ? (
        <p role="status" className="text-13 text-fg-muted">
          {start.error.detail}
        </p>
      ) : null}
      {blockedBy !== null ? (
        <ErrorBlock
          live
          error={{ detail: blockedBy }}
          title="The browser refused to send the App's manifest to GitHub"
          hint="The console's Content Security Policy, below, has to allow form-action https://github.com. Update WASM, then try again."
        />
      ) : null}
      {failure !== null ? <ErrorBlock live error={failure} title="Could not start creating the App" /> : null}
      {redirecting ? <ManifestForm manifest={manifest} onBlocked={onBlocked} /> : null}
    </div>
  );
}
