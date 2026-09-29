import { useRouter } from "@tanstack/react-router";
import type { ErrorComponentProps } from "@tanstack/react-router";
import { RotateCw } from "lucide-react";
import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

import { Button } from "../components/ui/Button";
import { SystemOutput } from "../components/ui/SystemOutput";
import { useT } from "../i18n";
import { describeError } from "../lib/errors";
import { nodeErrorWords } from "../nodes/nodeErrors";
import { useNode } from "../nodes/useNode";

/**
 * What a page shows when it cannot be displayed: the system's own message verbatim, the fix
 * above it when there is one, and a way out. Used by the error boundary and by the router
 * for a failed load.
 */
export function PageError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const t = useT();
  const { node } = useNode();
  const { hint, detail, output } = describeError(error);
  // A node the central could not use is the whole story: it is named, not "this page".
  const nodeWords = nodeErrorWords(t, error, node);
  return (
    <section aria-labelledby="page-error-title" className="flex max-w-[72ch] flex-col gap-3 py-8">
      <h1 id="page-error-title" tabIndex={-1} data-page-title="" className="title text-24 text-fg outline-none">
        {nodeWords?.title ?? t("shell.pageError.title")}
      </h1>
      <p className="text-14 text-fg-muted">{nodeWords?.hint ?? hint ?? t("shell.pageError.defaultHint")}</p>
      <SystemOutput
        label={t("shell.pageError.label")}
        maxHeight="max-h-96"
        className="rounded-control border border-border bg-bg-sunken px-3 py-2.5 text-13"
      >
        {detail}
      </SystemOutput>
      {nodeWords !== null && output !== null && output.trim() !== detail.trim() ? (
        <SystemOutput
          label={t("common.errorBlock.commandOutputLabel", { title: nodeWords.title })}
          maxHeight="max-h-96"
          className="rounded-control border border-border bg-bg-sunken px-3 py-2.5 text-13"
        >
          {output}
        </SystemOutput>
      ) : null}
      <div className="flex gap-2 pt-1">
        <Button
          variant="secondary"
          icon={<RotateCw aria-hidden="true" />}
          onClick={onRetry ?? (() => {
            window.location.reload();
          })}
        >
          {onRetry ? t("shell.pageError.tryAgain") : t("shell.pageError.reload")}
        </Button>
      </div>
    </section>
  );
}

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Clears a caught error when it changes: the shell passes the path, so leaving the page does. */
  resetKey?: unknown;
}

interface ErrorBoundaryState {
  error: unknown;
}

/**
 * Keeps a render failure inside the page that failed: the sidebar, the topbar and the
 * palette stay usable, so the operator can go somewhere else.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error };
  }

  override componentDidUpdate(previous: ErrorBoundaryProps): void {
    if (previous.resetKey !== this.props.resetKey && this.state.error !== null) this.setState({ error: null });
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error("A page failed to render:", error, info.componentStack);
  }

  override render() {
    if (this.state.error !== null) {
      return (
        <PageError
          error={this.state.error}
          onRetry={() => {
            this.setState({ error: null });
          }}
        />
      );
    }
    return this.props.children;
  }
}

/** The router's error view for a route whose load or render failed. */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  return (
    <PageError
      error={error}
      onRetry={() => {
        reset();
        void router.invalidate();
      }}
    />
  );
}
