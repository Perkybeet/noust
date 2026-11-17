import { useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { announce } from "../../app/Announcer";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";

export interface RestartTaggedAppsProps {
  tag: string;
  /** How many of the apps in view carry it: said in the question. */
  count: number;
}

/**
 * Restarts everything that carries a tag: the apps in view and, for the instance, the server's
 * units, one by one, the way `noust app tag restart` and `POST /api/tags/{tag}/restart` do. A
 * database that carries it is left, and the answer says so. Asked once, since it interrupts that
 * many services at the same time; one that fails does not stop the others, and is told with the
 * server's own words.
 */
export function RestartTaggedApps({ tag, count }: RestartTaggedAppsProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button icon={<RotateCw aria-hidden="true" />} onClick={() => setOpen(true)}>
        {t("appTags.restart.action")}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        friction="simple"
        server={node}
        title={t("appTags.restart.title", { tag })}
        description={t("appTags.restart.description", { count, tag })}
        actionLabel={t("appTags.restart.confirm")}
        onConfirm={async () => {
          const result = await request("post", "/api/tags/{tag}/restart", { params: { tag }, body: { dry_run: false } });
          void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
          const failed = result.results.filter((entry) => entry.outcome === "failed");
          const left = result.results.filter((entry) => entry.outcome === "skipped");
          if (result.restarted > 0) {
            const said = t("appTags.restart.done", { count: result.restarted, tag });
            announce(said);
            toast.success(said, left.length > 0 ? { description: t("appTags.restart.left", { count: left.length }) } : {});
          } else if (failed.length === 0) {
            toast.info(t("appTags.restart.nothing", { tag }), left.length > 0 ? { description: t("appTags.restart.left", { count: left.length }) } : {});
          }
          if (failed.length > 0) {
            toast.warning(t("appTags.restart.failedTitle", { count: failed.length }), {
              detail: failed.map((entry) => `${entry.name}: ${entry.detail}`).join("\n"),
            });
          }
        }}
      />
    </>
  );
}
