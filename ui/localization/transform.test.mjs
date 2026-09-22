import fs from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse } from '@babel/parser';
import { scan, transform, jsxText } from './transform.mjs';
import { issues } from './checks.mjs';

test('translates only display literals, preserving enums and user values', () => {
  const source = `const View = () => <Button value="Running" onClick={() => api("DeployStack")} title="Deploy">{name}{state === "Running" ? "Stop" : "Start"}</Button>`;
  const result = transform(source, 'view.tsx').code;
  assert.match(result, /value="Running"/);
  assert.match(result, /api\("DeployStack"\)/);
  assert.match(result, /state === "Running"/);
  assert.match(result, /\{name\}/);
  assert.match(result, /__komodoTranslate\("Stop"\)/);
  parse(result, { sourceType: 'module', plugins: ['typescript', 'jsx'] });
});
test('does not translate code or terminal content', () => {
  assert.deepEqual(scan('<Code>docker compose up</Code>'), []);
  assert.deepEqual(scan('<Terminal title="Shell">Hello</Terminal>'), []);
});
test('localizes shared UI compiled JSX without touching submitted form values', () => {
  const source = `import {jsx as j} from 'react/jsx-runtime'; const x=j(Input,{label:'Username',name:'username',value:'admin',children:'Login'});`;
  const result = transform(source, 'shared.js').code;
  assert.match(result, /label:__komodoTranslate\("Username"\)/);
  assert.match(result, /value:'admin'/);
  assert.match(result, /name:'username'/);
});
test('preserves React whitespace, entities and adjacent expressions', () => {
  assert.equal(jsxText('\n  Hello\n  world\n'), 'Hello world');
  const code = transform('<Text>Delete {name} now &amp; later</Text>', 'view.tsx').code;
  assert.match(code, /"Delete "/);
  assert.match(code, /" now & later"/);
});
test('upstream new or changed English is missing, placeholders must survive', () => {
  const result = issues({'New title': [], 'Path {name}': []}, {'Old title': '旧标题', 'Path {name}': '路径'}, []);
  assert.deepEqual(result.missing, ['New title']);
  assert.equal(result.invalid.length, 1);
});
test('template placeholders preserve user expressions and API values', () => {
  const result = transform('const View = () => <Button value="Delete">{`Delete ${name}?`}</Button>', 'view.tsx').code;
  assert.match(result, /"Delete \{0\}\?", \{"0": \(name\)\}/);
  assert.match(result, /value="Delete"/);
  parse(result, {sourceType: 'module', plugins: ['typescript', 'jsx']});
});

test('explicit display translations join catalog without double wrapping', () => {
  const source = `import {t as translate} from '@/localization/runtime'; const config={description:translate('Host connection help')}; const other=t('User data');`;
  assert.deepEqual(scan(source).map(e => e.source), ['Host connection help']);
  assert.equal(transform(source, 'config.tsx'), null);
});

test('server display metadata and fragments preserve configuration keys and values', () => {
 const source = `const view=<Config groups={{alerts:[{fields:{auto_rotate_keys:{description:"Rotate keys",placeholder:"Region"}}}]}}><>Server help</></Config>; const column={header:"Hostname",accessorKey:"host_name"};`;
 const output=transform(source,'src/resources/server/config.tsx').code;
 assert.match(output,/auto_rotate_keys:/);
 assert.match(output,/accessorKey:"host_name"/);
 for(const text of ['Rotate keys','Region','Server help','Hostname']) assert.ok(output.includes('__komodoTranslate('+JSON.stringify(text)+')'));
 parse(output,{sourceType:'module',plugins:['typescript','jsx']});
});

test('historical chart keys stay English while display labels translate', () => {
 const src=fs.readFileSync(new URL('../src/resources/server/stats/historical.tsx',import.meta.url),'utf8');
 const out=transform(src,'src/resources/server/stats/historical.tsx').code;
 assert.match(out,/key: "Used"/);
 assert.match(out,/key: "Cache\/Buffers"/);
 assert.match(out,/colors\[series.key\]/);
 assert.match(out,/dataKey=\{series.key\}/);
 assert.ok(out.includes('t(String(entry.value))'));
 assert.ok(out.includes('value, label: t(value)'));
 assert.ok(!out.includes('key: __komodoTranslate'));
});

test('workflow localization leaves executable examples and payload values intact', () => {
 const source=`const config={failure_alert:{label:"Failure Alert",description:"Send alerts"}}; const v=<><Select value="Cron"/><code>Run every day at 4:00 pm</code><MonacoEditor value="RunAction"/><Text>Workflow help</Text></>; api({type:"RunAction",params:{action:"example"}});`;
 const out=transform(source,'src/resources/procedure/config/index.tsx').code;
 assert.ok(out.includes('description:__komodoTranslate("Send alerts")'));
 assert.ok(out.includes('<code>Run every day at 4:00 pm</code>'));
 assert.ok(out.includes('value="RunAction"'));
 assert.ok(out.includes('type:"RunAction"'));
 assert.ok(out.includes('value="Cron"'));
 parse(out,{sourceType:'module',plugins:['typescript','jsx']});
});
test('every supported workflow execution has a separate display label', () => {
 const impl=fs.readFileSync(new URL('../src/resources/procedure/config/executions.tsx',import.meta.url),'utf8');
 const labels=fs.readFileSync(new URL('../src/resources/procedure/config/execution-labels.tsx',import.meta.url),'utf8');
 const keys=[...impl.matchAll(/^  (\w+): \{/gm)].map(m=>m[1]);
 const names=[...labels.matchAll(/^  (\w+): \(\) => t\(/gm)].map(m=>m[1]);
 assert.deepEqual(keys.sort(),names.sort());
 const selector=fs.readFileSync(new URL('../src/resources/procedure/config/execution-selector.tsx',import.meta.url),'utf8');
 assert.ok(selector.includes('value={type}'));
 assert.ok(selector.includes('onSelect?.(type as Types.Execution["type"])'));
 assert.ok(selector.includes('item + " " + executionLabel(item)'));
});
