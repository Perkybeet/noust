import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { appPrivateQuery, networkKeys } from "../../../api/queries/network";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { useOrganization } from "../../teams/useOrganization";
import { lockoutAddress } from "./access";
import { SETTINGS_ICONS } from "./objectIcons";

/**
 * Answer a private network only: a tailnet or a WireGuard interface, named instead of its
 * ranges. It is the site's own list of allowed addresses set from here (so the Addresses card
 * shows the result), and whether a site is private is read from that list, never recorded.
 * Connected over the internet the operator would be refused too: that is asked about first.
 */
export function PrivateCard({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const org = useOrganization();
  const queryClient = useQueryClient();
  const status = useQuery(appPrivateQuery(domain));
  const [chosen, setChosen] = useState("");
  const [lockout, setLockout] = useState<{ body: { network: string | null; accept_lockout: boolean }; address: string } | null>(null);
  const data = status.data;
  const usable = (data?.available ?? []).filter((network) => network.up && network.cidrs.length > 0);
  const network = usable.some((candidate) => candidate.name === chosen) ? chosen : (usable[0]?.name ?? "");

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: networkKeys.appPrivate(domain) });
    void queryClient.invalidateQueries({ queryKey: networkKeys.policy(domain) });
  };

  const enable = useMutation({
    mutationFn: (body: { network: string | null; accept_lockout: boolean }) =>
      request("put", "/api/apps/{domain}/private", { params: { domain }, body: { cidrs: [], ...body } }),
    onSuccess: (_, body) => {
      refresh();
      setLockout(null);
      announce(t("network.access.private.enabledToast", { domain, network: body.network ?? "" }));
    },
    onError: (error, body) => {
      const address = lockoutAddress(splitErrors(error, ["allow_ips"]).fields.allow_ips);
      if (address !== null) setLockout({ body, address });
    },
  });
  const disable = useMutation({
    mutationFn: () => request("delete", "/api/apps/{domain}/private", { params: { domain }, query: { accept_lockout: false } }),
    onSuccess: () => {
      refresh();
      announce(t("network.access.private.disabledToast", { domain }));
    },
  });
  const certificate = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/private/certificate", { params: { domain } }),
    onSuccess: (issued) => {
      refresh();
      announce(t("network.access.private.certificateToast", { name: issued.hostname }));
    },
  });

  const failure = [enable, disable, certificate].find((mutation) => mutation.isError && !(mutation === enable && lockout !== null));
  const down = (data?.available ?? []).filter((candidate) => !candidate.up || candidate.cidrs.length === 0);

  return (
    <Card title={t("network.access.private.title")} icon={SETTINGS_ICONS.privateNetwork} description={t("network.access.private.description")} loading={status.isPending}>
      <div className="flex flex-col gap-4">
        {status.isError ? <ErrorBlock compact error={status.error} title={t("network.access.private.readFailed")} /> : null}
        {data !== undefined ? (
          <>
            <p className="text-14" role="status">
              {data.private
                ? t("network.access.private.stateOn", { networks: data.networks.length > 0 ? data.networks.join(", ") : data.allow_ips.join(", ") })
                : data.public_ranges.length > 0
                  ? t("network.access.private.stateMixed", { ranges: data.public_ranges.join(", ") })
                  : t("network.access.private.stateOff")}
            </p>
            {usable.length > 0 ? (
              <div className="flex flex-wrap items-end gap-3">
                <Field label={t("network.access.private.network")} nativeLabel={false}>
                  <Select
                    aria-label={t("network.access.private.network")}
                    value={network}
                    onValueChange={setChosen}
                    options={usable.map((candidate) => ({ value: candidate.name, label: `${candidate.name} (${candidate.cidrs.join(", ")})` }))}
                  />
                </Field>
                <Button icon={<SETTINGS_ICONS.turnOn aria-hidden="true" />} variant="primary" loading={enable.isPending} onClick={() => enable.mutate({ network, accept_lockout: false })}>
                  {t("network.access.private.enable")}
                </Button>
                {data.private ? (
                  <Button icon={<SETTINGS_ICONS.turnOff aria-hidden="true" />} loading={disable.isPending} onClick={() => disable.mutate()}>
                    {t("network.access.private.disable")}
                  </Button>
                ) : null}
              </div>
            ) : (
              <EmptyState variant="inline" title={t("network.access.private.none")} description={t("network.access.private.noneHelp")} />
            )}
            {down.map((candidate) => (
              <Notice key={candidate.name} tone="warning" title={t("network.access.private.down", { name: candidate.name })}>
                {candidate.problem}
              </Notice>
            ))}
            {data.private && data.tailnet_name !== "" ? (
              <div className="flex flex-col gap-2">
                <p className="text-13 text-fg-muted">
                  {t.rich("network.access.private.tailnetName", { name: <Mono>{data.tailnet_name}</Mono> })}
                </p>
                {data.tailnet_certificate !== "" ? (
                  <p className="text-13 text-fg-muted">{t("network.access.private.certificateServed", { name: data.tailnet_certificate })}</p>
                ) : org.can("server.manage") ? (
                  <div>
                    <Button icon={<SETTINGS_ICONS.certificate aria-hidden="true" />} loading={certificate.isPending} onClick={() => certificate.mutate()}>
                      {t("network.access.private.certificate")}
                    </Button>
                    <p className="mt-1 text-13 text-fg-muted">{t("network.access.private.certificateHelp")}</p>
                  </div>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
        {failure !== undefined ? <ErrorBlock live compact error={failure.error} title={t("network.access.private.failed")} /> : null}
      </div>
      <ConfirmDialog
        open={lockout !== null}
        onOpenChange={(open) => {
          if (!open) {
            setLockout(null);
            enable.reset();
          }
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.access.save.lockoutTitle")}
        description={t("network.access.save.lockoutDescription", { address: lockout?.address ?? "", domain })}
        actionLabel={t("network.access.save.lockoutAction")}
        onConfirm={async () => {
          if (lockout === null) return;
          await enable.mutateAsync({ ...lockout.body, accept_lockout: true });
        }}
      />
    </Card>
  );
}
