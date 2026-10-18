// Reuse the unmodified benchmark and rule engine. Change only the selected solo band through _applyBand.
// Unlike upstream --band, this leaves gd.bandIds/bandBondIds intact, including the bot's later bond planning.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import crypto from 'node:crypto';
const gameRoot=path.resolve(process.env.ARK_GAME_ROOT||'');
if(!process.env.ARK_GAME_ROOT) throw new Error('ARK_GAME_ROOT is required');
let source=fs.readFileSync(path.join(gameRoot,'tools/botbench.mjs'),'utf8');
const hash=crypto.createHash('sha256').update(source).digest('hex');
if(hash!=='96d35d7cd9067888f147f81270733b45de4279b7a7768d3adf3ed4d409fb1d50')throw new Error('Upstream benchmark source hash mismatch');
const old="if (opt.band && cfg.mode === 'solo') { m.gd.bandIds = () => [String(opt.band)]; m.gd.bandBondIds = () => []; }";
if(source.split(old).length!==2) throw new Error('Pinned bandbench anchor mismatch');
source=source.replace(old,`if (opt.band && cfg.mode === 'solo') {
    const selected = String(opt.band);
    if (!m.gd.bandIds().includes(selected)) throw new Error('Unknown/unavailable band ' + selected);
    const applyBand = m._applyBand.bind(m);
    m._applyBand = (ps, _chosen) => applyBand(ps, selected);
  }`);
source=source.replace(/from '(\.\.\/server\/[^']+)'/g,(_match,rel)=>`from '${pathToFileURL(path.resolve(gameRoot,'tools',rel)).href}'`);
source=source.replace("const rec = { config: cfg.name, seed,",`const rec = { experiment: {sourceBenchmarkSha256:'${hash}', forcedSoloBand:opt.band||null, engineRules:'unchanged'}, config: cfg.name, seed,`);
if(process.env.ARK_STACKING_RUNTIME){
  const runtime=pathToFileURL(fs.realpathSync(process.env.ARK_STACKING_RUNTIME)).href;
  const importAnchor="import { performance } from 'node:perf_hooks';";
  if(source.split(importAnchor).length!==2)throw new Error('Pinned import anchor mismatch');
  source=source.replace(importAnchor,`import { stackingReports as __stackingReports } from ${JSON.stringify(runtime)};\n`+importAnchor);
  const startAnchor='  m.start();\n';
  if(source.split(startAnchor).length!==2)throw new Error('Pinned start anchor mismatch');
  source=source.replace(startAnchor,`  rec.stacking={calls:0,attempted:0,accepted:0,netCost:0,modules:{},activeRounds:[]};
  for(const ps of m.players.values()){
    const ready=ps.setReady.bind(ps);
    ps.setReady=(value)=>{
      const wasReady=ps.ready;
      const result=ready(value);
      if(value&&!wasReady&&ps.ready){
        for(const report of __stackingReports(ps)){
          rec.stacking.calls++;rec.stacking.attempted+=report.attempted;
          rec.stacking.accepted+=report.accepted;rec.stacking.netCost+=report.netCost;
          if(report.accepted)rec.stacking.activeRounds.push({round:m.round,pid:ps.playerId,phase:report.phase,accepted:report.accepted});
          for(const [name,detail] of Object.entries(report.modules)){
            const total=rec.stacking.modules[name]??={attempted:0,accepted:0,stops:{}};
            total.attempted+=detail.attempted;total.accepted+=detail.accepted;
            const reason=detail.stopReason??'complete';total.stops[reason]=(total.stops[reason]||0)+1;
          }
        }
      }
      return result;
    };
  }
`+startAnchor);
}
if(process.env.ARK_TEACHER_RUNTIME){
  const runtime=pathToFileURL(fs.realpathSync(process.env.ARK_TEACHER_RUNTIME)).href;
  const anchor="import { performance } from 'node:perf_hooks';";
  if(source.split(anchor).length!==2)throw new Error('Pinned teacher import anchor mismatch');
  source=source.replace(anchor,`import { reports as __teacherReports } from ${JSON.stringify(runtime)};\n`+anchor);
  const start='  m.start();\n';
  if(source.split(start).length!==2)throw new Error('Pinned teacher start anchor mismatch');
  source=source.replace(start,`  rec.teacher={calls:0,setupBuys:0,setupGrossCost:0,macroAccepted:0,macroAttempted:0,macroNetCost:0,activeRounds:[]};
  for(const ps of m.players.values()){
    const ready=ps.setReady.bind(ps);
    ps.setReady=(value)=>{
      const before=ps.ready;const result=ready(value);
      if(value&&!before&&ps.ready){
        const s=__teacherReports(ps);
        if(s){
          rec.teacher.calls++;rec.teacher.setupBuys+=s.setupBuys;rec.teacher.setupGrossCost+=s.setupCost;
          const accepted=s.macros.reduce((n,r)=>n+r.accepted,0);
          rec.teacher.macroAccepted+=accepted;rec.teacher.macroAttempted+=s.attempted;rec.teacher.macroNetCost+=s.cost;
          if(accepted||s.setupBuys)rec.teacher.activeRounds.push({round:s.round,pid:ps.playerId,setupBuys:s.setupBuys,macroAccepted:accepted});
        }
      }
      return result;
    };
  }
`+start);
}
const resultAnchor='  m.dispose();\n  return rec;';
if(source.split(resultAnchor).length!==2)throw new Error('Pinned result anchor mismatch');
source=source.replace(resultAnchor,`  rec.finalDiagnostics = {
    reason:summary?.reason??null, hiddenCleared:!!summary?.hiddenCleared,
    bossPool:m.bossPool?{hp:m.bossPool.hp,maxHp:m.bossPool.maxHp}:null,
    players:(summary?.players||[]).map(p=>({pid:p.playerId,band:p.bandId,stats:p.stats,bonds:p.bonds,lineup:p.lineup}))
  };
  m.dispose();
  return rec;`);
if(source.includes("from '../server/"))throw new Error('Unresolved benchmark import');
await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
