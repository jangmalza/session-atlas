/* Original-message chronology, never a stored topic or answers relationship. */
const RawTurns = (()=>{
  function create({document,fetch,live,openSource,redraw,remember,locate}) {
    const cache=new Map();let session=null,selection="",pendingMessage="",generation=0,needsLocate=false;
    function saved(){try{const value=JSON.parse(sessionStorage.getItem('session-atlas-raw-turn')||'null');if(value?.session===session?.id)selection=value.message||"";}catch(_){}}
    function store(){try{sessionStorage.setItem('session-atlas-raw-turn',JSON.stringify({session:session.id,message:selection}));}catch(_){}remember?.();}
    const turnsOf=()=>cache.get(session?.id)?.turns||[];
    function select(turn){const turns=turnsOf();selection=turn.request.messageId;locate(turns.indexOf(turn),turns.length);store();redraw();openSource(session.id,selection);}
    function clear(){selection="";store();redraw();}
    // Returns the chronology view for a session without analysis, or null for any other session.
    function view(current){
      if(!current || current.hasAnalysis!==false){session=null;generation++;return null;}
      if(session?.id!==current.id){session=current;selection="";saved();needsLocate=!!selection;}else session=current;
      const entry=cache.get(session.id);
      if(!live)return {turns:[],chosen:null,index:-1,loading:false,failed:false,offline:true,select,clear};
      if(!entry || entry.catalogRevision!==current.revision){
        if(!entry?.loading){const id=session.id,request=++generation;cache.set(id,{...entry,loading:true,catalogRevision:current.revision});
          fetch('timeline/turns.json?'+new URLSearchParams({session:id}),{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('Unavailable');return r.json();}).then(value=>{
            if(value.session?.id!==id || value.kind!=='chronological' || !Array.isArray(value.turns))throw new Error('Invalid chronology');
            cache.set(id,{...value,catalogRevision:current.revision});if(request===generation && session?.id===id)redraw();
          }).catch(()=>{cache.set(id,{catalogRevision:current.revision,failed:true});if(session?.id===id)redraw();});}
      }
      const data=cache.get(session.id),turns=data?.turns||[];
      if(pendingMessage && data?.turns){const found=turns.find(t=>t.request.messageId===pendingMessage || t.responses.some(r=>r.messageId===pendingMessage));if(found){selection=found.request.messageId;needsLocate=true;}pendingMessage="";}
      const chosen=turns.find(t=>t.request.messageId===selection)||null,index=turns.indexOf(chosen);
      if(needsLocate && chosen){locate(index,turns.length);needsLocate=false;store();}
      return {turns,chosen,index,loading:!!data?.loading && !data?.turns,failed:!!data?.failed,offline:false,select,clear};
    }
    return {view,move(direction){const turns=turnsOf(),index=turns.findIndex(t=>t.request.messageId===selection),next=turns[index+direction];if(next)select(next);},
      target(message){pendingMessage=message||"";},active:()=>!!session};
  }
  return {create};
})();
