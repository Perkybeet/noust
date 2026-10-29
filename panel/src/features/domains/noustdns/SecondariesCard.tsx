import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { noustDnsKeys } from "../../../api/queries/noustDns";
import type { NoustServer } from "../../../api/queries/noustDns";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { IconButton } from "../../../components/ui/IconButton";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { RowList } from "../../../components/ui/RowList";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";

function AddSecondaryDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [address, setAddress] = useState("");
  const [hostname, setHostname] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      request("post", "/api/dns/noust/server/secondaries", {
        body: { address: address.trim(), ...(hostname.trim() !== "" ? { hostname: hostname.trim().toLowerCase() } : {}) },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: noustDnsKeys.server });
      close(true);
    },
  });

  function close(settled = false): void {
    if (add.isPending && !settled) return;
    onOpenChange(false);
    setAddress("");
    setHostname("");
    setProblem(null);
    add.reset();
  }

  const fields = add.error && isApiError(add.error) ? add.error.fields : null;
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (address.trim() === "") {
      setProblem(t("noustDns.fieldRequired"));
      return;
    }
    setProblem(null);
    add.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("noustDns.secondaries.addTitle")}
      footer={
        <>
          <Button disabled={add.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={add.isPending}>
            {t("noustDns.secondaries.add")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("noustDns.secondaries.addressLabel")} description={t("noustDns.secondaries.addressHelp")} error={problem ?? fields?.["address"] ?? null}>
          <Input mono value={address} onValueChange={setAddress} placeholder="198.51.100.7" autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("common.words.name")} description={t("noustDns.secondaries.hostnameHelp")} optional error={fields?.["hostname"] ?? null}>
          <Input mono value={hostname} onValueChange={setHostname} placeholder="ns2.example.net" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        {add.isError && fields === null ? <ErrorBlock live compact error={add.error} title={t("noustDns.secondaries.addFailed")} /> : null}
      </form>
    </Dialog>
  );
}

/** The transfer key a secondary is set up with. A secret: read on request (it asks for sudo mode) and shown once, here. */
function KeyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const key = useMutation({ mutationFn: () => request("get", "/api/dns/noust/server/key") });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) key.reset();
      }}
      title={t("noustDns.secondaries.keyTitle")}
      description={t("noustDns.secondaries.keyDescription")}
      footer={
        key.data === undefined ? (
          <Button variant="primary" loading={key.isPending} onClick={() => key.mutate()}>
            {t("noustDns.secondaries.showKey")}
          </Button>
        ) : (
          <Button onClick={() => onOpenChange(false)}>{t("common.words.done")}</Button>
        )
      }
    >
      {key.data !== undefined ? <CopyValue label={t("noustDns.secondaries.keyTitle")} value={key.data.secret} /> : null}
      {key.isError ? <ErrorBlock compact error={key.error} title={t("noustDns.secondaries.keyFailed")} /> : null}
    </Dialog>
  );
}

/** The primary's secondaries: the servers in other networks that copy every zone, with the key to set one up. */
export function SecondariesCard({ server }: { server: NoustServer }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const secondaries = server.secondaries ?? [];
  const [adding, setAdding] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);

  return (
    <Card
      level={2}
      title={t("noustDns.secondaries.title")}
      icon={OBJECT_ICONS.server}
      description={t("noustDns.secondaries.description")}
      actions={
        <>
          <Button size="sm" onClick={() => setShowKey(true)}>
            {t("noustDns.secondaries.showKey")}
          </Button>
          <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={() => setAdding(true)}>
            {t("noustDns.secondaries.add")}
          </Button>
        </>
      }
    >
      {secondaries.length === 0 ? (
        <EmptyState variant="inline" title={t("noustDns.secondaries.none")} />
      ) : (
        <RowList aria-label={t("noustDns.secondaries.title")}>
          {secondaries.map((secondary) => (
            <RowList.Item
              key={secondary.address}
              actions={<IconButton label={t("noustDns.secondaries.removeTitle", { address: secondary.address })} icon={<ICONS.delete />} size="sm" onClick={() => setRemoving(secondary.address)} />}
            >
              <div className="flex min-w-0 flex-col gap-0.5">
                <Mono truncate>{secondary.address}</Mono>
                {secondary.hostname ? <Mono tone="muted" truncate>{secondary.hostname}</Mono> : null}
              </div>
            </RowList.Item>
          ))}
        </RowList>
      )}
      <AddSecondaryDialog open={adding} onOpenChange={setAdding} />
      <KeyDialog open={showKey} onOpenChange={setShowKey} />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(next) => {
          if (!next) setRemoving(null);
        }}
        friction="simple"
        server={node}
        destructive
        title={t("noustDns.secondaries.removeTitle", { address: removing ?? "" })}
        description={t("noustDns.secondaries.removeDescription")}
        actionLabel={t("noustDns.secondaries.remove")}
        onConfirm={async () => {
          if (removing === null) return;
          await request("delete", "/api/dns/noust/server/secondaries/{address}", { params: { address: removing } });
          void queryClient.invalidateQueries({ queryKey: noustDnsKeys.server });
        }}
      />
    </Card>
  );
}
