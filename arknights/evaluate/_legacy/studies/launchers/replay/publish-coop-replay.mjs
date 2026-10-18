// Stage verified immutable files, browser-check merged indexes, then publish by CAS.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const [rootArg,bundleArg,phase]=process.argv.slice(2);
assert(['stage','publish'].includes(phase));
const root=fs.realpathSync(rootArg),bundle=fs.realpathSync(bundleArg);
assert(bundle.startsWith(root+path.sep));
const hash=b=>createHash('sha256').update(b).digest('hex');
const live=path.join(root,'game/public/dev'),out=path.join(root,'experiments/coop-replay-publish-v1');
const lock=path.join(root,'experiments/replay-publish.lock'),fd=fs.openSync(lock,'wx');
const atomic=(file,bytes)=>{const temp=file+'.publish-'+process.pid;fs.writeFileSync(temp,bytes,{flag:'wx'});fs.renameSync(temp,file);};
try{
  const report=JSON.parse(fs.readFileSync(path.join(bundle,'verification.json')));
  assert.equal(report.mode,'record');
  for(const key of ['completeTerminal','completeSummary','completePartnerAudit'])assert.equal(report.verification[key],true);
  for(const key of ['engineErrors','actionErrors','controllerErrors'])assert.equal(report.verification[key],0);
  assert.equal(report.summary.victory,true);assert.equal(report.summary.seed,50003);
  assert.equal(report.provenance.singleCheckpoint,null);
  const trace=fs.readFileSync(report.provenance.trace.path);
  assert.equal(hash(trace),report.provenance.trace.sha256);
  for(const [file,sha]of Object.entries(report.provenance.sources)){
    assert(!file.includes('..')&&!path.isAbsolute(file));
    assert.equal(hash(fs.readFileSync(path.join(root,'experiment-code',file))),sha,file);
  }
  for(const file of report.files){
    assert(/^(agent\/(episodes|downloads)\/[A-Za-z0-9._-]+|recordings\/[A-Za-z0-9._-]+|agent\/index\.json)$/.test(file.file));
    const bytes=fs.readFileSync(path.join(bundle,file.file));
    assert.equal(bytes.length,file.bytes,file.file);assert.equal(hash(bytes),file.sha256,file.file);
  }
  const agentPath=path.join(live,'agent/index.json'),recordingPath=path.join(live,'recordings/index.json');
  if(phase==='stage'){
    fs.mkdirSync(out);
    const beforeAgent=fs.readFileSync(agentPath),beforeRecording=fs.readFileSync(recordingPath);
    const extraAgent=JSON.parse(fs.readFileSync(path.join(bundle,'agent/index.json')));
    const extraRecording=JSON.parse(fs.readFileSync(path.join(bundle,'recordings/index.json')));
    for(const entry of extraAgent.episodes)entry.policyLabel='PPO 训练团队胜局（学习席R11淘汰） · '+entry.policyLabel.split(' · ').slice(1).join(' · ');
    const {mergeIndexes}=await import(pathToFileURL(path.join(root,'experiment-code/model-replay/index.mjs')));
    const merged=mergeIndexes(JSON.parse(beforeAgent),JSON.parse(beforeRecording),extraAgent,extraRecording);
    fs.writeFileSync(path.join(out,'agent-before.json'),beforeAgent);
    fs.writeFileSync(path.join(out,'recordings-before.json'),beforeRecording);
    fs.writeFileSync(path.join(out,'agent-merged.json'),JSON.stringify(merged.agent));
    fs.writeFileSync(path.join(out,'recordings-merged.json'),JSON.stringify(merged.recordings));
    const staged=[];
    for(const file of report.files){
      if(['agent/index.json','recordings/index.json'].includes(file.file))continue;
      const destination=path.join(live,file.file);
      assert.equal(fs.existsSync(destination),false,'Refuse existing '+file.file);
      fs.copyFileSync(path.join(bundle,file.file),destination,fs.constants.COPYFILE_EXCL);
      assert.equal(hash(fs.readFileSync(destination)),file.sha256);
      staged.push(file);
    }
    const receipt={status:'staged-not-indexed',bundle,traceSHA256:hash(trace),
      before:{agent:hash(beforeAgent),recordings:hash(beforeRecording)},
      merged:{agent:hash(fs.readFileSync(path.join(out,'agent-merged.json'))),recordings:hash(fs.readFileSync(path.join(out,'recordings-merged.json')))},
      addedEpisodes:extraAgent.episodes.map(e=>e.id),addedRecordings:extraRecording.length,files:staged,
      inferenceRerun:false,source:'changing PPO training policy; primary eliminated R11; team victory R14'};
    fs.writeFileSync(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2));
    console.log(JSON.stringify({status:receipt.status,out,episodesAfter:merged.agent.episodes.length,recordingsAfter:merged.recordings.length,files:staged.length}));
  }else{
    const receipt=JSON.parse(fs.readFileSync(path.join(out,'receipt.json')));
    assert.equal(receipt.status,'staged-not-indexed');assert.equal(receipt.bundle,bundle);
    assert.equal(hash(fs.readFileSync(agentPath)),receipt.before.agent,'Agent index changed since staging');
    assert.equal(hash(fs.readFileSync(recordingPath)),receipt.before.recordings,'Recording index changed since staging');
    for(const file of receipt.files)assert.equal(hash(fs.readFileSync(path.join(live,file.file))),file.sha256);
    const agent=fs.readFileSync(path.join(out,'agent-merged.json')),recordings=fs.readFileSync(path.join(out,'recordings-merged.json'));
    assert.equal(hash(agent),receipt.merged.agent);assert.equal(hash(recordings),receipt.merged.recordings);
    try{atomic(recordingPath,recordings);atomic(agentPath,agent);}
    catch(error){
      // Exclusive publisher lock remains held. Restore only our own writes.
      if(hash(fs.readFileSync(recordingPath))===receipt.merged.recordings)atomic(recordingPath,fs.readFileSync(path.join(out,'recordings-before.json')));
      if(hash(fs.readFileSync(agentPath))===receipt.merged.agent)atomic(agentPath,fs.readFileSync(path.join(out,'agent-before.json')));
      throw error;
    }
    receipt.status='published';receipt.publishedAt=new Date().toISOString();
    fs.writeFileSync(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2));
    console.log(JSON.stringify({status:receipt.status,episodes:JSON.parse(agent).episodes.length,recordings:JSON.parse(recordings).length,ids:receipt.addedEpisodes}));
  }
}finally{fs.closeSync(fd);fs.unlinkSync(lock);}
