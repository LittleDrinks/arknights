import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {performance} from 'node:perf_hooks';
import {PINS,RULES_COMMIT,defaultSpec,parseProfileText,sha256,transformSource} from './lib.mjs';

const own={},forward=[];
for(let i=2;i<process.argv.length;i++){
  const key=process.argv[i];
  if(['--game','--profile','--out','--entry'].includes(key)){
    const value=process.argv[++i];if(!value||value.startsWith('--'))throw new Error('Missing value for '+key);
    own[key.slice(2)]=value;
  }else forward.push(key);
}
if(!own.game||!own.out)throw new Error('Usage: node policy/run.mjs --game GAME --out FILE [--profile JSON] [--entry BENCH_WRAPPER] --configs ... --seeds ... --jobs ...');
if(forward.includes('--json')||forward.includes('--compare'))throw new Error('Use --out; compare is not a run');
const game=fs.realpathSync(own.game),out=path.resolve(own.out);
if(fs.existsSync(out))throw new Error('Output exists; use a new arm/run directory');
fs.mkdirSync(path.dirname(out),{recursive:true});
const profile=own.profile?fs.realpathSync(own.profile):null;
const spec=profile?parseProfileText(fs.readFileSync(profile,'utf8')):defaultSpec();
const original=fs.readFileSync(path.join(game,PINS.botJs.relPath),'utf8');
if(sha256(original)!==PINS.botJs.sha256)throw new Error('Pinned bot source mismatch');
if(sha256(fs.readFileSync(path.join(game,PINS.botbench.relPath)))!==PINS.botbench.sha256)throw new Error('Pinned benchmark mismatch');
const transformed=transformSource(original,spec.rules);
const entry=own.entry?fs.realpathSync(own.entry):path.join(game,PINS.botbench.relPath);
const command=[process.execPath,'--import',fileURLToPath(new URL('./register.mjs',import.meta.url)),entry,...forward,'--json',out];
const manifest={kind:'controlled upstream bot policy experiment',trainedModel:false,engineRulesCommit:RULES_COMMIT,
  profile:spec,profileFileSha256:profile?sha256(fs.readFileSync(profile)):null,
  botOriginalSha256:PINS.botJs.sha256,botTransformedSha256:sha256(transformed.code),
  benchmarkOriginalSha256:PINS.botbench.sha256,entry,entrySha256:sha256(fs.readFileSync(entry)),
  command,nodeVersion:process.version,startedAt:new Date().toISOString(),status:'running'};
const manifestPath=out+'.manifest.json';
const save=()=>fs.writeFileSync(manifestPath,JSON.stringify(manifest,null,2)+'\n');save();
const env={...process.env,ARK_GAME_ROOT:game};
delete env.ARK_POLICY_PROFILE;if(profile)env.ARK_POLICY_PROFILE=profile;
const log=fs.openSync(out+'.log','w'),start=performance.now();
let result;
try{result=spawnSync(command[0],command.slice(1),{cwd:game,env,stdio:['ignore',log,log]});}
finally{fs.closeSync(log);}
manifest.batchWallSeconds=(performance.now()-start)/1000;manifest.finishedAt=new Date().toISOString();
manifest.exitCode=result.status;manifest.signal=result.signal;manifest.spawnError=result.error?String(result.error):null;
manifest.botFileUnchanged=sha256(fs.readFileSync(path.join(game,PINS.botJs.relPath)))===PINS.botJs.sha256;
manifest.status=result.status===0&&manifest.botFileUnchanged?'complete':'failed';
if(manifest.status==='complete'){
  const rows=JSON.parse(fs.readFileSync(out,'utf8'));manifest.games=rows.length;
  manifest.victories=rows.filter(r=>r.victory===true).length;
  manifest.engineErrors=rows.reduce((n,r)=>n+(r.errors||0),0);
}
save();
console.log(JSON.stringify({profile:spec.name,status:manifest.status,games:manifest.games,wins:manifest.victories,out,manifest:manifestPath}));
if(manifest.status!=='complete')process.exit(result.status||1);
