"use strict";

const page=document.body.dataset.page;
const $=id=>document.getElementById(id);
const e=(tag,cls,value)=>{const x=document.createElement(tag);if(cls)x.className=cls;if(value!==undefined)x.textContent=String(value);return x;};
async function api(path,body){const response=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||`요청 오류 ${response.status}`);return data;}
function empty(text){return e("p","muted",text);}
function link(href,text,cls="secondary link-button"){const a=e("a",cls,text);a.href=href;return a;}
function field(label,control){const wrap=e("label","field",label);wrap.append(control);return wrap;}
function showError(target,error){target.textContent=error.message;target.classList.add("error");if(!matchMedia("(prefers-reduced-motion:reduce)").matches){target.classList.remove("shake");void target.offsetWidth;target.classList.add("shake");}}
function busy(button,value){button.disabled=value;button.classList.toggle("busy",value);button.setAttribute("aria-busy",String(value));}

async function dashboard(){
  const data=await api("/api/dashboard"),tot=data.totals;
  const cards=[["문서",`${tot.documents} PDF · ${data.pages}쪽`],["파싱 후보",tot.candidates],["선택 대기",tot.candidates-tot.saved],["저장 항목",tot.saved],["TBD/TBR 후보",data.tbd_tbr],["AI 의견",tot.ai_reviews]];
  $("dashboardStats").replaceChildren(...cards.map(([label,value])=>{const card=e("article","panel overview-stat");card.append(e("strong","",value),e("span","",label));return card;}));
  const rows=[["문서 준비",`${tot.documents}개`],["후보 수정",`${tot.modified}건`],["저장 선택",`${tot.selected}건`],["검색·추적 대상",`${tot.saved}건`],["담당자 검토",`${tot.reviews}건`],["AI 모델",`${data.provider} · ${data.model}`]];
  $("dashboardProgress").replaceChildren(...rows.map(([label,value])=>{const row=e("div","progress-row");row.append(e("span","",label),e("strong","",value));return row;}));
  const recent=$("dashboardRecent");
  if(!data.recent_reviews.length){recent.replaceChildren(empty("저장된 담당자 검토가 없습니다. 문서함에서 후보를 선택 저장한 뒤 검토를 시작하세요."));return;}
  recent.replaceChildren(...data.recent_reviews.map(x=>{const row=e("div","recent-row");row.append(e("strong","",`${x.item_id} · ${x.decision}`),e("span","",`${x.reviewer} · ${x.created_at}`));return row;}));
}

async function qa(){
  $("questionForm").addEventListener("submit",async event=>{
    event.preventDefault();const button=$("askButton");busy(button,true);
    const status=$("answerStatus");status.classList.remove("error");status.textContent="전체 문서 검색과 AI 답변을 진행 중입니다.";
    try{
      const result=await api("/api/ask",{question:$("questionInput").value});
      status.textContent=`${result.model} · ${result.status==="answered"?"답변 초안":result.status==="source_only"?"AI 연결 불가 · 검색 근거 표시":"근거 부족"} · 인용 ${result.citations.length}건`;
      $("answerText").textContent=result.answer;
      const list=$("answerEvidence");
      if(!result.evidence.length){list.replaceChildren(empty("관련 문서·규격 항목을 찾지 못했습니다."));return;}
      list.replaceChildren(...result.evidence.map(x=>{
        const card=e("article","evidence-card");
        card.append(e("strong","",`${x.item_id} · ${x.title}`),e("p","",x.statement),
          e("small","",`${x.document_title||x.document_id} · ${x.document_id} · Rev ${x.revision} · ${x.section||"절 정보 없음"} · ${x.page}쪽 · ${x.source_state}`),
          link(`/api/pdf?document=${encodeURIComponent(x.document_id)}#page=${x.page}`,"원문 페이지 보기"));
        return card;
      }));
    }catch(error){showError(status,error);}finally{busy(button,false);}
  });
}

const traceState={entries:[],visible:[]};
function childIds(id){return [...new Set(traceState.entries.filter(x=>(x.item.parents||[]).includes(id)).map(x=>x.item.id))];}
function traceMatches(x){
  const query=$("traceQuery").value.trim().toLowerCase(),item=x.item;
  if(query&&!`${x.document} ${item.id} ${item.title} ${item.statement||item.result||""}`.toLowerCase().includes(query))return false;
  const method=$("traceMethod").value;if(method&&!(item.methods||[]).includes(method))return false;
  const relation=$("traceRelation").value;
  if(relation==="no-parent"&&(item.parents||[]).length)return false;
  if(relation==="no-child"&&childIds(item.id).length)return false;
  if(relation==="multi-parent"&&(item.parents||[]).length<2)return false;
  return true;
}
function renderTrace(){
  traceState.visible=traceState.entries.filter(traceMatches);
  $("traceCount").textContent=`표시 ${traceState.visible.length}건 · 저장된 전체 ${traceState.entries.length}건`;
  const body=$("traceTable");
  if(!traceState.visible.length){const row=e("tr"),cell=e("td","empty",traceState.entries.length?"검색 조건에 맞는 항목이 없습니다.":"저장된 항목이 없습니다. 문서함에서 후보를 선택 저장하세요.");cell.colSpan=6;row.append(cell);body.replaceChildren(row);return;}
  body.replaceChildren(...traceState.visible.map(x=>{
    const item=x.item,row=e("tr","clickable");row.tabIndex=0;
    row.append(e("td","",`${item.id} · ${item.title}`),e("td","",x.document),
      e("td","",(item.parents||[]).join(", ")||"—"),e("td","",childIds(item.id).join(", ")||"—"),
      e("td","",(item.methods||[]).join(", ")||"—"),e("td","",item.status||item.result||"—"));
    row.addEventListener("click",()=>openTrace(x));row.addEventListener("keydown",event=>{if(event.key==="Enter")openTrace(x);});return row;
  }));
}
function traceWidth(value){const max=Math.floor(innerWidth*.95),min=Math.min(560,max),width=Math.max(min,Math.min(max,Math.round(value)));$("traceDetail").style.width=`${width}px`;$("traceWidthLabel").textContent=`${width}px`;$("traceResize").setAttribute("aria-valuenow",String(width));localStorage.setItem("gkx_detail_width",String(width));}
function closeTrace(){ $("traceDetail").hidden=true;$("traceBackdrop").hidden=true;document.body.style.overflow=""; }
function openTrace(x){
  const item=x.item,body=e("div");
  body.append(e("p","muted",`${x.document} · Rev ${x.revision} · ${item.section||"절 정보 없음"} · ${item.page}쪽`),
    e("h3","","요구사항 / 결과 본문"),e("p","",item.statement||item.result||"본문 없음"));
  const relation=e("div","relation-grid");
  const parents=(item.parents||[]),children=childIds(item.id);
  for(const [label,ids] of [["상위 요구 ID",parents],["하위 요구 ID",children]]){
    const card=e("div","relation-card");card.append(e("small","",label),e("strong","",ids.join(", ")||"현재 저장 자료에서 미확인"));relation.append(card);
  }
  body.append(relation);
  body.append(e("p","muted","연결 대상이 현재 저장 자료에서 보이지 않아도 실제 문서 전체에 없다고 단정하지 않습니다."));
  const sameVcrm=traceState.entries.find(y=>y.document==="GKX-VCRM-001"&&y.item.id===item.id);
  if(sameVcrm&&sameVcrm!==x){body.append(e("h3","","VCRM 연결"),e("p","",sameVcrm.item.result||"결과 없음"));}
  const actions=e("div","detail-actions"),pdf=link(`/api/pdf?document=${encodeURIComponent(x.document)}#page=${item.page}`,"원문 페이지 열기"),ai=e("button","primary","AI 의견 생성");
  pdf.target="_blank";pdf.rel="noopener";actions.append(pdf,ai);body.append(actions);
  const result=e("p","muted","AI 의견은 담당자 판정 전 초안으로 저장됩니다.");body.append(result);
  ai.addEventListener("click",async()=>{busy(ai,true);try{const response=await api("/api/ai-review",{document:x.document,item:item.id});result.textContent=`${response.model} · ${response.opinion.status}: ${response.opinion.summary||"의견 생성 완료"}`;}catch(error){showError(result,error);}finally{busy(ai,false);}});
  $("traceDetailTitle").textContent=`${item.id} · ${item.title}`;$("traceDetailBody").replaceChildren(body);
  $("traceBackdrop").hidden=false;$("traceDetail").hidden=false;document.body.style.overflow="hidden";
  traceWidth(Number(localStorage.getItem("gkx_detail_width"))||Math.min(980,innerWidth*.82));$("traceClose").focus();
}
async function trace(){
  traceState.entries=await api("/api/trace?query=");renderTrace();
  for(const id of ["traceQuery","traceMethod","traceRelation"])$(id).addEventListener(id==="traceQuery"?"input":"change",renderTrace);
  $("traceClose").addEventListener("click",closeTrace);$("traceBackdrop").addEventListener("click",closeTrace);
  document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!$("traceDetail").hidden)closeTrace();});
  $("traceNarrow").addEventListener("click",()=>traceWidth($("traceDetail").getBoundingClientRect().width-100));
  $("traceWide").addEventListener("click",()=>traceWidth($("traceDetail").getBoundingClientRect().width+100));
  $("traceDefault").addEventListener("click",()=>traceWidth(Math.min(980,innerWidth*.82)));
  $("traceResize").addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();const width=$("traceDetail").getBoundingClientRect().width;traceWidth(event.key==="Home"?560:event.key==="End"?innerWidth*.95:width+(event.key==="ArrowLeft"?30:-30));});
  $("traceResize").addEventListener("pointerdown",event=>{event.preventDefault();const handle=$("traceResize");handle.setPointerCapture(event.pointerId);const move=x=>traceWidth(innerWidth-x.clientX);const end=()=>{handle.removeEventListener("pointermove",move);handle.removeEventListener("pointerup",end);};handle.addEventListener("pointermove",move);handle.addEventListener("pointerup",end);});
}

const aiState={queue:[]};
function aiDetail(record){
  const root=$("aiDetail"),op=record.opinion;
  const content=e("div");content.append(e("h3","",`${record.item_id} · ${record.title}`),
    e("p","muted",`${record.document} · ${record.model} · ${record.created_at}`),
    e("p","",op.summary||"요약 없음"));
  const facts=e("div","relation-grid");
  for(const [label,value] of [["유형",op.review_type],["AI 상태",op.status],["담당자 최근 판단",record.latest_decision||"대기"]]){const box=e("div","relation-card");box.append(e("small","",label),e("strong","",value));facts.append(box);}content.append(facts);
  if(op.evidence?.length){content.append(e("h3","","AI가 표시한 근거"));for(const source of op.evidence)content.append(e("p","",`${source.document_id} · Rev ${source.revision} · ${source.page}쪽 · ${source.quoted_id_or_fact||""}`));}
  if(op.questions_for_reviewer?.length){content.append(e("h3","","담당자 확인 질문"));const ul=e("ul");for(const q of op.questions_for_reviewer)ul.append(e("li","",q));content.append(ul);}
  const form=e("form","review-form-page");
  const name=e("input");name.required=true;name.placeholder="담당자 이름";
  const decision=e("select");for(const label of ["확인필요","채택","수정","기각"]){const option=e("option","",label);option.value=label;decision.append(option);}
  const comment=e("textarea");comment.required=true;comment.placeholder="원문 대조와 판단 근거를 기록하세요.";
  const button=e("button","primary","담당자 검토 저장");const message=e("p","muted","");
  form.append(field("담당자",name),field("판정",decision),field("검토 의견",comment),button,message);
  form.addEventListener("submit",async event=>{event.preventDefault();busy(button,true);try{await api("/api/review",{document:record.document,item:record.item_id,reviewer:name.value,decision:decision.value,comment:comment.value});message.textContent="담당자 검토를 저장했습니다.";await loadAiQueue(record.review_id);}catch(error){showError(message,error);}finally{busy(button,false);}});
  content.append(e("h3","","담당자 판정"),form);root.replaceChildren(content);
}
async function loadAiQueue(selectId){
  aiState.queue=await api("/api/ai-queue");const list=$("aiQueue");
  if(!aiState.queue.length){list.replaceChildren(empty("생성된 AI 의견이 없습니다. 문서함에서 항목을 저장한 뒤 상세 화면에서 AI 의견을 생성하세요."),link("/","문서함으로 이동"));return;}
  list.replaceChildren(...aiState.queue.map(x=>{const button=e("button","queue-item");button.type="button";button.append(e("strong","",`${x.item_id} · ${x.title}`),e("span","",`${x.opinion.status} · 담당자 ${x.latest_decision||"대기"}`));button.addEventListener("click",()=>aiDetail(x));return button;}));
  aiDetail(aiState.queue.find(x=>x.review_id===selectId)||aiState.queue[0]);
}

const recordState={rows:[],visible:[]};
function renderRecords(){
  const query=$("recordQuery").value.trim().toLowerCase(),decision=$("recordDecision").value;
  recordState.visible=recordState.rows.filter(x=>(!decision||x.decision===decision)&&(!query||`${x.item_id} ${x.doc_no} ${x.title} ${x.comment} ${x.reviewer}`.toLowerCase().includes(query)));
  $("recordCount").textContent=`표시 ${recordState.visible.length}건 · 전체 ${recordState.rows.length}건`;
  const body=$("recordTable");if(!recordState.visible.length){const row=e("tr"),cell=e("td","empty","조건에 맞는 검토 기록이 없습니다.");cell.colSpan=6;row.append(cell);body.replaceChildren(row);return;}
  body.replaceChildren(...recordState.visible.map(x=>{const row=e("tr");row.append(e("td","",x.review_id),e("td","",`${x.item_id} · ${x.title}\n${x.doc_no} p.${x.page}`),e("td","",x.decision),e("td","",x.comment),e("td","",x.reviewer),e("td","",x.created_at));return row;}));
}
function exportMd(){
  const clean=value=>String(value??"").replace(/\|/g,"\\|").replace(/\r?\n/g,"<br>");
  const lines=["# GEO-KSAT-X 담당자 검토 기록","","| 기록 ID | 문서·항목 | 판정 | 담당자 | 시각 | 의견 |","|---|---|---|---|---|---|"];
  for(const x of recordState.visible)lines.push(`| ${x.review_id} | ${clean(x.doc_no)} · ${clean(x.item_id)} p.${x.page} | ${clean(x.decision)} | ${clean(x.reviewer)} | ${clean(x.created_at)} | ${clean(x.comment)} |`);
  const blob=new Blob([lines.join("\n")+"\n"],{type:"text/markdown;charset=utf-8"});const url=URL.createObjectURL(blob),a=e("a");a.href=url;a.download="GEO-KSAT-X_검토기록.md";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function records(){recordState.rows=await api("/api/records");renderRecords();$("recordQuery").addEventListener("input",renderRecords);$("recordDecision").addEventListener("change",renderRecords);$("exportMd").addEventListener("click",exportMd);$("printRecords").addEventListener("click",()=>print());}

const start={dashboard,qa,trace,ai:loadAiQueue,reviews:records}[page];
if(start)start().catch(error=>{const main=document.querySelector("main");main.append(e("p","notice error",error.message));});
