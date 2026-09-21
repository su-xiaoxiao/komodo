import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { scan } from './transform.mjs';
import { issues } from './checks.mjs';

const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const result = new Map();
for (const [folder, suffix] of [['src', '.tsx'], ['node_modules/mogh_ui/dist', '.js']]) {
  const directory = path.join(ui, folder);
  for (const file of fs.readdirSync(directory, { recursive: true }).sort()) {
    if (!file.endsWith(suffix) || file.replaceAll('\\', '/').startsWith('localization/')) continue;
    const filename = `${folder}/${file.replaceAll('\\', '/')}`;
    for (const entry of scan(fs.readFileSync(path.join(directory, file), 'utf8'), filename)) {
      if (!result.has(entry.source)) result.set(entry.source, []);
      result.get(entry.source).push(`${filename}:${entry.line}`);
    }
  }
}
const catalog = Object.fromEntries([...result.entries()].sort(([a], [b]) => a.localeCompare(b, 'en')));
const catalogFile = path.join(ui, 'localization/catalog.json');
const serialized = JSON.stringify(catalog, null, 2) + '\n';
if (process.argv.includes('--extract')) fs.writeFileSync(catalogFile, serialized);
const chinese = JSON.parse(fs.readFileSync(path.join(ui, 'src/localization/zh-CN.json'), 'utf8'));
const retainedFile = path.join(ui, 'localization/retained.json');
const retained = fs.existsSync(retainedFile) ? JSON.parse(fs.readFileSync(retainedFile, 'utf8')) : [];
const { missing, invalid } = issues(catalog, chinese, retained);
const stale = Object.keys(chinese).filter(k => !catalog[k]);
const baseline = fs.existsSync(catalogFile) ? JSON.parse(fs.readFileSync(catalogFile, 'utf8')) : {};
const added = Object.keys(catalog).filter(k => !baseline[k]);
const removed = Object.keys(baseline).filter(k => !catalog[k]);
const report = { messages: Object.keys(catalog).length, translated: Object.keys(catalog).length - missing.length - retained.filter(k => catalog[k]).length, missing, retained: retained.filter(k => catalog[k]), stale, added, removed, invalid };
fs.mkdirSync(path.join(ui, 'localization/reports'), { recursive: true });
fs.writeFileSync(path.join(ui, 'localization/reports/coverage.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify({ ...report, missing: missing.length, retained: report.retained.length, stale: stale.length }, null, 2));
if (process.argv.includes('--check') && (missing.length || added.length || removed.length || invalid.length)) process.exitCode = 1;
