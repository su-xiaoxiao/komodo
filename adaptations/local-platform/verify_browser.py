"""Read-only browser acceptance for the isolated localized Komodo instance."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[2]
base = 'http://127.0.0.1:28793'
settings = dict(line.split('=', 1) for line in (root / '.local/komodo.env').read_text().splitlines() if '=' in line)
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(base, wait_until='networkidle')
    page.get_by_label('用户名', exact=True).wait_for()
    page.screenshot(path=str(root / '.local/login-zh.png'), full_page=True)
    page.get_by_label('用户名', exact=True).fill('admin')
    page.get_by_label('密码', exact=True).fill(settings['KOMODO_LOCAL_PASSWORD'])
    page.get_by_role('button', name='登录', exact=True).click()
    page.get_by_role('link', name='概览', exact=True).wait_for()
    page.locator('main').get_by_text('概览', exact=True).first.wait_for()
    page.wait_for_load_state('networkidle')
    page.screenshot(path=str(root / '.local/dashboard-zh.png'), full_page=True)
    assert page.locator('a[href="http://127.0.0.1:28789"]').count() > 0
    page.get_by_role('button', name='English', exact=True).click()
    page.get_by_role('link', name='Dashboard', exact=True).wait_for()
    page.locator('main').get_by_text('Dashboard', exact=True).first.wait_for()
    page.wait_for_load_state('networkidle')
    assert page.locator('html').get_attribute('lang') == 'en'
    page.screenshot(path=str(root / '.local/dashboard-en.png'), full_page=True)
    page.get_by_role('button', name='简体中文', exact=True).click()
    page.get_by_role('link', name='概览', exact=True).wait_for()
    assert page.locator('html').get_attribute('lang') == 'zh-CN'
    for route, title in [('/stacks', '应用栈（容器组）'), ('/builds', '镜像构建'), ('/procedures', '流水线'), ('/containers', '容器'), ('/platform', '本机平台')]:
        page.goto(base + route, wait_until='networkidle')
        page.locator('main').get_by_text(title, exact=True).first.wait_for()
        assert page.locator('html').get_attribute('lang') == 'zh-CN'
    page.screenshot(path=str(root / '.local/containers-zh.png'), full_page=True)
    assert not errors, errors
    report = {'login': True, 'language_switch': True, 'mcp_link': True,
              'routes': ['/', '/stacks', '/builds', '/procedures', '/containers', '/platform'], 'page_errors': errors}
    (root / '.local/browser-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    browser.close()
