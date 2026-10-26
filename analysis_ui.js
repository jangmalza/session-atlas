(() => {
  'use strict';
  const byId=id=>document.getElementById(id),frame=byId('live-frame');
  document.querySelector('.heading').append(byId('analysis-actions'));
  const settingsDialog=byId('analysis-settings'),runDialog=byId('analysis-run');
  const active=new Set(['queued','backing_up','analyzing','validating','building','classifying','saving']);
  const stages={idle:'대기 중',queued:'분석 대기',backing_up:'기존 데이터 백업 중',analyzing:'Codex 의미 분석 중',validating:'근거 검증 중',building:'분석 결과 생성 중',classifying:'Decisions 주제 배정 중',saving:'검증된 결과 반영 중',completed:'완료 · 화면에 결과를 반영합니다',failed:'실패 · 기존 결과 유지',interrupted:'작업 중단 · 자동 재실행하지 않음'};
  const errors={invalid_settings:'설정 값을 확인해 주세요.',invalid_cm_root:'Conversation Map 설치 폴더를 확인해 주세요.',invalid_key:'키는 공백 없이 16~2,048자의 문자로 입력해 주세요.',job_busy:'다른 분석이 실행 중입니다. 완료 후 다시 실행하세요.',source_changed:'대화 또는 분석 결과가 변경되었습니다. 대상을 다시 확인하세요.',existing_analysis_preserved:'이미 분석된 세션입니다. 기존 분석은 유지하며 Decisions 주제 배정만 실행할 수 있습니다.',analysis_required:'먼저 Codex 의미 분석이 필요합니다.',setup_required:'Codex CLI·Node.js·Conversation Map 설치 상태를 확인하세요.',check_codex_required:'분석 설정에서 Codex 로그인 상태를 확인하세요.',key_required:'분석 설정에서 Decisions API 키를 등록하세요.',no_messages:'분석할 대화 메시지가 없습니다.',consent_required:'선택한 작업의 전송 범위에 동의해야 합니다.',worker_stopped:'작업 프로세스가 종료되었습니다. 기존 결과는 유지됩니다.',stage_failed:'해당 단계에서 실패했습니다. 설정을 확인한 뒤 명시적으로 다시 실행해 주세요.',operation_failed:'처리하지 못했습니다. 서버 설정이나 보안 저장소 상태를 확인하세요.',settings_unavailable:'설정을 읽지 못했습니다. 서버의 보안 저장소 상태를 확인하세요.'};
  let session='',title='',plan=null,job={state:'idle'},serial=0,submitting=false,settingsBusy=false;
  function message(code){return errors[code]||'요청을 처리하지 못했습니다. 설정과 서버 연결을 확인해 주세요.';}
  async function request(path,body){
    const options={cache:'no-store'};
    if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-Context-Graph-Action':path.split('/').pop().replace('.json','')};options.body=JSON.stringify(body);}
    const response=await fetch('/analysis/'+path,options),value=await response.json();
    if(!response.ok)throw new Error(message(value.error));
    return value;
  }
  function sync(){
    const busy=active.has(job.state);
    byId('analysis-start').disabled=submitting||busy||!plan||Boolean(plan.blockedReason)||!byId('analysis-consent').checked;
    byId('analysis-mode').disabled=submitting||busy;
    byId('analysis-preview-refresh').disabled=submitting||busy;
    byId('analysis-consent').disabled=submitting||busy||!plan||Boolean(plan.blockedReason);
    const status=(stages[job.state]||'상태 확인 중')+(job.state==='analyzing'&&job.chunks?` · ${job.chunk}/${job.chunks} 청크`:'');
    byId('analysis-job-badge').textContent=job.state==='idle'?'':status;
    byId('analysis-job-open').hidden=job.state==='idle';
    if(job.session===session)byId('analysis-progress').textContent=status+(job.error?' · '+message(job.error):'');
  }
  function renderSettings(value){
    const auth={unchecked:'로그인 상태 미확인',ready:'로그인 확인됨',missing:'CLI 설치 필요',login_required:'로그인 필요',unavailable:'상태 확인 실패'};
    byId('analysis-codex-state').textContent=`Codex ${value.codexInstalled?'설치됨':'미설치'} · ${auth[value.codexAuth]||auth.unchecked} · Node.js ${value.nodeInstalled?'설치됨':'미설치'}`+(value.codexCheckedAt?` · 마지막 확인 ${new Date(value.codexCheckedAt*1000).toLocaleString('ko-KR')}`:'');
    byId('analysis-cm-root').value=value.cmRoot;
    byId('analysis-cm-state').textContent=value.conversationMapReady?'Conversation Map 준비됨':'Conversation Map 폴더 설정 필요';
    const sources={windows:'Windows 보안 저장소',environment:'서버 환경변수',none:'미등록'};
    byId('analysis-key-state').textContent=`API 키 ${value.keyRegistered?'등록됨':'미등록'} · ${sources[value.keySource]} · API 유효성 미확인`;
    byId('analysis-save-key').disabled=!value.keyStorageAvailable;
    byId('analysis-api-key').disabled=!value.keyStorageAvailable;
    byId('analysis-delete-key').disabled=value.keySource!=='windows';
  }
  async function openSettings(){
    byId('analysis-api-key').value='';byId('analysis-settings-feedback').textContent='설정을 불러오는 중…';
    settingsDialog.showModal();
    try{renderSettings(await request('settings.json'));byId('analysis-settings-feedback').textContent='';}
    catch(error){byId('analysis-settings-feedback').textContent=error.message;}
  }
  async function edit(path,body){
    if(settingsBusy)return;settingsBusy=true;
    byId('analysis-settings-feedback').textContent='처리 중…';
    try{renderSettings(await request(path,body));byId('analysis-settings-feedback').textContent=path==='check-codex.json'?'로컬 인증 상태 확인을 마쳤습니다. 모델 호출은 없습니다.':'설정을 저장했습니다. 분석은 실행되지 않았습니다.';}
    catch(error){byId('analysis-settings-feedback').textContent=error.message;}
    finally{body=null;byId('analysis-api-key').value='';settingsBusy=false;}
  }
  async function preview(autoMode=false){
    const token=++serial;plan=null;byId('analysis-consent').checked=false;sync();
    byId('analysis-scope').textContent='대상 대화를 확인하는 중…';byId('analysis-blocked').textContent='';
    try{
      let value=await request('preview.json',{session,mode:byId('analysis-mode').value});
      if(token!==serial)return;
      if(autoMode&&value.hasAnalysis&&value.mode!=='decisions'){
        byId('analysis-mode').value='decisions';return preview();
      }
      plan=value;
      byId('analysis-scope').textContent=`선택한 세션 1개 · 원문 메시지 ${value.messageCount.toLocaleString()}개${value.mode==='decisions'?'':` · 분석 입력 약 ${value.characterCount.toLocaleString()}자`} · ${value.hasAnalysis?'기존 분석 있음':'미분석'}`;
      byId('analysis-transfer').textContent=value.mode==='decisions'?'전송 범위: 기존 주제 제목·설명과 분석 항목 제목·요약·분류·근거 인용을 Decisions API로 전송합니다.':value.mode==='hybrid'?'전송 범위: 선택한 세션의 사용자·어시스턴트 대화를 Codex에, 생성된 주제·항목·근거 인용을 Decisions API에 전송합니다.':'전송 범위: 선택한 세션의 사용자·어시스턴트 대화를 Codex CLI에 설정된 모델로 전송합니다.';
      byId('analysis-blocked').textContent=value.blockedReason?message(value.blockedReason):'';
    }catch(error){if(token===serial){byId('analysis-scope').textContent='대상 확인 실패';byId('analysis-blocked').textContent=error.message;}}
    finally{if(token===serial)sync();}
  }
  async function openRun(sid,name){
    if(submitting)return;
    session=sid;title=name||sid;byId('analysis-session-title').textContent=title;byId('analysis-session-id').textContent=sid;
    byId('analysis-mode').value=job.session===sid?job.mode||'codex':'codex';byId('analysis-progress').textContent='';
    if(!runDialog.open)runDialog.showModal();
    await preview(true);
  }
  byId('analysis-settings-open').addEventListener('click',openSettings);
  byId('analysis-settings-close').addEventListener('click',()=>settingsDialog.close());
  settingsDialog.addEventListener('close',()=>{byId('analysis-api-key').value='';if(runDialog.open)preview();});
  settingsDialog.addEventListener('cancel',()=>{byId('analysis-api-key').value='';});
  byId('analysis-check-codex').addEventListener('click',()=>edit('check-codex.json',{}));
  byId('analysis-save-path').addEventListener('click',()=>edit('settings.json',{action:'save_path',cmRoot:byId('analysis-cm-root').value}));
  byId('analysis-save-key').addEventListener('click',()=>{const key=byId('analysis-api-key').value;byId('analysis-api-key').value='';edit('settings.json',{action:'save_key',key});});
  byId('analysis-delete-key').addEventListener('click',()=>edit('settings.json',{action:'delete_key'}));
  byId('analysis-open-setup').addEventListener('click',openSettings);
  byId('analysis-run-close').addEventListener('click',()=>runDialog.close());
  byId('analysis-mode').addEventListener('change',()=>preview());
  byId('analysis-preview-refresh').addEventListener('click',()=>preview());
  byId('analysis-consent').addEventListener('change',sync);
  byId('analysis-job-open').addEventListener('click',()=>{if(job.session)openRun(job.session,job.session===session?title:'선택한 분석 작업');});
  byId('analysis-start').addEventListener('click',async()=>{
    if(byId('analysis-start').disabled)return;
    submitting=true;sync();
    try{job=await request('start.json',{session:plan.session,mode:plan.mode,revision:plan.revision,allowModel:plan.usesModel,allowApi:plan.usesApi});byId('analysis-consent').checked=false;}
    catch(error){byId('analysis-blocked').textContent=error.message;plan=null;}
    finally{submitting=false;sync();}
  });
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==frame.contentWindow||event.data?.type!=='context-graph-analyze')return;
    if(typeof event.data.session!=='string'||!/^[A-Za-z0-9-]{1,128}$/.test(event.data.session))return;
    openRun(event.data.session,typeof event.data.title==='string'?event.data.title.slice(0,500):'');
  });
  async function poll(){
    try{job=await request('job.json');sync();}catch(_){byId('analysis-job-badge').textContent='분석 상태 연결 확인 필요';}
    finally{setTimeout(poll,2000);}
  }
  poll();
})();
