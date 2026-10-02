import { CornerDownRight, FileText } from "lucide-react";

import { isApiError } from "../../api/client";
import type { SiteParseFailure } from "../../api/queries/sites";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";

/**
 * A 404 from a route the site itself answered a moment ago: the server has no such route, an
 * older Noust (a 3.1 node reached through a 3.2 central).
 */
export function isMissingRoute(error: unknown): boolean {
  return isApiError(error) && error.status === 404;
}

/** "This server runs an older Noust": the text still works, and is one click away. */
export function OlderNoust({ onText }: { onText: () => void }) {
  const t = useT();
  const { node } = useNode();
  return (
    <Notice
      title={t("domains.siteViews.olderNoustTitle")}
      action={
        <Button size="sm" icon={<FileText aria-hidden="true" />} onClick={onText}>
          {t("domains.siteViews.editAsText")}
        </Button>
      }
    >
      {node === null ? t("domains.siteViews.olderNoustHere") : t("domains.siteViews.olderNoustOnNode", { node })}
    </Notice>
  );
}

/**
 * A draft the analyzer cannot read: its own words, the line and column, and the way back to
 * the text. Never half a structure: what follows the error could mean anything.
 */
export function NotParsed({ failure, webserver, onGoToText }: { failure: SiteParseFailure; webserver: string; onGoToText: (line: number) => void }) {
  const t = useT();
  return (
    <ErrorBlock
      compact
      title={t("domains.siteViews.notParsedTitle")}
      hint={t("domains.siteViews.notParsedHint", { line: failure.line, column: failure.column, webserver })}
      error={{ detail: failure.message }}
      action={
        <Button size="sm" icon={<CornerDownRight aria-hidden="true" />} onClick={() => onGoToText(failure.line)}>
          {t("domains.siteViews.goToText")}
        </Button>
      }
    />
  );
}

/** A view reading the site's model: shaped like cards, saying what it reads. */
export function ViewLoading() {
  const t = useT();
  return (
    <LoadingRegion label={t("domains.siteViews.loadingSr")}>
      <Skeleton className="h-40 rounded-card" />
      <Skeleton className="h-64 rounded-card" />
    </LoadingRegion>
  );
}
