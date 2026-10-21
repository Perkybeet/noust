import { useOrganization } from "./useOrganization";

export interface Organised {
  /**
   * There is something to organise: more than one team, project or environment, or someone who
   * may start (`teams.manage`). A server nobody organised in teams shows everyone else the
   * sidebar it always had, and the palette no Projects.
   */
  organised: boolean;
  /** The server has not yet said whether it has teams, so whether Projects is offered is not known. */
  settling: boolean;
}

/** Whether Projects is a destination here: the one answer the sidebar and the palette share. */
export function useOrganised(): Organised {
  const org = useOrganization();
  return {
    organised: org.available && (org.teamsMatter || org.scope.showProject || org.scope.showEnvironment || org.can("teams.manage")),
    settling: org.loading,
  };
}
