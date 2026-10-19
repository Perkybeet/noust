// A worker that cannot start: the release that carries it must not be served.
console.error("worker: cannot reach the queue (broken on purpose)");
process.exit(1);
