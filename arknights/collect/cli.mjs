import { Collections } from './store.mjs';
const [root, id, profile] = process.argv.slice(2);
try { console.log(JSON.stringify(new Collections(root).export(id, profile))); }
catch (error) { console.error(error.message); process.exitCode = 1; }
