import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { run, validateSeason, SEASONS, LEAGUES } from './multiseason-strength.js';
const source='openfootball/football.json';
const ref='e6744429ee395bc86f247348c6184bb08d4eb361';
const files=[],matches=[];
for(const season of SEASONS){
  for(const league of LEAGUES){
    const path=season+'/'+league+'.1.json';
    const response=await fetch('https://raw.githubusercontent.com/'+source+'/'+ref+'/'+path,
      {headers:{'User-Agent':'Footmo-CC0-research/1.0'},signal:AbortSignal.timeout(30000)});
    if(!response.ok)throw Error('Source request failed '+path+' '+response.status);
    const bytes=Buffer.from(await response.arrayBuffer());
    if(!bytes.length||bytes.length>2e6)throw Error('Unexpected file size '+path);
    const rows=validateSeason(JSON.parse(bytes),season,league);
    matches.push(...rows);
    files.push({path,sha256:createHash('sha256').update(bytes).digest('hex'),rows:rows.length});
  }
}
const report={source,sourceCommit:ref,license:'CC0-1.0',files,generatedAt:new Date().toISOString(),
  ...run(matches)};
await mkdir('research-output',{recursive:true});
await writeFile('research-output/multiseason-strength-report.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({training:report.training,development:report.development,test:report.test,promotionEligible:false},null,2));
