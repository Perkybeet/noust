import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { myTeamsQuery } from "../../api/queries/teams";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { CopyValue } from "../../components/ui/CopyValue";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { fleetKeys } from "./data";
import { rekeyNode, setNodeTeam } from "./nodeTools";
import { nodeKeys, nodesQuery } from "./nodes";

// ---------------------------------------------------------------------------------------
// A new key for a server

/**
 * Giving a server a new key, in the two steps `noust node rekey` has: this central makes the new
 * pair and spells the command to run on the server as root (which also takes the old key out
 * there), and once the server prints its join code, the code is pasted here and the central
 * switches to the new key only when the server answers with it. The code carries the server's
 * token: it is used once, kept nowhere here, and the field is emptied the moment it is sent.
 */
export function RekeyDialog({ node, onClose }: { node: string; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [code, setCode] = useState("");
  const [missing, setMissing] = useState(false);

  const prepare = useMutation({ mutationFn: () => rekeyNode(node) });
  const finish = useMutation({
    mutationFn: () => rekeyNode(node, code.trim()),
    onSuccess: () => {
      setCode("");
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
      toast.success(t("nodeTools.rekey.done", { name: node }));
      onClose();
    },
  });
  const command = prepare.data?.authorize_command ?? null;
  const busy = prepare.isPending || finish.isPending;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (busy) return;
    if (command === null) {
      prepare.mutate();
      return;
    }
    if (code.trim() === "") {
      setMissing(true);
      return;
    }
    setMissing(false);
    finish.mutate();
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
      size="lg"
      title={t("nodeTools.rekey.title", { name: node })}
      description={t("nodeTools.rekey.description", { name: node })}
      footer={
        <>
          <Button disabled={busy} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={busy}>
            {command === null ? t("nodeTools.rekey.prepare") : t("nodeTools.rekey.switch")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {command === null ? (
          <Notice>{t("nodeTools.rekey.explain", { name: node })}</Notice>
        ) : (
          <>
            <Field label={t("nodeTools.rekey.commandLabel", { name: node })} description={t("nodeTools.rekey.commandHelp")}>
              <CopyValue value={command} label={t("nodeTools.rekey.copy")} name={t("nodeTools.rekey.commandName")} />
            </Field>
            <Field label={t("nodeTools.rekey.codeLabel")} description={t("nodeTools.rekey.codeHelp")} error={missing ? t("nodeTools.rekey.codeMissing") : undefined}>
              <Textarea
                mono
                rows={4}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={code}
                onChange={(event) => {
                  setCode(event.target.value);
                }}
              />
            </Field>
          </>
        )}
        {prepare.isError ? <ErrorBlock live compact error={prepare.error} title={t("nodeTools.rekey.prepareFailed", { name: node })} /> : null}
        {finish.isError ? <ErrorBlock live compact error={finish.error} title={t("nodeTools.rekey.failed", { name: node })} /> : null}
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------
// The team that has a server

/** The value that stands for "no team": the Select needs a value to hold it. */
const INSTANCE = "";

/**
 * Giving a server to a team, or back to the instance. A team's members then see the server and
 * work on it through this central, within what their roles hold in the team and never on anything
 * that is about the machine; every other team reads the server as not there.
 */
export function NodeTeamDialog({ node, onClose }: { node: string; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const nodes = useQuery(nodesQuery());
  const teams = useQuery(myTeamsQuery());
  const current = nodes.data?.items.find((item) => item.name === node)?.team ?? INSTANCE;
  const [chosen, setChosen] = useState<string | null>(null);
  const team = chosen ?? current;

  const save = useMutation({
    mutationFn: () => setNodeTeam(node, team === INSTANCE ? null : team),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
      toast.success(team === INSTANCE ? t("nodeTools.team.releasedToast", { name: node }) : t("nodeTools.team.assignedToast", { name: node, team }));
      onClose();
    },
  });
  const options = [
    { value: INSTANCE, label: t("nodeTools.team.none") },
    ...(teams.data?.teams ?? []).map((candidate) => ({ value: candidate.slug, label: candidate.name })),
  ];

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={t("nodeTools.team.title", { name: node })}
      description={t("nodeTools.team.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={nodes.isPending || team === current}>
            {t("common.words.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (!save.isPending && team !== current) save.mutate();
        }}
      >
        <Field label={t("nodeTools.team.label")} description={t("nodeTools.team.help")} nativeLabel={false}>
          <Select value={team} onValueChange={setChosen} options={options} />
        </Field>
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("nodeTools.team.failed", { name: node })} /> : null}
      </form>
    </Dialog>
  );
}
