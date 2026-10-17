// Fault fixtures test result handling only; they are never counted as actual game episodes.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const game=fs.realpathSync(process.argv[2]);
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ark-policy-result-tests-'));
const entry=path.join(temp,'fixture.mjs');
fs.writeFileSync(entry,`import fs from 'node:fs';
const out=process.argv[process.argv.indexOf('--json')+1], kind=process.env.ARK_RESULT_TEST;
const a={config:'solo:ABYSS',seed:1,victory:false,errors:0}, b={...a,seed:2};
if(kind==='missing')process.exit(0);
if(kind==='exit7')process.exit(7);
if(kind==='broken'){fs.writeFileSync(out,'{broken');process.exit(0);}
const cases={empty:[],partial:[a],duplicate:[a,a],engine:[{...a,victory:true,errors:2},b],invalid:[{...a,victory:'true'},b]};
fs.writeFileSync(out,JSON.stringify(cases[kind]));`);
for(const kind of ['empty','missing','broken','partial','duplicate','engine','invalid','exit7']){
  const out=path.join(temp,kind+'.json');
  const result=spawnSync(process.execPath,[fileURLToPath(new URL('./run.mjs',import.meta.url)),'--game',game,'--entry',entry,'--out',out,'--configs','solo:ABYSS','--seeds','2','--quiet'],{env:{...process.env,ARK_RESULT_TEST:kind},encoding:'utf8'});
  const manifest=JSON.parse(fs.readFileSync(out+'.manifest.json','utf8'));
  assert.notEqual(result.status,0,kind);
  assert.equal(manifest.status,'failed',kind);
  assert.equal(manifest.exitCode,kind==='exit7'?7:0,kind);
  if(kind==='engine'){assert.equal(manifest.engineErrors,2);assert.equal(manifest.victories,0);}
}
console.log('RESULT_FAULT_CHECKS_OK: 8 failed fixtures rejected, terminal manifests saved; no simulated-game claims from fixtures');
