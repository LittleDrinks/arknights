import { replay } from './replay.mjs';
try { console.log(JSON.stringify(await replay(JSON.parse(process.argv[2]), progress => process.send?.(progress)))); }
catch (e) { console.error(e.stack || e); process.exitCode = 1; }
