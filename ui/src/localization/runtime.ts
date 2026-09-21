import zh from './zh-CN.json';

export type Locale = 'zh-CN' | 'en';
const dictionary: Record<string, string> = zh;
const key = 'komodo.locale';
export function getLocale(): Locale {
  try { return localStorage.getItem(key) === 'en' ? 'en' : 'zh-CN'; }
  catch { return 'zh-CN'; }
}
export function setLocale(locale: Locale) {
  try { localStorage.setItem(key, locale); } catch { /* Storage may be disabled. */ }
  window.location.reload();
}
export function t(source: string, params?: Record<string, unknown>): string {
  const message = getLocale() === 'zh-CN' ? dictionary[source] ?? source : source;
  return params ? message.replace(/\{(\d+)\}/g, (match, key: string) => key in params ? String(params[key]) : match) : message;
}
document.documentElement.lang = getLocale();
