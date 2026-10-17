import { replay } from './replay.mjs';

try {
  const result = await replay(JSON.parse(process.argv[2]));
  console.log(JSON.stringify(result));
} catch (error) {
  console.error(error.stack || error);
  process.exitCode = 1;
}
