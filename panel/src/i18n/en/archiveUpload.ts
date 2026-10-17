/**
 * Sending the code of an application as an archive from the browser. What the server says when it
 * refuses an archive (a path out of the project, a link that points out, no files) is shown
 * verbatim and is not here.
 */
export const archiveUpload = {
  label: "Archive of the project",
  help: "A .zip, .tar.gz, .tgz, .tar.bz2 or .tar.xz with the project's files. It is extracted once on the server, and nothing outside the project is written.",
  extension: "{name} is not an archive Noust extracts. Use a .zip, .tar, .tar.gz, .tgz, .tar.bz2 or .tar.xz.",
  empty: "{name} is empty.",
  uploading: "Uploading the archive",
  completing: "Extracting the archive",
  progress: "{received} of {size}",
  waiting: "The connection dropped. Trying again by itself (attempt {attempt}).",
  paused: "The upload stopped after {received}. The server keeps what it has: retry to go on from there.",
  retry: "Retry now",
  cancel: "Cancel the upload",
  cancelled: "The upload was cancelled. Nothing was kept.",
  failed: "The archive was not accepted",
  done: "{name} was uploaded",
  doneBody: "{files} files were extracted on the server.",
  another: "Choose another file",
} as const;
