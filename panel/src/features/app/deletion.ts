import type { DeletionPlan } from "../../api/queries/apps";

/** What the operator ticked on the page of deleting an application: every box starts unticked. */
export interface DeleteChoice {
  files: boolean;
  certificate: boolean;
  volumes: boolean;
  databases: boolean;
}

export const NOTHING_CHOSEN: DeleteChoice = { files: false, certificate: false, volumes: false, databases: false };

/** What `DELETE /api/apps/{domain}` is asked, in the API's own terms. */
export interface DeleteOptions {
  removeFiles: boolean;
  removeSsl: boolean;
  removeVolumes: boolean;
  removeDatabases: boolean;
  /** The directory, named exactly, of an application that lives outside Noust's apps directory. */
  removeAdoptedDirectory: string | null;
}

type Database = DeletionPlan["databases"][number];

/** The databases the deletion drops when asked: the application's own, which nothing else uses. */
export function dropsOf(plan: Pick<DeletionPlan, "databases">): Database[] {
  return plan.databases.filter((database) => database.will_drop);
}

/** The databases it never drops: the ones it only uses, and the ones other applications use too. */
export function keptOf(plan: Pick<DeletionPlan, "databases">): Database[] {
  return plan.databases.filter((database) => !database.will_drop);
}

/**
 * What a choice asks of the API. A choice the application cannot have (volumes of a service that has none, a
 * database that is not only its own) is not asked for, whatever a stale box says. A directory outside Noust's apps
 * directory (an adopted stack's) is removed by the server only when it is named exactly, and the box of its files
 * says as much, so ticking it names the directory: the sentence on the page and what happens are the same thing.
 */
export function deleteOptionsOf(choice: DeleteChoice, plan: Pick<DeletionPlan, "path" | "keeps_directory" | "has_volumes" | "databases">): DeleteOptions {
  return {
    removeFiles: choice.files,
    removeSsl: choice.certificate,
    removeVolumes: choice.volumes && plan.has_volumes,
    removeDatabases: choice.databases && dropsOf(plan).length > 0,
    removeAdoptedDirectory: choice.files && plan.keeps_directory ? plan.path : null,
  };
}
