import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { LEAGUES, normalizeSource, validateSource, createUpcomingFeed } from "../src/upcoming.mjs";

const root=fileURLToPath(new URL("..",import.meta.url));
const today=new Date().toISOString().slice(0,10);
const now=new Date(), start=now.getUTCFullYear()-(now.getUTCMonth()<6?1:0);
const years=[start-2,start-1,start].map(y=>y+"-"+String((y+1)%100).padStart(2,"0"));
const source="openfootball/football.json";
async function read(url,maxBytes=2000000){
  const response=await fetch(url,{headers:{"User-Agent":"FootballIntelligence-CC0-research/1.0"},signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw Error("OpenFootball request failed: "+response.status);
  const bytes=Buffer.from(await response.arrayBuffer());
  if(!bytes.length||bytes.length>maxBytes)throw Error("Unexpected source file size");
  return bytes;
}
const metadata=JSON.parse(await read("https://api.github.com/repos/openfootball/football.json",100000));
if(metadata.full_name!==source||metadata.license?.spdx_id!=="CC0-1.0"||metadata.default_branch!=="master")throw Error("OpenFootball license or repository identity changed; review before refresh");
const upstream=JSON.parse(await read("https://api.github.com/repos/openfootball/football.json/commits/master",300000));
const ref=upstream.sha;
if(!/^[a-f0-9]{40}$/.test(ref))throw Error("Invalid upstream commit");
let existing=[];
try{existing=await Promise.all(Object.keys(LEAGUES).map(async l=>JSON.parse(await readFile(join(root,"data",l+".json"),"utf8"))));}catch{}
if(existing.length===4&&existing.every(d=>d.source_commit===ref&&d.season===years[2])){
  for(const d of existing)validateSource(d,d.league);
  console.log("OpenFootball has not changed. Keeping the existing dated estimates and source snapshots.");
  process.exit(0);
}
const documents=[];
for(const league of Object.keys(LEAGUES)){
  const matches=[],receipts=[];
  for(const season of years){
    const path=season+"/"+league+".1.json";
    const bytes=await read("https://raw.githubusercontent.com/openfootball/football.json/"+ref+"/"+path);
    const rows=normalizeSource(JSON.parse(bytes),season,league);
    const blob_sha=createHash("sha1").update("blob "+bytes.length+"\0").update(bytes).digest("hex");
    receipts.push({path,blob_sha,rows:rows.length,finished:rows.filter(m=>m[4]!==null).length});
    matches.push(...rows);
  }
  documents.push(validateSource({source,license:"CC0-1.0",source_commit:ref,season:years[2],league,receipts,matches},league));
}
// Validate everything before changing any committed source files.
const feed=createUpcomingFeed(documents,today);
if(!feed.matches.length)throw Error("No future schedule; retaining previous snapshot until source coverage is reviewed");
await mkdir(join(root,"data"),{recursive:true});
for(const d of documents)await writeFile(join(root,"data",d.league+".json"),JSON.stringify(d)+"\n");
console.log("Validated "+feed.matches.length+" scheduled games from "+ref+".");
