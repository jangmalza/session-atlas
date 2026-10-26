import {test,expect} from '@playwright/test';
const sid='11111111-1111-4111-8111-111111111111';
test.beforeEach(async({context})=>{
  await context.route('**/*',route=>{const url=new URL(route.request().url());return url.hostname==='127.0.0.1'&&url.port==='18765'?route.continue():route.abort();});
});
async function selectQuestion(page:any){
  await page.goto('/?view=graph');const frame=page.frameLocator('#live-frame');
  await frame.locator(`#mind-sessions [data-key="${sid}"]`).click();
  await frame.locator('#mind-topics .mind-node').first().click();
  await frame.locator('#mind-questions .mind-node').first().click();
  await expect(frame.locator('#source-reader .reader-target')).toBeVisible();return frame;
}
test('four columns, verified source panel and next question preserve original content',async({page},testInfo)=>{
  const frame=await selectQuestion(page);
  await expect(frame.locator('.reader-target .reader-original')).toHaveText('Synthetic question 000 literal <img src=x>.');
  await expect(frame.locator('.reader-target img')).toHaveCount(0);
  const positions=await frame.locator('#mind-canvas>.mind-column').evaluateAll((es:any[])=>es.map(e=>e.getBoundingClientRect().top));
  expect(new Set(positions).size).toBe(1);expect(positions.length).toBe(4);
  await frame.locator('#question-next').click();
  await expect(frame.locator('.reader-target .reader-original')).toHaveText('Synthetic question 001 literal <img src=x>.');
  await page.screenshot({path:testInfo.outputPath('verified-source-panel.png')});
  await frame.locator('#source-reader-close').click();
  await frame.locator('#session-list-mode').click();await frame.locator('#mind-mode').click();
  await expect(frame.locator('#mind-questions [aria-pressed="true"] .name')).toHaveText('E2E question 01');
  await expect(frame.locator('#mind-links .mind-edge-active')).not.toHaveCount(0);
});
test('reader returns to the same old page after navigation and reload',async({page})=>{
  await page.goto('/?view=timeline&session='+sid);let frame=page.frameLocator('#live-frame');
  await expect(frame.locator('#message-list .message').first().locator('.message-heading')).toContainText('#251');
  await frame.locator('#older').click();await expect(frame.locator('#message-list .message').first().locator('.message-heading')).toContainText('#151');
  await frame.locator('#older').click();await expect(frame.locator('#message-list .message').first().locator('.message-heading')).toContainText('#51');
  await frame.locator('#message-list .raw-message').first().locator('summary').first().click();
  await frame.locator('#message-list').evaluate(e=>{e.scrollTop=180;});
  const first=await frame.locator('#message-list .message').first().getAttribute('data-message-id');
  await page.locator('nav [data-view="graph"]').click();await expect(page.frameLocator('#live-frame').locator('#session-list-mode')).toBeVisible();
  await page.locator('nav [data-view="timeline"]').click();frame=page.frameLocator('#live-frame');
  await expect(frame.locator('#message-list .message').first()).toHaveAttribute('data-message-id',first!);
  await page.reload();await expect(page.frameLocator('#live-frame').locator('#message-list .message').first()).toHaveAttribute('data-message-id',first!);
  await expect(page.frameLocator('#live-frame').locator('#follow-latest')).not.toBeChecked();
  await expect(page.frameLocator('#live-frame').locator('#message-list .raw-message').first()).toHaveAttribute('open','');
});
test('search follows stored evidence and bookmarks persist without touching titles',async({page})=>{
  await page.goto('/?view=graph');const frame=page.frameLocator('#live-frame');
  await frame.locator('#explorer-search').click();await frame.locator('#explorer-search-query').fill('Synthetic question 009');
  await frame.locator('#explorer-search-form button').click();
  await frame.locator('.explorer-result button').filter({hasText:'마인드맵'}).first().click();
  await expect(frame.locator('#mind-questions [aria-pressed="true"] .name')).toHaveText('E2E question 09');
  await expect(frame.locator('.reader-target .reader-original')).toContainText('question 009');
  await frame.locator('#explorer-star').click();await page.reload();
  await expect(page.frameLocator('#live-frame').locator('#explorer-star')).toHaveAttribute('aria-pressed','true');
  await page.frameLocator('#live-frame').locator('#explorer-resume').click();
  await expect(page.frameLocator('#live-frame').locator('#source-reader')).toBeVisible();
});
test('management menus and mobile layouts remain usable',async({page})=>{
  const frame=await selectQuestion(page);await frame.locator('#source-reader-close').click();
  await frame.locator('#mind-question-title-action>.node-management>summary').click();
  await frame.locator('#mind-question-title-edit').click();await expect(frame.locator('#item-title-dialog')).toBeVisible();
  await frame.locator('#item-title-close').click();
  await page.setViewportSize({width:390,height:844});await frame.locator('#session-list-mode').click();
  expect(await frame.locator('html').evaluate((e:any)=>e.scrollWidth<=e.clientWidth)).toBe(true);
  await frame.locator('#mind-mode').click();
  expect(await frame.locator('html').evaluate((e:any)=>e.scrollWidth<=e.clientWidth)).toBe(true);
});
test('unanalysed conversations show explicit chronological turns without generating analysis',async({page,request})=>{
  const before=await (await request.get('/status.json')).json();
  await page.goto('/?view=graph');const frame=page.frameLocator('#live-frame');
  await frame.locator('#mind-sessions [data-key="22222222-2222-4222-8222-222222222222"]').click();
  await expect(frame.locator('#mind-session-notice')).toContainText('원문 순서 묶음');
  await frame.locator('#mind-questions [data-kind="raw-request"]').first().click();
  await expect(frame.locator('#mind-answers [data-kind="raw-response"]')).toHaveCount(1);
  await expect(frame.locator('.reader-target .reader-original')).toHaveText('Unanalysed raw message 0');
  for(let i=0;i<8;i++)await frame.locator('#question-next').click();
  await expect(frame.locator('#mind-questions [aria-pressed="true"]')).toHaveAttribute('data-kind','raw-request');
  await expect(frame.locator('.reader-target .reader-original')).toHaveText('Unanalysed raw message 16');
  const after=await (await request.get('/status.json')).json();
  expect(after.analysedSessionCount).toBe(before.analysedSessionCount);expect(after.autoAnalysis).toBe(false);
});
test('display-only updates do not reload the iframe or discard another edit draft',async({page})=>{
  const frame=await selectQuestion(page);await frame.locator('#source-reader-close').click();
  await frame.locator('#mind-session-editor>.node-management>summary').click();
  await frame.locator('#mind-session-name-details>summary').click();
  const origin=await frame.locator('html').evaluate(e=>e.ownerDocument.defaultView!.performance.timeOrigin);
  await frame.locator('#mind-session-name').fill('Temporary E2E alias');await frame.locator('#mind-session-name-save').click();
  await expect(frame.locator('#mind-session-name-status')).toContainText('저장');
  await frame.locator('#mind-session-name').fill('Unsaved draft to preserve');
  let refreshes=0;page.on('request',r=>{if(r.url().includes('/timeline/context.json'))refreshes++;});
  await expect.poll(()=>refreshes,{timeout:10000}).toBeGreaterThan(0);
  await expect(frame.locator('#mind-session-name')).toHaveValue('Unsaved draft to preserve');
  expect(await frame.locator('html').evaluate(e=>e.ownerDocument.defaultView!.performance.timeOrigin)).toBe(origin);
  await frame.locator('#mind-session-name-reset').click();
  await expect(frame.locator('#mind-session-name')).toHaveValue('E2E conversation');
});
test('Claude Code sessions share the catalog and source reader without semantic analysis',async({page})=>{
  await page.goto('/?view=graph');const frame=page.frameLocator('#live-frame');
  await frame.locator('#query').fill('Claude Code');
  await expect(frame.locator('#mind-sessions .mind-session-node')).toHaveCount(1);
  await expect(frame.locator('#mind-sessions .session-project')).toContainText('Claude Code');
  await frame.locator('#mind-sessions .mind-session-node').click();
  await frame.locator('#mind-questions [data-kind="raw-request"]').first().click();
  await expect(frame.locator('.reader-target .reader-original')).toHaveText('Synthetic Claude user');
  await frame.locator('#mind-answers [data-kind="raw-response"]').first().click();
  await expect(frame.locator('.reader-target .reader-original')).toHaveText('Synthetic Claude assistant');
});
