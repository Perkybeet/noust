/**
 * Looking for new versions of the images on every server of the fleet: the plan first (which servers take part, which are left out and
 * why, whether sudo mode is needed), and the look only when it is confirmed. It is the bulk-action plan of the fleet's pages, asked of
 * the route of its own: one job of the central that asks each server for its own look, a few servers at a time. This central's own
 * Docker is not in it (its own pages look at it), and a server before 4.3 or without Docker is a line of the plan and not a failure.
 */

import { useQuery } from "@tanstack/react-query";

import { lookAcrossFleet } from "../../../api/queries/dockerFleet";
import { QueryState } from "../../../components/page/QueryState";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { Accepted } from "../../databases/jobs";
import { PlanView } from "../../fleet/BulkActionDialog";

export interface FleetLookDialogProps {
  /** Whether to download what moved to compare (for a Docker that cannot ask a registry without downloading). */
  pull: boolean;
  onClose: () => void;
  /** Called with the job the central queued, so the page that asked follows it. */
  onQueued: (accepted: Accepted) => void;
}

export function FleetLookDialog({ pull, onClose, onQueued }: FleetLookDialogProps) {
  const t = useT();
  // The plan is a question and not an action: it queues nothing and takes no sudo mode, so it is asked as the dialog opens.
  const plan = useQuery({
    queryKey: ["docker", "fleet-look-plan", pull],
    queryFn: () => lookAcrossFleet({ plan: true, pull, nodes: [], labels: {} }),
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const toRun = plan.data?.plan.summary["run"] ?? 0;
  return (
    <ConfirmDialog
      friction="simple"
      destructive={false}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("docker.versions.fleet.title")}
      description={t("docker.versions.fleet.description")}
      actionLabel={t("docker.versions.look")}
      ready={toRun > 0}
      onConfirm={async () => {
        const answer = await lookAcrossFleet({ plan: false, pull, nodes: [], labels: {} });
        if (answer.job !== null && answer.job !== undefined) onQueued(answer.job);
      }}
    >
      <QueryState
        query={plan}
        sentences={{ loading: t("docker.versions.fleet.planning"), failed: t("docker.versions.fleet.planFailed"), refreshFailed: t("docker.versions.fleet.planFailed") }}
        skeleton={<Skeleton className="h-32 w-full" />}
      >
        {(answer) => <PlanView t={t} plan={answer.plan} action="docker_images_check" />}
      </QueryState>
    </ConfirmDialog>
  );
}
