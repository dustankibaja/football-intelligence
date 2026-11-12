import { cp, mkdir, rm, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { createUpcomingFeed } from "./src/upcoming.mjs";
const root = fileURLToPath(new URL(".", import.meta.url));
const dest = join(root, "dist");
await rm(dest, { recursive: true, force: true });
await mkdir(dest, { recursive: true });
for (const name of ["index.html", "styles.css", "app.mjs", "analysis.mjs", "upcoming.mjs", "upcoming-ui.mjs"]) {
  await cp(join(root, "src", name), join(dest, name));
}
const documents=await Promise.all(["en","es","it","de"].map(async league=>JSON.parse(await readFile(join(root,"data",league+".json"),"utf8"))));
const today=new Date().toISOString().slice(0,10);
const feed=createUpcomingFeed(documents,today);
await writeFile(join(dest,"upcoming-data.json"),JSON.stringify(feed)+"\n");
console.log("Built Football Intelligence with "+feed.matches.length+" future scheduled matches from the pinned open source; "+feed.matches.filter(m=>m.prediction).length+" have early estimates.");
