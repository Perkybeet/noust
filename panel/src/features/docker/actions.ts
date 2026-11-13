/**
 * What the Docker zone does to Docker: start, stop, restart and remove, each asked of the server, which judges it by whose the
 * object is (the console only offers what `can` says, and the server decides again). Starting, stopping and restarting change what
 * the page shows at once, so they say nothing when they work; a refusal is said with the server's own words and its fix.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { dockerKeys } from "../../api/queries/docker";
import { reportActionError } from "../apps/useAppActions";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";

/** The verbs of a container that run now and are undone by the next one. */
export type ContainerVerb = "start" | "stop" | "restart";

function failure(verb: ContainerVerb, name: string): string {
  const locale = getLocale();
  switch (verb) {
    case "start":
      return translate(locale, "docker.actions.startFailed", { name });
    case "stop":
      return translate(locale, "docker.actions.stopFailed", { name });
    case "restart":
      return translate(locale, "docker.actions.restartFailed", { name });
  }
}

/** Start, stop and restart a container. The page is read again whether it worked or was refused: the state it shows is Docker's. */
export function useContainerVerb(verb: ContainerVerb) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ref: string) => {
      const params = { params: { ref } };
      switch (verb) {
        case "start":
          return request("post", "/api/docker/containers/{ref}/start", params);
        case "stop":
          return request("post", "/api/docker/containers/{ref}/stop", params);
        case "restart":
          return request("post", "/api/docker/containers/{ref}/restart", params);
      }
    },
    onError: (error, ref) => {
      reportActionError(failure(verb, ref), error);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: dockerKeys.all }),
  });
}

/** Start, stop and restart a whole stack, in the order Compose gives. Only a stack nobody of Noust made gets here: an application's is the application's. */
export function useStackVerb(verb: ContainerVerb) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => {
      const params = { params: { name } };
      switch (verb) {
        case "start":
          return request("post", "/api/docker/stacks/{name}/start", params);
        case "stop":
          return request("post", "/api/docker/stacks/{name}/stop", params);
        case "restart":
          return request("post", "/api/docker/stacks/{name}/restart", params);
      }
    },
    onError: (error, name) => {
      reportActionError(failure(verb, name), error);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: dockerKeys.all }),
  });
}

/** What is removed: a container (with `force` when it runs), an image by id, a network by id or name, a volume by name with its name typed. */
export type RemoveTarget =
  | { kind: "container"; ref: string; force: boolean }
  | { kind: "image"; ref: string }
  | { kind: "network"; ref: string }
  | { kind: "volume"; ref: string };

/**
 * Removes one thing. A volume is sent with its own name as the confirmation, which the server requires and the dialog asks the
 * operator to type: removing the data of a volume is the one thing here that cannot be undone and is never one click.
 */
export function useRemove() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (target: RemoveTarget) => {
      switch (target.kind) {
        case "container":
          return request("delete", "/api/docker/containers/{ref}", { params: { ref: target.ref }, query: { force: target.force } });
        case "image":
          return request("delete", "/api/docker/images/{image_id}", { params: { image_id: target.ref } });
        case "network":
          return request("delete", "/api/docker/networks/{ref}", { params: { ref: target.ref } });
        case "volume":
          return request("delete", "/api/docker/volumes/{name}", { params: { name: target.ref }, body: { confirm_name: target.ref } });
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: dockerKeys.all }),
  });
}
