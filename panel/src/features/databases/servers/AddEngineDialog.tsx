import { useState } from "react";

import { Button } from "../../../components/ui/Button";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Dialog } from "../../../components/ui/Dialog";
import { useT } from "../../../i18n";
import { MissingSoftware } from "../../server/software/MissingSoftware";

type Way = "system" | "container";

export interface AddEngineDialogProps {
  onClose: () => void;
  /** Installs an engine of the system's own (a package of the distribution or of the project). */
  onSystem: () => void;
  /** Makes an engine in a container of Noust's, in another version or of an engine the system does not have. */
  onContainer: () => void;
}

/**
 * "Add an engine": the one way in for the two things it can mean, said as what each gives you: the system's own (a package,
 * one version) or one in a container (another version, or an engine the system lacks). Choosing continues in the dialog that
 * makes it. A container needs Docker, and when it is not there the same notice every page shows for a missing tool says so,
 * with the way to install it, before the person has filled in anything.
 */
export function AddEngineDialog({ onClose, onSystem, onContainer }: AddEngineDialogProps) {
  const t = useT();
  const [way, setWay] = useState<Way>("system");
  return (
    <Dialog
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      title={t("dbServers.add.title")}
      description={t("dbServers.add.description")}
      footer={
        <>
          <Button onClick={onClose}>{t("common.words.cancel")}</Button>
          <Button
            variant="primary"
            onClick={() => {
              onClose();
              if (way === "system") onSystem();
              else onContainer();
            }}
          >
            {t("common.words.continue")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <ChoiceCards<Way>
          legend={t("dbServers.add.legend")}
          value={way}
          onValueChange={setWay}
          columns={1}
          options={[
            { value: "system", label: t("dbServers.add.system"), description: t("dbServers.add.systemLine") },
            { value: "container", label: t("dbServers.add.container"), description: t("dbServers.add.containerLine") },
          ]}
        />
        {way === "container" ? <MissingSoftware component="docker" /> : null}
      </div>
    </Dialog>
  );
}
