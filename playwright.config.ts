import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'tests/e2e',fullyParallel:false,workers:1,timeout:30000,
  retries:process.env.CI?1:0,
  use:{baseURL:'http://127.0.0.1:18765',viewport:{width:1440,height:1000},trace:'retain-on-failure',screenshot:'only-on-failure'},
  reporter:[['list'],['html',{open:'never'}]],
  webServer:{command:`"${process.env.E2E_PYTHON||'python'}" tests/e2e/server.py`,url:'http://127.0.0.1:18765/status.json',reuseExistingServer:false,timeout:30000,
    env:{PYTHONUTF8:'1',OPENAI_API_KEY:'',ANTHROPIC_API_KEY:''}},
});
