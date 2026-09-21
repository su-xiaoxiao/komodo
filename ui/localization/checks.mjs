export function placeholders(text) {
  return [...text.matchAll(/\{[A-Za-z0-9_]+\}|\[\[\$[A-Za-z0-9_]+\]\]/g)].map(m => m[0]).sort();
}
export function issues(catalog, dictionary, retained) {
  const missing = Object.keys(catalog).filter(k => !dictionary[k] && !retained.includes(k));
  const invalid = Object.entries(dictionary).filter(([key, value]) =>
    !key || typeof value !== 'string' || !value.trim() ||
    JSON.stringify(placeholders(key)) !== JSON.stringify(placeholders(value)));
  return { missing, invalid };
}
