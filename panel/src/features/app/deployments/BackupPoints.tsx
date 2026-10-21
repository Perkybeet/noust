import { useQuery } from "@tanstack/react-query";
import { Archive, History } from "lucide-react";

import { rollbackPointsQuery } from "../../../api/queries/apps";
import type { RollbackPoint } from "../../../api/queries/apps";
import { QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { SkeletonRows } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useRollback } from "../RollbackHost";
import { versionTitle } from "../versionTitle";
import { shortCommit } from "./words";

function PointRow({ point, t }: { point: RollbackPoint; t: T }) {
  const { openRollback } = useRollback();
  const commit = shortCommit(point.git_commit);
  const title = versionTitle(point.created_at, t.locale) ?? point.id;
  return (
    <RowList.Item
      actions={
        <Button
          size="sm"
          icon={<History aria-hidden="true" />}
          onClick={() => openRollback({ backup: point.id })}
        >
          {t("appPages.deployments.releases.goBack")}
          <span className="sr-only">
            {t("appPages.deployments.releases.srBackup", { id: point.id })}
          </span>
        </Button>
      }
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="flex min-w-0 items-baseline gap-2 text-13 font-medium text-fg">
          <span className="truncate">{title}</span>
          {commit ? <Mono tone="muted">{commit}</Mono> : null}
        </span>
        <span className="min-w-0 text-12 text-fg-muted">{`${point.description}, ${formatBytes(point.size_bytes, t.locale)}`}</span>
      </div>
    </RowList.Item>
  );
}

/**
 * The backups an app kept in a single folder can go back to, under its history: it has no
 * releases to switch to, so going back restores one. Each row asks the page's one question.
 */
export function BackupPoints({ domain }: { domain: string }) {
  const t = useT();
  const points = useQuery(rollbackPointsQuery(domain));
  return (
    <Section
      title={t("appPages.deployments.releases.rollbackPointsTitle")}
      icon={Archive}
      description={t.rich(
        "appPages.deployments.releases.rollbackPointsDescription",
        {
          enableReleases: (
            <TextLink to="/apps/$domain/settings/deploys" params={{ domain }}>
              {t("appPages.deployments.releases.enableReleases")}
            </TextLink>
          ),
        },
      )}
    >
      <QueryState
        query={points}
        label={t("appPages.deployments.releases.backupsLabel")}
        skeleton={<SkeletonRows rows={3} />}
        isEmpty={(data) => data.items.length === 0}
        empty={
          <EmptyState
            variant="inline"
            title={t("appPages.deployments.releases.noBackupsYet")}
            action={
              <TextLink to="/backups" size="ui">
                {t("appPages.deployments.releases.openBackups")}
              </TextLink>
            }
          />
        }
      >
        {(data) => (
          <RowList
            aria-label={t("appPages.deployments.releases.backupsAriaLabel", {
              domain,
            })}
          >
            {data.items.map((point) => (
              <PointRow key={point.id} point={point} t={t} />
            ))}
          </RowList>
        )}
      </QueryState>
    </Section>
  );
}
