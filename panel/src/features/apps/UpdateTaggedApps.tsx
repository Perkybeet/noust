import { useQueryClient } from "@tanstack/react-query";
import { CircleArrowUp } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { announce } from "../../app/Announcer";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";

export interface UpdateTaggedAppsProps {
  tag: string;
  /** How many of the apps in view carry it: said in the question. */
  count: number;
}

/**
 * Updates every app that carries a tag, each as a job: what the terminal does with
 * `noust app tag deploy`, and what a pipeline does through the API. Asked once, naming how many
 * and what an update is, since it builds that many releases at once. An app with nothing new is
 * skipped unless asked to rebuild anyway; a failure to queue one is told with its own words.
 */
export function UpdateTaggedApps({ tag, count }: UpdateTaggedAppsProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [force, setForce] = useState(false);

  return (
    <>
      <Button icon={<CircleArrowUp aria-hidden="true" />} onClick={() => setOpen(true)}>
        {t("appTags.update.action")}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setForce(false);
        }}
        friction="simple"
        server={node}
        title={t("appTags.update.title", { tag })}
        description={t("appTags.update.description", { count, tag })}
        actionLabel={t("appTags.update.confirm")}
        onConfirm={async () => {
          const result = await request("post", "/api/tags/{tag}/deploy", { params: { tag }, body: { force } });
          void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
          const queued = result.results.filter((entry) => entry.outcome === "queued").length;
          const skipped = result.results.filter((entry) => entry.outcome === "skipped").length;
          const failed = result.results.filter((entry) => entry.outcome === "failed");
          if (queued > 0) {
            const said = t("appTags.update.queued", { count: queued, tag });
            announce(said);
            toast.success(said, skipped > 0 ? { description: t("appTags.update.skipped", { count: skipped }) } : {});
          } else if (failed.length === 0) {
            toast.info(t("appTags.update.nothing", { tag }), skipped > 0 ? { description: t("appTags.update.skipped", { count: skipped }) } : {});
          }
          if (failed.length > 0) {
            toast.warning(t("appTags.update.failedTitle", { count: failed.length }), {
              detail: failed.map((entry) => `${entry.domain}: ${entry.detail}`).join("\n"),
            });
          }
        }}
      >
        <Checkbox label={t("appTags.update.force")} checked={force} onCheckedChange={setForce} />
      </ConfirmDialog>
    </>
  );
}
