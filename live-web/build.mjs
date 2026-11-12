import { cp, mkdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
const root = fileURLToPath(new URL(".", import.meta.url));
const dest = join(root, "dist");
await rm(dest, { recursive: true, force: true });
await mkdir(dest, { recursive: true });
for (const name of ["index.html", "styles.css", "app.mjs", "analysis.mjs"]) {
  await cp(join(root, "src", name), join(dest, name));
}
console.log("Built static Football Intelligence research workspace");
