// Training-only reward ablation. Never use its shaped reward as the victory metric.
// All observations/candidates/controllers/actions are the approved ShopEnv's.
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import readline from 'node:readline';
const root=process.env.ARK_ROOT;
if(!root)throw new Error('ARK_ROOT required');
const {ShopEnv}=await import(pathToFileURL(path.join(root,'experiment-code/rl/env.mjs')));
const env=await ShopEnv.create(process.env.ARK_GAME_ROOT||path.join(root,'game'));
let paidRounds=0;
const decorate=state=>{
  const completed=Math.min(15,Math.max(0,state.done?state.rounds:state.rounds-1));
  const roundBonus=.02*Math.max(0,completed-paidRounds);paidRounds=Math.max(completed,paidRounds);
  // Privileged, terminal-only diagnostic reward from the real simulated boss.
  // It is never added to policy observations or candidate features.
  const boss=state.done?env.m.bossPool:null;
  const bossFraction=boss?.maxHp>0?Math.max(0,Math.min(1,1-boss.hp/boss.maxHp)):0;
  const bossBonus=state.done&&!state.victory ? .4*bossFraction : 0;
  const terminalReward=state.done?(state.victory?1:-1):0;
  return {...state,reward:terminalReward+roundBonus+bossBonus,
    trainingReward:{mode:'round-progress+terminal-boss/v1',terminalReward,roundBonus,bossBonus,
      privilegedTerminalBoss:true,primaryMetric:'summary.victory; shaped reward is not success'}};
};
for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})){
  try{
    const req=JSON.parse(line);let result;
    if(req.cmd==='reset'){paidRounds=0;result=env.reset(req);result={...result,reward:0};}
    else if(req.cmd==='step')result=decorate(env.step(req.candidateIndex));
    else if(req.cmd==='demo')result=env.demo(req);
    else if(req.cmd==='close'){env.close();console.log(JSON.stringify({ok:true}));break;}
    else throw new Error('Expected reset/step/demo/close');
    console.log(JSON.stringify({ok:true,...result}));
  }catch(e){console.log(JSON.stringify({ok:false,error:String(e.stack||e),engineErrors:env.engineErrors(),actionErrors:env.actionErrors||0}));}
}
env.close();
