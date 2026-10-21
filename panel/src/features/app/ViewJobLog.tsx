import { ScrollText } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { deploymentsQuery } from "../../api/queries/deployments";
import type { Job } from "../../api/queries/jobs";
import { Button, buttonClassName } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { JobOutputDrawer } from "../databases/jobs";
import { RECENT_APP_DEPLOYS } from "./AppBanner";
import { jobWords } from "./useAppJob";

/**
 * "View log" for the job in hand on an app. Once the job has a deployment of its own the log is
 * that deployment's page (its phases, its log streamed, what failed); until it has, the job's own
 * output opens beside the page. A job that is still waiting in line has no log yet and offers none:
 * the queue says its place.
 */
export function ViewJobLog({ domain, job }: { domain: string; job: Job }) {
  const t = useT();
  // The same read as the page's banner and tab counts, so asking is free.
  const deployments = useQuery(deploymentsQuery({ domain, limit: RECENT_APP_DEPLOYS }));
  const [open, setOpen] = useState(false);
  if (job.status === "pending") return null;
  const deployment = deployments.data?.items.find((item) => item.job_id === job.id);
  if (deployment !== undefined) {
    return (
      <Link to="/apps/$domain/deployments/$id" params={{ domain, id: String(deployment.id) }} className={buttonClassName("ghost", "sm")}>
        <ScrollText aria-hidden="true" />
        {t("appPages.common.viewLog")}
      </Link>
    );
  }
  return (
    <>
      <Button size="sm" variant="ghost" icon={<ScrollText aria-hidden="true" />} onClick={() => setOpen(true)}>
        {t("appPages.common.viewLog")}
      </Button>
      <JobOutputDrawer job={job} open={open} onOpenChange={setOpen} title={jobWords(job.type, domain, t.locale).title} />
    </>
  );
}
