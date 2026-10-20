// A build leaves something behind; here, a file the server reads.
require("node:fs").writeFileSync("built.txt", "built\n");
