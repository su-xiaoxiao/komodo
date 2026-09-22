import { parse } from '@babel/parser';

// Only presentation literals. API enum values, identifiers, code and user data
// are never replaced. Keep upstream TSX unchanged: this runs before Vite JSX.
const attributes = new Set(['label', 'title', 'description', 'placeholder', 'tooltip', 'aria-label', 'emptyMessage']);
const excluded = /^(?:Code|CodeEditor|Editor|MonacoEditor|Terminal|pre|code|script|style)$/;

export function jsxText(raw) {
  const lines = raw.replace(/\r/g, '').split('\n');
  const result = [];
  lines.forEach((line, i) => {
    line = line.replace(/\t/g, ' ');
    if (i !== 0) line = line.replace(/^ +/, '');
    if (i !== lines.length - 1) line = line.replace(/ +$/, '');
    if (line) result.push(line);
  });
  return result.join(' ');
}

export function scan(source, filename = 'input.tsx') {
  const ast = parse(source, { sourceType: 'module', plugins: ['typescript', 'jsx'] });
  const entries = [];
  const resourceView = /resources\/(server|procedure|action)\//.test(filename.replaceAll('\\', '/'));
  const jsxCalls = new Set();
  const translators = new Set();
  for (const statement of ast.program.body) {
    if (statement.type === 'ImportDeclaration' && statement.source.value === '@/localization/runtime') {
      for (const spec of statement.specifiers) if (spec.imported?.name === 't') translators.add(spec.local.name);
    }
    if (statement.type === 'ImportDeclaration' && statement.source.value === 'react/jsx-runtime') {
      for (const spec of statement.specifiers) if (['jsx', 'jsxs'].includes(spec.imported?.name)) jsxCalls.add(spec.local.name);
    }
  }
  function literal(node, wrapped) {
    const value = node.type === 'JSXText' ? jsxText(node.value) : node.value;
    if (typeof value !== 'string' || !/[A-Za-z]/.test(value) || !value.trim()) return;
    entries.push({ source: value, start: node.start, end: node.end, line: node.loc.start.line, file: filename, wrapped });
  }
  function expression(node, wrapped = false) {
    if (!node) return;
    if (node.type === 'StringLiteral') literal(node, wrapped);
    else if (node.type === 'TemplateLiteral') {
      const value = node.quasis.map((part, i) => part.value.cooked + (i < node.expressions.length ? `{${i}}` : '')).join('');
      if (/[A-Za-z]/.test(value)) entries.push({ source: value, start: node.start, end: node.end,
        line: node.loc.start.line, file: filename, wrapped,
        params: node.expressions.map(part => source.slice(part.start, part.end)) });
    }
    // Only output branches, never the condition / enum comparison.
    else if (node.type === 'ConditionalExpression') { expression(node.consequent); expression(node.alternate); }
    else if (node.type === 'LogicalExpression') expression(node.right);
  }
  function visit(node, blocked = false) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'CallExpression' && translators.has(node.callee.name) && node.arguments[0]?.type === 'StringLiteral') {
      const arg = node.arguments[0];
      entries.push({ source: arg.value, start: arg.start, end: arg.end, line: arg.loc.start.line, file: filename, manual: true });
      return;
    }
    if (node.type === 'ObjectProperty' && !node.computed && (resourceView ? ['label', 'title', 'description', 'placeholder', 'header'] : ['label', 'title']).includes(node.key.name ?? node.key.value)) {
      expression(node.value);
    }
    if (node.type === 'CallExpression' && jsxCalls.has(node.callee.name)) {
      const tag = node.arguments[0];
      if (excluded.test(tag?.name ?? tag?.value ?? '')) return;
      const props = node.arguments[1];
      for (const prop of props?.properties ?? []) {
        const name = prop.key?.name ?? prop.key?.value;
        if (attributes.has(name) || name === 'children') {
          expression(prop.value);
          if (prop.value?.type === 'ArrayExpression') prop.value.elements.forEach(v => expression(v));
        }
        visit(prop.value);
      }
      return;
    }
    if (resourceView && node.type === 'JSXFragment') {
      for (const child of node.children) {
        if (child.type === 'JSXText') literal(child, true);
        else if (child.type === 'JSXExpressionContainer') { expression(child.expression); visit(child.expression); }
        else visit(child);
      }
      return;
    }
    if (node.type === 'JSXElement') {
      const name = node.openingElement.name;
      blocked ||= name.type === 'JSXIdentifier' && excluded.test(name.name);
      if (blocked) return;
      for (const attr of node.openingElement.attributes) {
        if (attr.type !== 'JSXAttribute') continue;
        if (attributes.has(attr.name.name)) {
          if (attr.value?.type === 'StringLiteral') literal(attr.value, true);
          else if (attr.value?.type === 'JSXExpressionContainer') expression(attr.value.expression);
        }
        if (attr.value?.type === 'JSXExpressionContainer') visit(attr.value.expression);
      }
      for (const child of node.children) {
        if (child.type === 'JSXText') literal(child, true);
        else if (child.type === 'JSXExpressionContainer') { expression(child.expression); visit(child.expression); }
        else visit(child);
      }
      return;
    }
    for (const [key, value] of Object.entries(node)) {
      if (['loc', 'extra', 'comments', 'tokens'].includes(key)) continue;
      if (Array.isArray(value)) value.forEach(v => visit(v, blocked));
      else if (value && typeof value === 'object' && value.type) visit(value, blocked);
    }
  }
  visit(ast);
  const unique = [...new Map(entries.map(e => [e.start, e])).values()];
  return unique.filter(e => !unique.some(parent => parent.start < e.start && parent.end >= e.end));
}

export function transform(source, filename) {
  const entries = scan(source, filename).filter(e => !e.manual);
  if (!entries.length) return null;
  let code = source;
  for (const e of entries.sort((a, b) => b.start - a.start)) {
    const params = e.params?.length ? `, {${e.params.map((value, i) => `${JSON.stringify(String(i))}: (${value})`).join(',')}}` : '';
    const call = `__komodoTranslate(${JSON.stringify(e.source)}${params})`;
    code = code.slice(0, e.start) + (e.wrapped ? `{${call}}` : call) + code.slice(e.end);
  }
  return { code: `import { t as __komodoTranslate } from '@/localization/runtime';\n${code}`, map: null };
}

export default function localizationPlugin() {
  return {
    name: 'komodo-zh-cn', enforce: 'pre',
    transform(code, id) {
      const file = id.replaceAll('\\', '/').split('?')[0];
      const app = file.includes('/ui/src/') && !file.includes('/localization/') && file.endsWith('.tsx');
      const shared = file.includes('/node_modules/mogh_ui/dist/') && file.endsWith('.js');
      if (!app && !shared) return null;
      return transform(code, file);
    },
  };
}
