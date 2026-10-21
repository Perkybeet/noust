/**
 * How an application's code is fetched and which pushes deploy it: submodules, Git LFS, shallow
 * clones, skip markers and watch paths (an application's Source settings), and the words for
 * the deliveries those rules ignore. What git answers is shown verbatim and is not here.
 */
export const sourceOptions = {
  title: "Fetching and deploying",
  description: "What the clone brings and which pushes become deployments.",
  loading: "Loading how this app is fetched",
  readFailed: "Could not read how this app is fetched",
  submodules: {
    label: "Fetch submodules",
    description: "Put the files of the repository's submodules in the release. A release is normally an archive of the commit, which leaves them empty.",
  },
  lfs: {
    label: "Git LFS files",
    description: "Without them a release holds pointer files, which is a broken deployment.",
    auto: "When the repository uses LFS",
    on: "Always",
    off: "Never",
  },
  shallow: {
    label: "Shallow clone",
    description: "Fetch only the latest commit. Turn it off when a build reads the history, such as git describe.",
  },
  skip: {
    label: "Skip pushes marked [skip ci] or [skip cd]",
    description: "A push is not deployed when every one of its commits says so. A pull request is not built when its title or head commit does.",
  },
  paths: {
    label: "Watch paths",
    description: "One glob per line. A push deploys only when it touches one of them; a line starting with ! excludes, and the last match wins. Empty means every push deploys.",
    placeholder: "apps/web/**\n!**/*.md",
    note: "GitHub, Gitea and GitLab list the files of a push. A forge that does not (Bitbucket) cannot be filtered, and its pushes deploy.",
  },
  saved: "{domain} will be fetched and deployed this way",
  notSaved: "The source settings of {domain} were not saved",
  reset: "Back to the defaults",
  resetTitle: "Use the defaults for {domain}?",
  resetDescription: "Submodules and LFS are fetched as the repository needs, history is not kept, and every push deploys.",
  resetAction: "Use the defaults",
  resetDone: "{domain} uses the default source settings",
  deployUrl: {
    label: "Deploy URL",
  },
  outcome: {
    skip: "Marked to skip, ignored",
    paths: "No watched path touched, ignored",
  },
} as const;
