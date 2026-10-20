// Compile the external overlay and pinned browser modules without touching game/.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { RULES } from './export.mjs';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

export async function buildClient(root = ROOT) {
  const WEB = path.join(root, 'arknights/replay/web');
  const COLLECT = path.join(root, 'arknights/collect/web');
  const out = path.join(root, 'runtime/web/client');
  fs.mkdirSync(out, { recursive: true });
  const vendor = {};
  for (const name of ['pixi.min.js', 'pixi-spine.js']) {
    const bytes = fs.readFileSync(path.join(root, 'runtime/web/vendor', name));
    const filename = `vendor-${sha(bytes).slice(0, 16)}.js`;
    fs.writeFileSync(path.join(out, filename), bytes);
    vendor['/vendor/' + name] = '/client/' + filename;
  }
  const original = fs.readFileSync(path.join(root, 'game/public/index.html'), 'utf8');
  const styles = [...original.matchAll(/<link rel="stylesheet" href="(\/css\/[^\"]+)"\s*\/>/g)].map(m => m[1]);
  const inputs = {};
  inputs['game/public/index.html'] = sha(original);
  inputs['arknights/collect/web/replay.html'] = sha(fs.readFileSync(path.join(COLLECT, 'replay.html')));
  const resolveFile = url => {
    const candidates = url.startsWith('/collect/') ? [path.join(COLLECT, url.slice('/collect/'.length))]
      : url.startsWith('/vendor/') ? [path.join(root, 'runtime/web', url)]
      : url.startsWith('/shared/') ? [path.join(root, 'game', url)]
      : url.startsWith('/sim/') ? [path.join(root, 'game/server', url)]
      : [path.join(WEB, url), path.join(root, 'game/public', url)];
    return candidates.find(file => fs.existsSync(file) && fs.statSync(file).isFile());
  };
  const result = await build({
    entryPoints: { main: '/js/main.js', renderer: '/js/render/app.js', assets: '/js/assets.js',
      collect: '/collect/replay.mjs', styles: '/client-styles.css' },
    outdir: out, bundle: true, splitting: true, format: 'esm', platform: 'browser', target: 'es2022',
    entryNames: '[name]-[hash]', chunkNames: 'chunk-[hash]', minify: true, metafile: true,
    plugins: [{ name: 'browser-overlay', setup(ctx) {
      ctx.onResolve({ filter: /.*/ }, args => {
        if (/^(https?:|data:|blob:)/.test(args.path)) return { path: args.path, external: true };
        const aliases = { preact: '/vendor/preact.module.js', 'preact/hooks': '/vendor/hooks.module.js', htm: '/vendor/htm.module.js' };
        const url = aliases[args.path] || new URL(args.path, 'http://local' + (args.importer || '/')).pathname;
        // Preserve the collector's single, dynamically served Net instance and URL-relative worker/plugin loaders.
        if (args.kind === 'url-token' || ['/js/net.js', '/js/battle/simHost.js', '/shared/plugins/scriptLoad.js',
          '/shared/plugins/sandbox.js', '/shared/plugins/sdk.js', '/js/render/board3d/load.js'].includes(url)
          || (url.startsWith('/vendor/') && !['/vendor/preact.module.js', '/vendor/hooks.module.js', '/vendor/htm.module.js'].includes(url)))
          return { path: url, external: true };
        return { path: url, namespace: 'browser' };
      });
      ctx.onLoad({ filter: /.*/, namespace: 'browser' }, args => {
        if (args.path === '/client-styles.css') return { contents: styles.map(url => `@import ${JSON.stringify(url)};`).join('\n'), loader: 'css' };
        const file = resolveFile(args.path);
        if (!file) throw Error('Missing browser module: ' + args.path);
        let contents = fs.readFileSync(file, 'utf8');
        inputs[path.relative(root, file)] = sha(contents);
        if (args.path === '/js/battle/runner.js') {
          // Server-authoritative play and recorded-event playback need no speculative local simulation.
          // The upstream warm-up downloads the entire simulator beside the renderer and can exhaust its load timeout.
          // Rehearsal and genuine client-combat paths still call ensureSim explicitly when needed.
          const before = "if (phase && phase !== 'LOBBY' && (!simP || simCs !== (roomCs || null)))";
          if (contents.split(before).length !== 2) throw Error('Pinned runner warm-up changed; review the browser adapter');
          contents = contents.replace(before, "if (pub?.combatMode !== 'server' && phase && phase !== 'LOBBY' && (!simP || simCs !== (roomCs || null)))");
        }
        if (args.path === '/js/render/app/pixi.js') for (const [before, after] of Object.entries(vendor)) contents = contents.replaceAll(before, after);
        return { contents, loader: args.path.endsWith('.css') ? 'css' : 'js' };
      });
    } }],
  });
  const outputs = Object.entries(result.metafile.outputs);
  const entry = url => '/client/' + path.basename(outputs.find(([, info]) => info.entryPoint === 'browser:' + url)?.[0] || '');
  const preload = new Set();
  const visit = filename => {
    const pair = outputs.find(([name]) => path.basename(name) === filename);
    if (!pair || preload.has(filename)) return;
    preload.add(filename);
    for (const dep of pair[1].imports) if (!dep.external && dep.kind === 'import-statement') visit(path.basename(dep.path));
  };
  for (const url of ['/js/main.js', '/js/render/app.js', '/js/assets.js']) visit(path.basename(entry(url)));
  const warm = [...preload].map(name => `<link rel="modulepreload" href="/client/${name}">`).join('\n')
    + Object.values(vendor).map(url => `<script defer src="${url}"></script>`).join('\n');
  const optimize = (html, main) => html
    .replace(/<link rel="(?:modulepreload|prefetch)"[^>]*>/g, '')
    .replace(/<link rel="stylesheet" href="\/css\/[^\"]+"\s*\/?\s*>/g, '')
    .replace(/(<script type="module" src=")[^"]+("[^>]*>)/, `$1${main}$2`)
    .replace('</head>', `<link rel="stylesheet" href="${entry('/client-styles.css')}">\n${warm}\n</head>`);
  const files = {};
  for (const name of [...outputs.map(([name]) => path.basename(name)), ...Object.values(vendor).map(url => path.basename(url))]) {
    files[name] = sha(fs.readFileSync(path.join(out, name)));
  }
  const manifest = { version: 1, rules: RULES, files, inputs,
    index: optimize(original, entry('/js/main.js')),
    collect: optimize(fs.readFileSync(path.join(COLLECT, 'replay.html'), 'utf8'), entry('/collect/replay.mjs')) };
  const target = path.join(root, 'runtime/web/client.json');
  fs.writeFileSync(target + '.tmp', JSON.stringify(manifest)); fs.renameSync(target + '.tmp', target);
  return { files: Object.keys(files).length, entries: outputs.filter(([, v]) => v.entryPoint).map(([p]) => path.basename(p)) };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) console.log(JSON.stringify(await buildClient()));
