import { useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { MenuItem, MenuSeparator } from "../ui/Menu";
import type { OwnerTarget } from "./OwnerMark";
import { RowMenu } from "./RowMenu";

/** One thing the menu offers, already known to work on this object: a verb, its icon and what it does. */
export interface ObjectAction {
  id: string;
  label: string;
  /** The verb's icon as an element (`<VERB_ICONS.turnOff />`), decorative. */
  icon: ReactNode;
  /** One sentence under the label for what the operator would otherwise stop to ask. */
  description?: string;
  onSelect: () => void;
  /** What removes something: drawn last, after a separator, in the failure colour. */
  destructive?: boolean;
}

/** A line that leads to the owner, for what is changed there and not here. */
export interface ViaOwner {
  label: string;
  /** Which verbs it stands for: "Restarting, stopping and removing are done from the application". */
  description: string;
  icon: ReactNode;
  target: OwnerTarget;
}

export interface ObjectMenuProps {
  /** The object, as the row names it: "shop-example-com-web-1". Not translated. */
  name: string;
  /** What this object lets be done here, in the order drawn. The caller has already left out what the server's `can` refuses. */
  actions: readonly ObjectAction[];
  viaOwner?: ViaOwner | undefined;
}

/**
 * The menu at the end of a row of an object that has an owner (a container, an image, a volume, a network, a stack): it offers
 * what works here and nothing else, because a verb the owner takes (restarting an application's container would fight its
 * health gate) is not made available and then refused. What the owner does is said once, as one line that leads to the owner,
 * and never as a disabled copy of each verb on every row (DESIGN PM-1). An object that nothing can be done to draws no menu.
 */
export function ObjectMenu({ name, actions, viaOwner }: ObjectMenuProps) {
  const navigate = useNavigate();
  if (actions.length === 0 && viaOwner === undefined) return null;
  const direct = actions.filter((action) => action.destructive !== true);
  const destroying = actions.filter((action) => action.destructive === true);
  return (
    <RowMenu name={name}>
      {direct.map((action) => (
        <MenuItem key={action.id} icon={action.icon} onClick={action.onSelect} {...(action.description !== undefined ? { description: action.description } : {})}>
          {action.label}
        </MenuItem>
      ))}
      {viaOwner !== undefined ? (
        <>
          {direct.length > 0 ? <MenuSeparator /> : null}
          <MenuItem
            icon={viaOwner.icon}
            description={viaOwner.description}
            onClick={() => {
              void navigate({ to: viaOwner.target.to, params: viaOwner.target.params as never, search: viaOwner.target.search as never });
            }}
          >
            {viaOwner.label}
          </MenuItem>
        </>
      ) : null}
      {destroying.length > 0 ? <MenuSeparator /> : null}
      {destroying.map((action) => (
        <MenuItem key={action.id} destructive icon={action.icon} onClick={action.onSelect} {...(action.description !== undefined ? { description: action.description } : {})}>
          {action.label}
        </MenuItem>
      ))}
    </RowMenu>
  );
}
