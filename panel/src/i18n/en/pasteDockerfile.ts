/**
 * Typing or pasting a Dockerfile for an application that has no repository. What the server says
 * when it refuses the text is shown verbatim and is not here.
 */
export const pasteDockerfile = {
  open: "Or paste a Dockerfile",
  label: "Dockerfile",
  help: "The build has nothing but this file, so a COPY finds nothing: the Dockerfile fetches what it needs itself (ADD of a URL, RUN git clone, or a base image that has the program).",
  use: "Use this Dockerfile",
  failed: "That Dockerfile was not accepted",
  stored: "The Dockerfile is ready",
  storedBody: "It is kept on this server as the whole of the project. Continue to inspect it.",
  edit: "Edit it",
} as const;
