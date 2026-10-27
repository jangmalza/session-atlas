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
  await frame.locator('#mind-session-change').click();await frame.locator('#mind-session-change').click();
  await expect(frame.locator('#mind-questions [aria-pressed="true"] .name')).toHaveText('E2E question 01');
  await expect(frame.locator('#mind-links .mind-edge-active')).not.toHaveCount(0);
});
test('reader keeps its paging position across a reload and resumes from the saved place',async({page})=>{
  const frame=await selectQuestion(page);
  await frame.locator('#source-reader-newer').click();await expect(frame.locator('#source-reader-status')).toContainText('#8');
  const first=await frame.locator('#source-reader-body .reader-message').first().getAttribute('data-message-id');
  await frame.locator('#source-reader-page-size').click();await expect(frame.locator('#source-reader-status')).toContainText('/ 350');
  await page.reload();const again=page.frameLocator('#live-frame');
  await again.locator('#explorer-resume').click();await expect(again.locator('#source-reader')).toBeVisible();
  await expect(again.locator('#source-reader-body .reader-target')).toHaveAttribute('data-message-id',first!);
  await expect(page.locator('nav [data-view]')).toHaveCount(0);
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
  await page.setViewportSize({width:390,height:844});await frame.locator('#mind-session-change').click();
  expect(await frame.locator('html').evaluate((e:any)=>e.scrollWidth<=e.clientWidth)).toBe(true);
  await frame.locator('#mind-session-change').click();
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

test('analysis settings keep synthetic credentials out of browser storage and never start jobs',async({page})=>{
  const key='synthetic-e2e-key-for-isolated-fixture-only';
  await page.goto('/?view=graph');await page.locator('#analysis-settings-open').click();
  await expect(page.locator('#analysis-key-state')).toContainText('미등록');
  await page.locator('#analysis-api-key').fill(key);await page.locator('#analysis-save-key').click();
  await expect(page.locator('#analysis-key-state')).toContainText('Windows 보안 저장소');
  await expect(page.locator('#analysis-api-key')).toHaveValue('');
  expect(await page.evaluate(()=>JSON.stringify([localStorage,sessionStorage]))).not.toContain(key);
  await page.reload();await page.locator('#analysis-settings-open').click();
  await expect(page.locator('#analysis-key-state')).toContainText('등록됨');
  await expect(page.locator('#analysis-api-key')).toHaveValue('');
  await page.locator('#analysis-delete-key').click();await expect(page.locator('#analysis-key-state')).toContainText('미등록');
  expect((await (await page.request.get('/analysis/job.json')).json()).state).toBe('idle');
});

test('selected-session analysis requires consent and updates only after an isolated fake worker completes',async({page},testInfo)=>{
  let starts=0;page.on('request',request=>{if(request.url().endsWith('/analysis/start.json'))starts++;});
  await page.goto('/?view=graph');const frame=page.frameLocator('#live-frame');
  const raw='22222222-2222-4222-8222-222222222222';
  await frame.locator(`#mind-sessions [data-key="${raw}"]`).click();await frame.locator('#explorer-analyze').click();
  await expect(page.locator('#analysis-session-id')).toHaveText(raw);
  await expect(page.locator('#analysis-start')).toBeDisabled();
  await expect(page.locator('#analysis-blocked')).toContainText('로그인 상태');
  await page.locator('#analysis-open-setup').click();await page.locator('#analysis-check-codex').click();
  await expect(page.locator('#analysis-codex-state')).toContainText('로그인 확인됨');
  await page.locator('#analysis-settings-close').click();await expect(page.locator('#analysis-scope')).toContainText('24개');
  await expect(page.locator('#analysis-start')).toBeDisabled();
  await page.locator('#analysis-consent').check();await expect(page.locator('#analysis-start')).toBeEnabled();
  await page.screenshot({path:testInfo.outputPath('verified-analysis-dialog.png')});
  await page.locator('#analysis-start').click();await expect(page.locator('#analysis-start')).toBeDisabled();
  await expect(page.locator('#analysis-progress')).toContainText('완료',{timeout:20000});
  expect(starts).toBe(1);
  await page.locator('#analysis-run-close').click();
  await expect(frame.locator('#mind-sessions [aria-pressed="true"]')).toHaveAttribute('data-key',raw);
  await expect(frame.locator('#mind-topics')).toContainText('Synthetic generated topic',{timeout:15000});
  await frame.locator('#explorer-analyze').click();
  await expect(page.locator('#analysis-mode')).toHaveValue('decisions');
  await page.locator('#analysis-mode').selectOption('codex');
  await expect(page.locator('#analysis-blocked')).toContainText('이미 분석된 세션');
  await expect(page.locator('#analysis-start')).toBeDisabled();
});
