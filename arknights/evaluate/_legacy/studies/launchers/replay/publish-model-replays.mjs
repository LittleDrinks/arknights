// Append verified model episodes under an exclusive task lock; preserve existing replay entries.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root=path.resolve(process.argv[2]),bundles=process.argv.slice(3).map(p=>path.resolve(p));
if(!bundles.length)throw new Error('root and bundle paths required');
const {mergeIndexes}=await import(pathToFileURL(path.join(root,'experiment-code/model-replay/index.mjs')));
const digest=b=>createHash('sha256').update(b).digest('hex');
const live=path.join(root,'game/public/dev'),lock=path.join(root,'experiments/replay-publish.lock');
const fd=fs.openSync(lock,'wx');
try{
  const agentPath=path.join(live,'agent/index.json'),recPath=path.join(live,'recordings/index.json');
  const agentBefore=fs.readFileSync(agentPath),recBefore=fs.readFileSync(recPath);
  let agent=JSON.parse(agentBefore),recordings=JSON.parse(recBefore);
  const planned=[],provenance=[];
  for(const dir of bundles){
    if(!dir.startsWith(root+path.sep))throw new Error('Bundle outside project');
    const report=JSON.parse(fs.readFileSync(path.join(dir,'verification.json')));
    const evalManifest=JSON.parse(fs.readFileSync(path.join(root,'experiments/learning-pilot-v2/eval-bc-dev/manifest.json')));
    const arm=evalManifest.arms.find(a=>a.difficulty===report.result.difficulty&&a.name==='bc');
    const basename=path.basename(report.provenance.trace.path);
    if(evalManifest.status!=='complete'||arm?.status!=='complete'||arm.traces[basename]!==report.provenance.trace.sha256||evalManifest.checkpointSha256.bc!==report.provenance.checkpoint.sha256)throw new Error('Saved evaluation manifest does not bind this trace/checkpoint');
    const addedAgent=JSON.parse(fs.readFileSync(path.join(dir,'agent/index.json'))),addedRec=JSON.parse(fs.readFileSync(path.join(dir,'recordings/index.json')));
    for(const ep of addedAgent.episodes)ep.policyLabel='BC v1';
    ({agent,recordings}=mergeIndexes(agent,recordings,addedAgent,addedRec));
    for(const file of report.files){
      if(['agent/index.json','recordings/index.json'].includes(file.file))continue;
      if(!/^(agent\/(episodes|downloads)|recordings)\/[A-Za-z0-9._-]+$/.test(file.file))throw new Error('Unsafe bundle file');
      const bytes=fs.readFileSync(path.join(dir,file.file));
      if(bytes.length!==file.bytes||digest(bytes)!==file.sha256)throw new Error('Bundle checksum mismatch');
      const target=path.join(live,file.file);
      if(fs.existsSync(target))throw new Error('Refuse existing artifact '+file.file);
      planned.push({target,bytes,relative:file.file,sha256:file.sha256});
    }
    provenance.push({id:report.id,victory:report.victory,traceSha256:report.provenance.trace.sha256,checkpointSha256:report.provenance.checkpoint.sha256,evalManifestSha256:digest(fs.readFileSync(path.join(root,'experiments/learning-pilot-v2/eval-bc-dev/manifest.json')))});
  }
  const backup=path.join(root,'experiments','replay-publish-'+Date.now());fs.mkdirSync(backup);
  fs.writeFileSync(path.join(backup,'agent-before.json'),agentBefore);fs.writeFileSync(path.join(backup,'recordings-before.json'),recBefore);
  for(const file of planned)fs.writeFileSync(file.target,file.bytes,{flag:'wx'});
  if(digest(fs.readFileSync(agentPath))!==digest(agentBefore)||digest(fs.readFileSync(recPath))!==digest(recBefore))throw new Error('Live index changed; artifacts retained, indexes not overwritten');
  const atomic=(dest,obj)=>{const temp=dest+'.publish-'+process.pid;fs.writeFileSync(temp,JSON.stringify(obj),{flag:'wx'});fs.renameSync(temp,dest);};
  atomic(recPath,recordings);atomic(agentPath,agent);
  const receipt={publishedAt:new Date().toISOString(),episodesBefore:JSON.parse(agentBefore).episodes.length,episodesAfter:agent.episodes.length,recordingsBefore:JSON.parse(recBefore).length,recordingsAfter:recordings.length,provenance,files:planned.map(({relative,sha256,bytes})=>({file:relative,sha256,bytes:bytes.length}))};
  fs.writeFileSync(path.join(backup,'receipt.json'),JSON.stringify(receipt,null,2));
  console.log(JSON.stringify({backup,...receipt,files:receipt.files.length}));
}finally{fs.closeSync(fd);fs.unlinkSync(lock);}
