/**
 * Which jobs are the Software page's, and where it is. Kept apart from the page's words (which read the registry and the
 * translator) so that what asks only "is this job one of Software's" (the task centre, deciding who tells the person that
 * an install ended) does not carry them.
 */

/** The sentences that title an install and a removal: the server puts the registry's id in them (`nginx_stream_module`). */
export const SOFTWARE_JOB_TITLES: ReadonlySet<string> = new Set(["jobs.title.server_action_software_install", "jobs.title.server_action_software_remove"]);

/** The page that starts them and follows them to their end. */
export const SOFTWARE_PAGE = "/server/software";

/** Whether a job's title is that of an install or a removal of a component. */
export function isSoftwareJob(title: { code: string } | null): boolean {
  return title !== null && SOFTWARE_JOB_TITLES.has(title.code);
}
