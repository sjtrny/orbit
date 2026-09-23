const {chromium} = require('/workspaces/codex-web-home/codex-web/demo/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.ORBIT_URL || 'http://192.168.20.10:18008';
const artifacts = path.resolve(__dirname, '../artifacts');

(async () => {
  const browser = await chromium.launch({headless:true});
  const checks = [];
  const errors = [];
  const pass = name => { checks.push(name); console.log(`PASS: ${name}`); };
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/#volume=storage1&node=1`, {waitUntil:'networkidle'});
    await page.waitForSelector('.chart-arc');

    assert.equal(await page.locator('#volume-select option').count(), 11);
    assert.deepEqual(await page.locator('#volume-select optgroup').evaluateAll(groups => groups.map(group => group.label)), ['File systems', 'Disks']);
    assert.equal((await page.locator('body').innerText()).includes('Storage pool'), false);
    assert.equal(await page.locator('[data-view], #file-search, #activity-view, #help-dialog').count(), 0);
    assert.match(await page.locator('#used-summary').innerText(), /used$/);
    pass('Single-purpose UI with all discovered targets and live capacity');

    const root = await (await page.request.get(`${base}/api/tree?volume=storage1`)).json();
    const folder = root.children.find(item => item.kind === 'directory' && item.size > 0);
    const segment = page.locator(`.chart-arc[data-key="${folder.id}"]`).first();
    await segment.focus();
    assert.equal(await page.locator('#center-name').innerText(), folder.name);
    await segment.press('Enter');
    await page.waitForFunction(id => document.querySelector('#breadcrumbs button:last-child')?.dataset.node === String(id), folder.id);
    await page.locator('#chart-center').click();
    await page.waitForFunction(() => document.querySelector('#breadcrumbs button:last-child')?.dataset.node === '1');
    pass('Keyboard drill-down, breadcrumbs, and center back action');

    await page.locator(`.directory-row[data-item="${folder.id}"]`).click();
    await page.waitForFunction(id => document.querySelector('#breadcrumbs button:last-child')?.dataset.node === String(id), folder.id);
    await page.locator('#up-button').click();
    await page.waitForFunction(() => document.querySelector('#breadcrumbs button:last-child')?.dataset.node === '1');
    pass('Folder list and up navigation');

    await page.locator('#volume-select').selectOption('storage2');
    await page.waitForFunction(() => document.querySelector('#volume-title')?.textContent === 'storage2');
    await page.waitForSelector('.chart-arc');
    assert.match(await page.locator('#breadcrumbs').innerText(), /\/pools\/storage2/);
    pass('Volume switching');

    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path:path.join(artifacts, 'minimal-desktop.png'), fullPage:true, animations:'disabled'});
    pass('Desktop layout without horizontal overflow');

    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.locator('.map').evaluate(element => getComputedStyle(element).borderRightWidth), '0px');
    await page.screenshot({path:path.join(artifacts, 'minimal-mobile.png'), fullPage:true, animations:'disabled'});
    pass('Mobile stacked layout without horizontal overflow');

    assert.deepEqual(errors, []);
    pass('No uncaught browser errors');
    fs.writeFileSync(path.join(artifacts, 'browser-results.json'), JSON.stringify({at:new Date().toISOString(),url:base,checks,errors}, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
