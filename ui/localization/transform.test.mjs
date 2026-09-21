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
