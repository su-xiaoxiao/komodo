import { Button } from '@mantine/core';
import { getLocale, setLocale } from './runtime';

export default function LanguageSwitch() {
  const chinese = getLocale() === 'zh-CN';
  return <Button variant="subtle" size="compact-sm"
    title={chinese ? 'Switch to English (reloads page)' : '切换简体中文（刷新页面）'}
    onClick={() => setLocale(chinese ? 'en' : 'zh-CN')}>
    {chinese ? 'English' : '简体中文'}
  </Button>;
}
