import { useState } from "react";

import { Button } from "../../../components/ui/Button";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Dialog } from "../../../components/ui/Dialog";
import { useT } from "../../../i18n";

/** What a person says they have: the two providers most companies use, then the protocols for the rest. */
export type Chosen = "entra" | "google" | "oidc" | "saml" | "directory";

export interface AddProviderDialogProps {
  /** Goes on to the form that fits what was chosen. */
  onChoose: (chosen: Chosen) => void;
  onClose: () => void;
}

/**
 * The first step of adding a way to sign in: which one the company has, said in the words of the company ("Microsoft
 * Entra ID", "a LDAP directory") and not of the protocols. The form that follows is the same one whichever the
 * route, with the name, the label and the address of the two best known already in it.
 */
export function AddProviderDialog({ onChoose, onClose }: AddProviderDialogProps) {
  const t = useT();
  const [value, setValue] = useState<Chosen>("entra");
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="md"
      title={t("ssoAdmin.add.title")}
      description={t("ssoAdmin.add.description")}
      footer={
        <>
          <Button onClick={onClose}>{t("common.words.cancel")}</Button>
          <Button
            variant="primary"
            onClick={() => {
              onChoose(value);
            }}
          >
            {t("common.words.continue")}
          </Button>
        </>
      }
    >
      <ChoiceCards<Chosen>
        legend={t("ssoAdmin.add.legend")}
        value={value}
        onValueChange={setValue}
        options={[
          { value: "entra", label: "Microsoft Entra ID", untranslated: true, description: t("ssoAdmin.add.entra") },
          { value: "google", label: "Google Workspace", untranslated: true, description: t("ssoAdmin.add.google") },
          { value: "oidc", label: t("ssoAdmin.add.oidc"), description: t("ssoAdmin.add.oidcHelp") },
          { value: "saml", label: t("ssoAdmin.add.saml"), description: t("ssoAdmin.add.samlHelp") },
          { value: "directory", label: t("ssoAdmin.add.directory"), description: t("ssoAdmin.add.directoryHelp") },
        ]}
      />
    </Dialog>
  );
}
