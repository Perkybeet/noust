import { Input as BaseInput } from "@base-ui/react/input";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { ChangeEvent, SyntheticEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { CopyValue } from "../../components/ui/CopyValue";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { cloudAccountsQuery, cloudKeys } from "../cloud/data";
import { fleetKeys } from "./data";
import {
  BASTION,
  applyInventory,
  connectionKey,
  connectionQuery,
  isInventoryPart,
  linkMachine,
  parseBastionKey,
  parseTimeout,
  saveConnection,
} from "./nodeTools";
import type { ConnectionBody, ConnectionOptions, InventoryReport } from "./nodeTools";
import { nodeKeys } from "./nodes";
import { LoadingRegion } from "../../components/page/LoadingRegion";

// ---------------------------------------------------------------------------------------
// How the central connects to a server

function ConnectionForm({ node, options, onClose }: { node: string; options: ConnectionOptions; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const firstRef = useRef<HTMLInputElement>(null);
  const [timeout, setTimeoutText] = useState(options.connect_timeout === null || options.connect_timeout === undefined ? "" : String(options.connect_timeout));
  const [through, setThrough] = useState(options.bastion !== null && options.bastion !== undefined);
  const [address, setAddress] = useState(options.bastion?.address ?? "");
  const [key, setKey] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"timeout" | "address" | "key", string>>>({});

  const save = useMutation({
    mutationFn: (body: ConnectionBody) => saveConnection(node, body),
    onSuccess: (saved) => {
      queryClient.setQueryData(connectionKey(node), saved);
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
      toast.success(t("nodeTools.connection.saved", { name: node }));
    },
  });
  const saved = save.data;
  const current = saved ?? options;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    const found: typeof errors = {};
    const wait = parseTimeout(timeout);
    const parsedKey = parseBastionKey(key);
    if (wait === undefined) found.timeout = t("nodeTools.connection.timeoutInvalid");
    if (through && !BASTION.test(address.trim())) found.address = t("nodeTools.connection.bastionAddressInvalid");
    // The pinned key is kept only for the bastion it was pinned for: any other is never trusted on first use.
    const keptFor = options.bastion?.address;
    if (through && (parsedKey === undefined || (parsedKey === null && keptFor !== address.trim()))) found.key = t("nodeTools.connection.bastionKeyRequired");
    setErrors(found);
    if (Object.keys(found).length > 0 || wait === undefined) {
      firstRef.current?.focus();
      return;
    }
    save.mutate({
      connect_timeout: wait,
      bastion: through ? { address: address.trim(), ...(parsedKey ?? {}) } : null,
      test: true,
    });
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="lg"
      initialFocus={firstRef}
      title={t("nodeTools.connection.title", { name: node })}
      description={t("nodeTools.connection.description", { name: node })}
      footer={
        saved !== undefined ? (
          <Button variant="primary" onClick={onClose}>
            {t("common.words.close")}
          </Button>
        ) : (
          <>
            <Button disabled={save.isPending} onClick={onClose}>
              {t("common.words.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
              {t("nodeTools.connection.save")}
            </Button>
          </>
        )
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <Field
          label={t("nodeTools.connection.timeoutLabel")}
          description={t("nodeTools.connection.timeoutDescription", { seconds: options.default_timeout })}
          error={errors.timeout}
          optional
          className="max-w-xs"
        >
          <Input
            ref={firstRef}
            mono
            inputMode="numeric"
            autoComplete="off"
            placeholder={String(options.default_timeout)}
            value={timeout}
            onValueChange={(value: string) => {
              setTimeoutText(value);
            }}
          />
        </Field>
        <Checkbox
          label={t("nodeTools.connection.bastionToggle")}
          description={t("nodeTools.connection.bastionToggleHelp")}
          checked={through}
          onCheckedChange={setThrough}
        />
        {through ? (
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label={t("nodeTools.connection.bastionAddressLabel")} description={t("nodeTools.connection.bastionAddressDescription")} error={errors.address}>
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder="hop@bastion.example.com:22"
                value={address}
                onValueChange={(value: string) => {
                  setAddress(value);
                }}
              />
            </Field>
            <Field
              label={t("nodeTools.connection.bastionKeyLabel")}
              description={
                options.bastion !== null && options.bastion !== undefined
                  ? `${t("nodeTools.connection.bastionKeyDescription")} ${t("nodeTools.connection.bastionKeyKept", { address: options.bastion.address })}`
                  : t("nodeTools.connection.bastionKeyDescription")
              }
              error={errors.key}
            >
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder="SHA256:..."
                value={key}
                onValueChange={(value: string) => {
                  setKey(value);
                }}
              />
            </Field>
          </div>
        ) : null}
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("nodeTools.connection.failed", { name: node })} /> : null}
        {current.bastion !== null && current.bastion !== undefined ? (
          <Subsection
            title={t("nodeTools.connection.authorizedTitle")}
            description={t("nodeTools.connection.authorizedDescription", { user: current.bastion.user, name: node })}
          >
            <p className="text-12 text-fg-muted">{t("nodeTools.connection.pinned", { fingerprint: current.bastion.fingerprint })}</p>
            {current.authorized_key_line !== null && current.authorized_key_line !== undefined ? (
              <CopyValue value={current.authorized_key_line} label={t("nodeTools.connection.authorizedCopy")} />
            ) : null}
          </Subsection>
        ) : null}
      </form>
    </Dialog>
  );
}

/** How this central connects to a server: its wait, and a bastion to go through. */
export function ConnectionDialog({ node, onClose }: { node: string; onClose: () => void }) {
  const t = useT();
  const query = useQuery(connectionQuery(node));
  if (query.data !== undefined) return <ConnectionForm node={node} options={query.data} onClose={onClose} />;
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="lg"
      title={t("nodeTools.connection.title", { name: node })}
      description={t("nodeTools.connection.description", { name: node })}
      footer={<Button onClick={onClose}>{t("common.words.close")}</Button>}
    >
      {query.isError ? (
        <ErrorBlock live error={query.error} title={t("nodeTools.connection.loadFailed", { name: node })} onRetry={() => void query.refetch()} />
      ) : (
        <LoadingRegion label={t("nodeTools.connection.loading", { name: node })} className="flex flex-col gap-3">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-10 w-full" />
        </LoadingRegion>
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// An inventory from another central

const FILE_CLASS =
  "max-w-full rounded-control text-13 text-fg-muted file:mr-3 file:h-control-sm file:cursor-pointer file:rounded-control file:border file:border-border-strong file:bg-surface file:px-3 file:text-13 file:font-medium file:text-fg hover:file:bg-surface-hover";

function InventoryReportView({ report }: { report: InventoryReport }) {
  const t = useT();
  const label = (part: string): string => (isInventoryPart(part) ? t(`nodeTools.inventory.part.${part}`) : part);
  if (report.applied.length === 0 && report.skipped.length === 0) return <EmptyState variant="inline" title={t("nodeTools.inventory.nothing")} />;
  return (
    <div className="flex flex-col gap-4">
      {report.applied.length > 0 ? (
        <Subsection title={t("nodeTools.inventory.appliedLegend")}>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-13 text-fg">
            {report.applied.map((item) => (
              <li key={item.part}>{label(item.part)}</li>
            ))}
          </ul>
        </Subsection>
      ) : null}
      {report.skipped.length > 0 ? (
        <Subsection title={t("nodeTools.inventory.skippedLegend")}>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-13 text-fg-muted">
            {report.skipped.map((item) => (
              <li key={item.part}>
                <span className="font-medium text-fg">{label(item.part)}</span>
                {item.reason !== null && item.reason !== undefined ? `: ${item.reason}` : ""}
              </li>
            ))}
          </ul>
        </Subsection>
      ) : null}
    </div>
  );
}

/**
 * Applying the inventory another central exported for this server: its labels, placements and the
 * rest are recorded here. The file is read in the browser and sent as the document; it holds no
 * secret, and the central checks it whole before it writes anything.
 */
export function InventoryDialog({ node, onClose }: { node: string; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [document, setDocument] = useState<Record<string, unknown> | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [includeSsh, setIncludeSsh] = useState(false);

  const apply = useMutation({
    mutationFn: () => applyInventory(node, document ?? {}, includeSsh),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
    },
  });

  const read = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file === undefined) return;
    setProblem(null);
    void file
      .text()
      .then((text) => {
        const parsed: unknown = JSON.parse(text);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("not an object");
        setDocument(parsed as Record<string, unknown>);
      })
      .catch(() => {
        setDocument(null);
        setProblem(t("nodeTools.inventory.fileUnreadable"));
      });
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (apply.isPending || apply.isSuccess) return;
    if (document === null) {
      setProblem((current) => current ?? t("nodeTools.inventory.fileMissing"));
      return;
    }
    apply.mutate();
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !apply.isPending) onClose();
      }}
      size="md"
      title={t("nodeTools.inventory.applyTitle", { name: node })}
      description={t("nodeTools.inventory.applyDescription")}
      footer={
        apply.isSuccess ? (
          <Button variant="primary" onClick={onClose}>
            {t("common.words.close")}
          </Button>
        ) : (
          <>
            <Button disabled={apply.isPending} onClick={onClose}>
              {t("common.words.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={apply.isPending}>
              {t("nodeTools.inventory.apply")}
            </Button>
          </>
        )
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-4">
        {apply.isSuccess ? (
          <InventoryReportView report={apply.data} />
        ) : (
          <>
            <Field label={t("nodeTools.inventory.fileLabel")} description={t("nodeTools.inventory.fileDescription")} error={problem}>
              <BaseInput type="file" accept=".json,application/json" onChange={read} className={FILE_CLASS} />
            </Field>
            <Checkbox label={t("nodeTools.inventory.includeSsh")} description={t("nodeTools.inventory.includeSshHelp")} checked={includeSsh} onCheckedChange={setIncludeSsh} />
            {apply.isError ? <ErrorBlock live compact error={apply.error} title={t("nodeTools.inventory.failed", { name: node })} /> : null}
          </>
        )}
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// Its machine at a provider

/** Linking a server of the fleet to its machine at a cloud provider. Sudo mode, asked when it is sent. */
export function LinkMachineDialog({ node, onClose }: { node: string; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const accounts = useQuery(cloudAccountsQuery());
  const [account, setAccount] = useState("");
  const [machine, setMachine] = useState("");
  const [force, setForce] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const names = (accounts.data ?? []).map((candidate) => candidate.name);
  const chosen = account === "" ? (names[0] ?? "") : account;

  const link = useMutation({
    mutationFn: () => linkMachine(node, { account: chosen, provider_id: machine.trim(), force }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: cloudKeys.machines });
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      toast.success(t("nodeTools.link.linked", { name: node, machine: machine.trim() }));
      onClose();
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (link.isPending) return;
    if (machine.trim() === "" || chosen === "") {
      setProblem(t("nodeTools.link.machineInvalid"));
      return;
    }
    setProblem(null);
    link.mutate();
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !link.isPending) onClose();
      }}
      size="md"
      title={t("nodeTools.link.title", { name: node })}
      description={t("nodeTools.link.description", { name: node })}
      footer={
        <>
          <Button disabled={link.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          {/* design-exception: submit-disabled-invalid the central holds no provider account to link with, which the dialog says: there is no field to complete */}
          <Button type="submit" form={formId} variant="primary" loading={link.isPending} disabled={names.length === 0}>
            {t("nodeTools.link.link")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-4">
        {names.length === 0 && !accounts.isPending ? (
          <Notice tone="warning">{t("nodeTools.link.noAccounts")}</Notice>
        ) : (
          <>
            <Field label={t("nodeTools.link.accountLabel")} nativeLabel={false}>
              <Select value={chosen === "" ? null : chosen} onValueChange={setAccount} options={names.map((name) => ({ value: name, label: name }))} />
            </Field>
            <Field label={t("nodeTools.link.machineLabel")} description={t("nodeTools.link.machineDescription")} error={problem}>
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={machine}
                onValueChange={(value: string) => {
                  setMachine(value);
                }}
              />
            </Field>
            <Checkbox label={t("nodeTools.link.force")} description={t("nodeTools.link.forceHelp")} checked={force} onCheckedChange={setForce} />
          </>
        )}
        {link.isError ? <ErrorBlock live compact error={link.error} title={t("nodeTools.link.failed", { name: node })} /> : null}
      </form>
    </Dialog>
  );
}
