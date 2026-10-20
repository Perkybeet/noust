// adapter-node makes a server Noust can run (`node build`); adapter-auto would make nothing it can.
import adapter from "@sveltejs/adapter-node";

export default { kit: { adapter: adapter() } };
