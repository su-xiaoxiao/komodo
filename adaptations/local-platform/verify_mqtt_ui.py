"""Explicitly execute one MQTT procedure through its real browser confirmation dialog."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from komodo_api import Komodo

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', required=True, choices=['mqtt-release', 'mqtt-restore'])
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
api = Komodo(root)
procedure = next(p for p in api.call('read/ListProcedures', {}) if p['name'] == args.run)
settings = dict(line.split('=', 1) for line in (root/'.local/komodo.env').read_text().splitlines() if '=' in line)
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:28793')
    page.get_by_label('用户名', exact=True).fill('admin')
    page.get_by_label('密码', exact=True).fill(settings['KOMODO_LOCAL_PASSWORD'])
    page.get_by_role('button', name='登录', exact=True).click()
    page.get_by_role('link', name='概览', exact=True).wait_for()
    page.goto('http://127.0.0.1:28793/procedures/'+procedure['id'], wait_until='networkidle')
    page.get_by_role('button', name='运行流水线', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.wait_for()
    dialog.locator('input').fill(args.run)
    with page.expect_response(lambda response: '/execute/RunProcedure' in response.url) as submitted:
        dialog.get_by_role('button', name='运行流水线', exact=True).click()
    response = submitted.value
    assert response.ok, response.status
    execution = response.json()
    dialog.wait_for(state='hidden')
    page.screenshot(path=str(root/'.local'/f'{args.run}-browser.png'), full_page=True)
    assert not errors, errors
    report = {'procedure':args.run,'browser_confirmed':True,'page_errors':errors,'execution':execution}
    (root/'.local'/f'{args.run}-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'procedure':args.run,'update_id':execution['_id']['$oid'],'page_errors':errors}))
    browser.close()
