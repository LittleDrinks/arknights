// Static data only. Never instantiate a Match or call reset/step.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { createHash } from 'node:crypto';
const [game, code] = process.argv.slice(2);
const { getData } = await import(pathToFileURL(path.join(game, 'server/data.js')));
const { makeSchema } = await import(pathToFileURL(path.join(code, 'rl/features.mjs')));
const fail = message => { throw new Error(message); };
const data = getData({ log: { info() {}, warn: fail, error: fail } });
console.log(JSON.stringify({ schema: makeSchema(data),
  dataSHA256: createHash('sha256').update(JSON.stringify(data)).digest('hex') }));
