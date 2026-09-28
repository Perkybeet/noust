import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { serviceKeys } from "../../api/queries/services";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";

type Mode = "simple" | "advanced";

const RESTART_OPTIONS = ["always", "on-failure", "on-abnormal", "on-abort", "on-watchdog", "on-success", "no"] as const;

interface EnvRow {
  id: number;
  key: string;
  value: string;
}

let nextRowId = 0;

function emptyRow(): EnvRow {
  nextRowId += 1;
  return { id: nextRowId, key: "", value: "" };
}

const RAW_TEMPLATE = `[Unit]
Description=

[Service]
Type=simple
User=
WorkingDirectory=
ExecStart=
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
`;

export interface CreateServiceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Creates a systemd unit: simple mode fills in a template from a command, a directory, a user
 * and environment; advanced mode writes the unit file directly. Both go through `POST
 * /api/services`, which enables the unit once it is written.
 */
export function CreateServiceDialog({ open, onOpenChange }: CreateServiceDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>("simple");
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [directory, setDirectory] = useState("/var/www");
  const [user, setUser] = useState("");
  const [restart, setRestart] = useState<(typeof RESTART_OPTIONS)[number]>("always");
  const [env, setEnv] = useState<EnvRow[]>([]);
  const [raw, setRaw] = useState(RAW_TEMPLATE);

  const create = useMutation({
    mutationFn: () =>
      mode === "simple"
        ? request("post", "/api/services", {
            body: {
              name,
              command,
              working_directory: directory,
              restart,
              ...(user.trim() !== "" ? { user: user.trim() } : {}),
              environment: Object.fromEntries(
                env.filter((row) => row.key.trim() !== "").map((row) => [row.key.trim(), row.value]),
              ),
            },
          })
        : request("post", "/api/services", {
            // working_directory and restart are unused by the backend in raw mode (it writes
            // raw_content verbatim) but the generated type still requires them in the body.
            body: { name, raw_content: raw, working_directory: "/var/www", restart: "always" },
          }),
    onSuccess: (result) => {
      toast.success(t("services.createDialog.createdToast", { name: result.service }), {
        description: t("services.createDialog.createdDescription"),
      });
      void queryClient.invalidateQueries({ queryKey: serviceKeys.all });
      // Not close(false): that guards on create.isPending, which this closure still reads as
      // true (nothing has re-rendered between the mutation resolving and this callback), so
      // the guard meant for Cancel/backdrop-dismiss during a submit would also swallow the
      // deliberate close after a successful one. onOpenChange is unguarded on purpose.
      reset();
      onOpenChange(false);
    },
  });

  const reset = (): void => {
    setMode("simple");
    setName("");
    setCommand("");
    setDirectory("/var/www");
    setUser("");
    setRestart("always");
    setEnv([]);
    setRaw(RAW_TEMPLATE);
    create.reset();
  };

  const close = (next: boolean): void => {
    if (!next && create.isPending) return;
    onOpenChange(next);
    if (!next) reset();
  };

  const valid = name.trim() !== "" && (mode === "advanced" ? raw.trim() !== "" : command.trim() !== "");

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={t("services.createDialog.title")}
      description={t("services.createDialog.description")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => close(false)}>
            {t("services.cancel")}
          </Button>
          <Button variant="primary" disabled={!valid} loading={create.isPending} onClick={() => create.mutate()}>
            {t("services.createDialog.create")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <Field label={t("services.createDialog.nameLabel")} name="name" className="max-w-64 flex-1">
            <Input value={name} onValueChange={setName} mono placeholder="my-worker" autoComplete="off" spellCheck={false} />
          </Field>
          <SegmentedControl
            label={t("services.createDialog.modeLabel")}
            value={mode}
            onValueChange={setMode}
            options={[
              { value: "simple", label: t("services.createDialog.modeSimple") },
              { value: "advanced", label: t("services.createDialog.modeAdvanced") },
            ]}
          />
        </div>

        {mode === "simple" ? (
          <>
            <Field label={t("services.createDialog.commandLabel")} name="command" description={t("services.createDialog.commandDescription")}>
              <Input
                value={command}
                onValueChange={setCommand}
                mono
                placeholder="/usr/bin/node /var/www/worker/index.js"
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("services.createDialog.directoryLabel")} name="directory">
                <Input value={directory} onValueChange={setDirectory} mono autoComplete="off" spellCheck={false} />
              </Field>
              <Field label={t("services.createDialog.userLabel")} name="user" optional description={t("services.createDialog.userDescription")}>
                <Input value={user} onValueChange={setUser} mono autoComplete="off" spellCheck={false} />
              </Field>
            </div>
            <Field label={t("services.createDialog.restartLabel")} name="restart" nativeLabel={false}>
              <Select
                aria-label={t("services.createDialog.restartLabel")}
                value={restart}
                onValueChange={setRestart}
                mono
                options={RESTART_OPTIONS.map((value) => ({ value, label: value }))}
              />
            </Field>
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-13 font-medium text-fg">{t("services.createDialog.environment")}</span>
                <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={() => setEnv((rows) => [...rows, emptyRow()])}>
                  {t("services.createDialog.addVariable")}
                </Button>
              </div>
              {env.length === 0 ? (
                <p className="text-13 text-fg-muted">{t("services.createDialog.noEnvironment")}</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {env.map((row) => (
                    <div key={row.id} className="flex items-center gap-2">
                      <Input
                        aria-label={t("services.createDialog.variableName")}
                        value={row.key}
                        onValueChange={(value) =>
                          setEnv((rows) => rows.map((r) => (r.id === row.id ? { ...r, key: value } : r)))
                        }
                        mono
                        placeholder={t("services.createDialog.variableNamePlaceholder")}
                        className="w-40"
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <Input
                        aria-label={t("services.createDialog.variableValue")}
                        value={row.value}
                        onValueChange={(value) =>
                          setEnv((rows) => rows.map((r) => (r.id === row.id ? { ...r, value } : r)))
                        }
                        mono
                        placeholder={t("services.createDialog.variableValuePlaceholder")}
                        className="flex-1"
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <IconButton
                        label={
                          row.key
                            ? t("services.createDialog.removeVariableNamed", { name: row.key })
                            : t("services.createDialog.removeVariable")
                        }
                        icon={<Trash2 />}
                        size="sm"
                        onClick={() => setEnv((rows) => rows.filter((r) => r.id !== row.id))}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        ) : (
          <Field label={t("services.createDialog.unitFileLabel")} name="raw" description={t("services.createDialog.unitFileDescription")}>
            <Textarea mono rows={14} value={raw} onChange={(event) => setRaw(event.target.value)} spellCheck={false} />
          </Field>
        )}

        {create.isError ? <ErrorBlock live compact error={create.error} title={t("services.createDialog.createFailed")} /> : null}
      </div>
    </Dialog>
  );
}
