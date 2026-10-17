import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

/** How an application's git push deployment stands: branch, remote, keys, hook. */
export type AppPush = ResponseOf<"/api/apps/{domain}/push", "get">;
export type PushKey = AppPush["keys"][number];
export type PushEnableBody = BodyOf<"/api/apps/{domain}/push", "put">;
export type PushKeyBody = BodyOf<"/api/apps/{domain}/push/keys", "post">;

export const pushKeys = {
  // Under the app's own prefix, so a job on the app finishing refreshes it with the rest.
  forApp: (domain: string) => ["app", domain, "push"] as const,
};

/** The deployment, which also says whether the server takes pushes at all. Changes nothing. */
export const appPushQuery = (domain: string) =>
  queryOptions({
    queryKey: pushKeys.forApp(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/push", { params: { domain }, signal }),
  });

/** Turns git push on (or changes the branch). Needs "Confirm it's you". */
export function enableAppPush(domain: string, body: PushEnableBody) {
  return request("put", "/api/apps/{domain}/push", { params: { domain }, body });
}

/** Turns it off; the source and the branch go back, and the keys are removed. Needs "Confirm it's you". */
export function disableAppPush(domain: string, deleteRepository: boolean) {
  return request("delete", "/api/apps/{domain}/push", { params: { domain }, query: { delete_repository: deleteRepository } });
}

/** Writes the hook and its settings again. */
export function refreshAppPush(domain: string) {
  return request("post", "/api/apps/{domain}/push/refresh", { params: { domain } });
}

/** Lets a key push. Needs "Confirm it's you". */
export function addPushKey(domain: string, body: PushKeyBody) {
  return request("post", "/api/apps/{domain}/push/keys", { params: { domain }, body });
}

/** Takes a key's permission to push away. Needs "Confirm it's you". */
export function removePushKey(domain: string, label: string) {
  return request("delete", "/api/apps/{domain}/push/keys/{label}", { params: { domain, label } });
}

/** Prepares the server to take pushes: an account and sshd's rules for it. Needs "Confirm it's you". */
export function prepareServerForPush() {
  return request("post", "/api/push/setup", {});
}
