import { useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { VERB_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { Panel } from "./panels";
import type { Panels } from "./usePanels";

/**
 * What a person can do with one of their own panels besides arranging it: rename it, or delete it. The two that come
 * with the console have neither (they are put back as they came, from the button of the page): it gives no menu for them.
 * The menu is the page header's "More actions"; the questions it asks are drawn beside the page.
 */
export function usePanelActions(panel: Panel, panels: Panels, afterDelete: () => void): { overflow: ReactNode; dialogs: ReactNode } {
  const t = useT();
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [name, setName] = useState(panel.name);
  const [problem, setProblem] = useState<string | null>(null);
  if (panel.builtin) return { overflow: undefined, dialogs: null };

  const rename = (event: SyntheticEvent): void => {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed === "") {
      setProblem(t("overview.panels.nameRequired"));
      return;
    }
    panels.rename(panel, trimmed);
    setRenaming(false);
  };

  return {
    overflow: (
      <>
        <MenuItem
          icon={<VERB_ICONS.edit />}
          onClick={() => {
            setName(panel.name);
            setProblem(null);
            setRenaming(true);
          }}
        >
          {t("overview.panels.rename")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem destructive icon={<ICONS.delete />} onClick={() => setDeleting(true)}>
          {t("overview.panels.delete")}
        </MenuItem>
      </>
    ),
    dialogs: (
      <>
        <Dialog
          size="sm"
          open={renaming}
          onOpenChange={setRenaming}
          title={t("overview.panels.renameTitle")}
          footer={
            <>
              <Button variant="secondary" onClick={() => setRenaming(false)}>
                {t("common.words.cancel")}
              </Button>
              <Button variant="primary" type="submit" form="rename-panel">
                {t("overview.panels.rename")}
              </Button>
            </>
          }
        >
          <form id="rename-panel" onSubmit={rename} noValidate>
            <Field label={t("common.words.name")} error={problem}>
              <Input
                value={name}
                maxLength={60}
                autoComplete="off"
                onValueChange={(value: string) => {
                  setName(value);
                  setProblem(null);
                }}
              />
            </Field>
          </form>
        </Dialog>
        <ConfirmDialog
          friction="simple"
          open={deleting}
          onOpenChange={setDeleting}
          title={t("overview.panels.deleteTitle", { name: panel.name })}
          description={t("overview.panels.deleteDescription")}
          actionLabel={t("overview.panels.delete")}
          destructive
          onConfirm={() => {
            panels.remove(panel);
            afterDelete();
            return Promise.resolve();
          }}
        />
      </>
    ),
  };
}
