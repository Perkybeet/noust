import { Drawer as BaseDrawer } from "@base-ui/react/drawer";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { BACKDROP } from "../ui/Dialog";
import { IconButton } from "../ui/IconButton";

export interface GallerySheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}

/**
 * The gallery on a phone, where there is no column beside the page: a sheet that rises from the foot and is swiped
 * down to close. It enters decelerating and leaves accelerating, as every layer of the console does, and with reduced
 * motion it is instant.
 */
export function GallerySheet({ open, onOpenChange, title, children }: GallerySheetProps) {
  const t = useT();
  return (
    <BaseDrawer.Root swipeDirection="down" open={open} onOpenChange={(next: boolean) => onOpenChange(next)}>
      <BaseDrawer.Portal>
        <BaseDrawer.Backdrop className={BACKDROP} />
        <BaseDrawer.Viewport className="fixed inset-0 z-overlay flex items-end">
          <BaseDrawer.Popup className="sheet-popup elev-seated flex max-h-4/5 w-full flex-col rounded-t-wall border-t border-border-strong bg-bg-column text-fg outline-none">
            <div aria-hidden="true" className="mx-auto mt-2 h-1 w-10 rounded-pill bg-border-strong" />
            <div className="flex items-center gap-3 px-4 pt-2 pb-2">
              <BaseDrawer.Title className="title min-w-0 flex-1 truncate text-16 text-fg">{title}</BaseDrawer.Title>
              <BaseDrawer.Close render={<IconButton label={t("common.dialog.close")} icon={<X />} size="sm" tooltip={false} />} />
            </div>
            <BaseDrawer.Content className="flex min-h-0 flex-1 flex-col pb-2">{children}</BaseDrawer.Content>
          </BaseDrawer.Popup>
        </BaseDrawer.Viewport>
      </BaseDrawer.Portal>
    </BaseDrawer.Root>
  );
}
