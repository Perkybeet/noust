/**
 * The key Noust signs in to code hosts with (backlog W: made from the console, not only from the
 * command line). A private repository over ssh needs its public half added at GitHub, GitLab or
 * wherever it lives, so it is shown whole to copy; a server that has none makes one with a press,
 * and a key that exists is never replaced.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createServerKey, deployKeyKeys, serverKeyQuery } from "../../../api/queries/deployKeys";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { CopyValue } from "../../../components/ui/CopyValue";
import { EmptyState } from "../../../components/ui/EmptyState";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ServerQueryState, explainServerError } from "../errors";

export function ServerKeyCard() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const key = useQuery(serverKeyQuery());
  const make = useMutation({
    mutationFn: createServerKey,
    onSuccess: (made) => queryClient.setQueryData(deployKeyKeys.server, made),
    onError: (error) => reportActionError(t("server.serverKey.createFailed"), explainServerError(t, error, node)),
  });
  return (
    <ServerQueryState query={key} label={t("server.serverKey.label")} skeleton={<SkeletonCard lines={3} />}>
      {(data) => (
        <Card level={2} title={t("server.serverKey.title")} icon={OBJECT_ICONS.key} description={t("server.serverKey.description")} padding="sm">
          {data.public_key === null || data.public_key === undefined ? (
            <EmptyState
              variant="inline"
              title={t("server.serverKey.none")}
              action={
                <Button loading={make.isPending} onClick={() => make.mutate()}>
                  {t("server.serverKey.create")}
                </Button>
              }
            />
          ) : (
            <div className="flex min-w-0 flex-col gap-3">
              <CopyValue value={data.public_key} label={t("server.serverKey.copy")} />
              {data.fingerprint !== null && data.fingerprint !== undefined ? <KeyValueList items={[{ label: t("server.serverKey.fingerprint"), value: data.fingerprint }]} /> : null}
              <p className="text-13 text-fg-muted">{t("server.serverKey.addAt")}</p>
            </div>
          )}
        </Card>
      )}
    </ServerQueryState>
  );
}
