"use strict";

const state = {documents: [], candidates: [], trace: [], current: null, view: "candidates", detail: null};
const $ = (id) => document.getElementById(id);
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const fieldNames = {id:"항목 ID",type:"항목 유형",title:"제목",statement:"요구문",rationale:"근거",parents:"상위 ID (쉼표 구분)",methods:"검증 방법 (쉼표 구분)",priority:"우선순위",status:"상태",section:"절",page:"페이지",source_doc:"출처 문서",verification_item:"검증 항목",test_case:"시험 사례",result:"검증 결과"};

function el(tag, cls, content) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (content !== undefined) node.textContent = String(content);
  return node;
}

async function api(path, body) {
  const options = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `요청 오류 ${response.status}`);
  return data;
}

function notice(message, error=false) {
  const target = $("notice");
  target.textContent = message;
  target.classList.toggle("error", error);
  if (error && !reduceMotion) {
    target.classList.remove("shake");
    void target.offsetWidth;
    target.classList.add("shake");
  }
}

async function perform(task) {
  try { await task(); }
  catch (error) { notice(error.message, true); }
}

async function refreshSummary() {
  const data = await api("/api/summary");
  const labels = [["documents","문서"],["candidates","후보"],["selected","선택"],["saved","저장"],["reviews","검토"]];
  $("stats").replaceChildren(...labels.map(([key,label]) => {
    const box = el("div","stat"); box.append(el("strong","",data[key]),el("span","",label)); return box;
  }));
}

async function refreshDocuments() {
  state.documents = await api("/api/documents");
  $("documentCount").textContent = `${state.documents.length}개`;
  const current=state.documents.find(x=>x.document===state.current);
  if(current)$("currentMeta").textContent=`Rev ${current.revision} · ${current.pages}쪽 · 후보 ${current.candidates}건 · 저장 ${current.saved}건`;
  renderDocuments();
}

function renderDocuments() {
  const query = $("docSearch").value.trim().toLowerCase();
  const fragment = document.createDocumentFragment();
  for (const doc of state.documents.filter(x => `${x.document} ${x.title}`.toLowerCase().includes(query))) {
    const button = el("button","document" + (doc.document === state.current ? " active" : ""));
    button.type = "button";
    const count = el("span","count",`${doc.saved}/${doc.candidates} 저장`);
    button.append(count,el("strong","",doc.document),el("small","",`${doc.title} · Rev ${doc.revision}`));
    button.addEventListener("click",()=>perform(()=>openDocument(doc.document)));
    fragment.append(button);
  }
  $("documents").replaceChildren(fragment);
}

async function openDocument(docNo) {
  if (state.current !== docNo) $("traceSearch").value = "";
  state.current = docNo;
  const doc = state.documents.find(x => x.document === docNo);
  $("currentDocType").textContent = doc.source;
  $("currentDoc").textContent = `${doc.document} · ${doc.title}`;
  $("currentMeta").textContent = `Rev ${doc.revision} · ${doc.pages}쪽 · 후보 ${doc.candidates}건 · 저장 ${doc.saved}건`;
  $("pdfButton").disabled = false;
  $("extractButton").disabled = !!state.cloudDemo;
  $("saveButton").disabled = false;
  $("selectAll").disabled = false;
  $("clearAll").disabled = false;
  renderDocuments();
  await refreshCandidates();
  if (state.view === "trace") await refreshTrace();
  notice(`${doc.document}의 후보를 불러왔습니다.`);
}

async function refreshCandidates() {
  if (!state.current) return;
  state.candidates = await api(`/api/candidates?document=${encodeURIComponent(state.current)}`);
  const selected = state.candidates.filter(x => x.selected).length;
  const modified = state.candidates.filter(x => x.modified).length;
  const invalid = state.candidates.filter(x => x.errors.length).length;
  $("candidateSummary").textContent = `후보 ${state.candidates.length}건 · 선택 ${selected}건 · 수정 ${modified}건 · 오류 ${invalid}건`;
  const fragment = document.createDocumentFragment();
  for (const entry of state.candidates) {
    const item = entry.item;
    const row = el("tr","clickable"); row.tabIndex = 0;
    const selectCell = el("td");
    const check = el("input","candidate-check"); check.type="checkbox"; check.checked=entry.selected;
    check.disabled = !!entry.errors.length && !entry.selected;
    check.setAttribute("aria-label",`${item.id} 저장 선택`);
    check.addEventListener("click",event=>event.stopPropagation());
    check.addEventListener("change",()=>perform(async()=>{
      await api("/api/select",{document:state.current,item:entry.original_id,selected:check.checked});
      await refreshCandidates(); await refreshSummary(); await refreshDocuments();
    }));
    const visual=el("label","check-visual");
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("aria-hidden","true");
    const mark=document.createElementNS("http://www.w3.org/2000/svg","polyline");
    mark.setAttribute("points","4 12 9 17 20 6");svg.append(mark);visual.append(check,svg);selectCell.append(visual);
    const titleCell = el("td"); titleCell.append(el("div","row-title",item.title || "제목 없음"),
      el("div","excerpt",item.statement || item.result || "본문 없음"));
    const statusCell=el("td");
    statusCell.append(el("span","pill " + (entry.errors.length ? "warning" : entry.selected ? "success" : ""),
      entry.errors.length ? "오류 확인" : entry.modified ? "수정됨" : entry.selected ? "선택됨" : "검토 대기"));
    row.append(selectCell,el("td","",item.id),el("td","",item.type),titleCell,el("td","",item.page),statusCell);
    row.addEventListener("click",()=>showCandidate(entry));
    row.addEventListener("keydown",event=>{if(event.key==="Enter")showCandidate(entry);});
    fragment.append(row);
  }
  if (!state.candidates.length) {
    const row=el("tr"); const cell=el("td","empty","후보가 없습니다.");cell.colSpan=6;row.append(cell);fragment.append(row);
  }
  $("candidateRows").replaceChildren(fragment);
}

async function refreshTrace() {
  if (!state.current) return;
  const docNo = state.current;
  const query = $("traceSearch").value.trim();
  const entries = await api(`/api/trace?document=${encodeURIComponent(docNo)}&query=${encodeURIComponent(query)}`);
  if (state.current !== docNo) return;
  state.trace = entries;
  const fragment=document.createDocumentFragment();
  for (const entry of state.trace) {
    const row=el("tr","clickable");row.tabIndex=0;
    const title=el("td");title.append(el("div","row-title",entry.item.title),el("div","excerpt",entry.item.statement || entry.item.result || ""));
    row.append(el("td","",entry.item.id),el("td","",entry.document),el("td","",entry.item.type),title,el("td","",entry.item.page));
    row.addEventListener("click",()=>perform(()=>showTrace(entry)));
    row.addEventListener("keydown",event=>{if(event.key==="Enter")perform(()=>showTrace(entry));});
    fragment.append(row);
  }
  if (!state.trace.length) {const row=el("tr"),cell=el("td","empty","이 문서에 저장된 항목이 없습니다.");cell.colSpan=5;row.append(cell);fragment.append(row);}
  $("traceRows").replaceChildren(fragment);
}

function setView(view) {
  state.view=view;
  $("candidateView").hidden=view!=="candidates";
  $("traceView").hidden=view!=="trace";
  $("candidatesTab").classList.toggle("active",view==="candidates");
  $("traceTab").classList.toggle("active",view==="trace");
  $("candidatesTab").setAttribute("aria-current",view==="candidates"?"page":"false");
  $("traceTab").setAttribute("aria-current",view==="trace"?"page":"false");
  if (view==="trace") perform(refreshTrace);
}

function setWidth(px) {
  const max=Math.floor(innerWidth*.95), min=Math.min(560,max);
  const width=Math.max(min,Math.min(max,Math.round(px)));
  $("detail").style.width=`${width}px`;
  $("widthLabel").textContent=`${width}px`;
  $("resizeHandle").setAttribute("aria-valuenow",String(width));
  $("resizeHandle").setAttribute("aria-valuemin",String(min));
  $("resizeHandle").setAttribute("aria-valuemax",String(max));
  localStorage.setItem("gkx_detail_width",String(width));
}

function openDetail(kind,title,content) {
  state.detail={kind,title};
  $("detailKind").textContent=kind;
  $("detailTitle").textContent=title;
  $("detailBody").replaceChildren(content);
  $("backdrop").hidden=false;
  $("detail").hidden=false;
  document.body.style.overflow="hidden";
  const saved=Number(localStorage.getItem("gkx_detail_width"));
  setWidth(saved || Math.min(980,innerWidth*.82));
  $("closeDetail").focus();
}

function closeDetail() {
  $("backdrop").hidden=true;$("detail").hidden=true;
  document.body.style.overflow="";state.detail=null;
}

function showCandidate(entry) {
  const body=el("div");
  const item=entry.item;
  body.append(el("p","muted",`${entry.document} · ${item.section || "절 정보 없음"} · ${item.page}쪽`));
  if(entry.errors.length){const list=el("ul","error-list");entry.errors.forEach(e=>list.append(el("li","",e)));body.append(list);}
  const form=el("form","field-grid");
  const fields=Object.keys(item).filter(key=>fieldNames[key]);
  for(const key of fields){
    const label=el("label","field" + (["statement","rationale","result"].includes(key)?" wide":""),fieldNames[key]);
    const raw=Array.isArray(item[key])?item[key].join(", "):item[key]??"";
    const input=["statement","rationale","result"].includes(key)?el("textarea"):el("input");
    input.name=key;input.value=String(raw);label.append(input);form.append(label);
  }
  const actions=el("div","detail-actions");
  const apply=el("button","primary","수정값 반영");apply.type="submit";
  const revert=el("button","secondary","원본으로 되돌리기");revert.type="button";
  const toggle=el("button","secondary",entry.selected?"저장 선택 해제":"저장 선택");toggle.type="button";
  actions.append(apply,revert,toggle);
  form.addEventListener("submit",event=>{event.preventDefault();perform(async()=>{
    const updates=fields.filter(key=>String(form.elements[key].value)!==String(Array.isArray(item[key])?item[key].join(", "):item[key]??""));
    for(const key of updates)await api("/api/edit",{document:entry.document,item:entry.original_id,field:key,value:form.elements[key].value});
    await refreshCandidates();await refreshSummary();
    const updated=state.candidates.find(x=>x.original_id===entry.original_id);
    showCandidate(updated);notice(updates.length?"수정값을 반영했습니다. 저장 선택 상태는 유지됩니다.":"변경 사항이 없습니다.");
  });});
  revert.addEventListener("click",()=>perform(async()=>{
    await api("/api/revert",{document:entry.document,item:entry.original_id});
    await refreshCandidates();await refreshSummary();
    showCandidate(state.candidates.find(x=>x.original_id===entry.original_id));notice("파싱 원본으로 되돌렸습니다.");
  }));
  toggle.addEventListener("click",()=>perform(async()=>{
    await api("/api/select",{document:entry.document,item:entry.original_id,selected:!entry.selected});
    await refreshCandidates();await refreshSummary();await refreshDocuments();
    showCandidate(state.candidates.find(x=>x.original_id===entry.original_id));
  }));
  form.append(actions);
  body.append(el("h3","","파싱 후보 수정"),form);
  const compare=el("div","compare");
  const original=el("div"),current=el("div");
  original.append(el("h3","","파싱 원본"),el("pre","",JSON.stringify(entry.original || {},null,2)));
  current.append(el("h3","","현재 수정값"),el("pre","",JSON.stringify(item,null,2)));
  compare.append(original,current);
  body.append(compare);
  const pdf=el("a","secondary","원문 PDF에서 페이지 열기");
  pdf.href=`/api/pdf?document=${encodeURIComponent(entry.document)}#page=${item.page}`;pdf.target="_blank";pdf.rel="noopener";
  body.append(pdf);
  openDetail("파싱 후보",`${item.id} · ${item.title}`,body);
}

async function showTrace(entry) {
  const body=el("div");const item=entry.item;
  body.append(el("p","muted",`${entry.document} · Rev ${entry.revision} · ${item.section || "절 정보 없음"} · ${item.page}쪽`),
    el("h3","","전체 내용"),el("p","",item.statement || item.result || ""));
  const refs=el("p","",`상위 ID: ${(item.parents||[]).join(", ") || "없음"} · 검증방법: ${(item.methods||[]).join(", ") || "없음"}`);body.append(refs);
  const pdf=el("a","secondary","원문 PDF 보기");pdf.href=`/api/pdf?document=${encodeURIComponent(entry.document)}#page=${item.page}`;pdf.target="_blank";pdf.rel="noopener";body.append(pdf);
  const config=await api("/api/ai-config");
  const aiReviews=await api(`/api/ai-reviews?document=${encodeURIComponent(entry.document)}&item=${encodeURIComponent(item.id)}`);
  body.append(el("h3","","AI 의견 초안"),el("p","muted",`${config.provider} · ${config.model} · 담당자 확인 전 의견`));
  const generate=el("button","secondary", "AI 의견 생성");generate.type="button";
  generate.addEventListener("click",()=>perform(async()=>{
    busy(generate,true);
    try{
      const response=await api("/api/ai-review",{document:entry.document,item:item.id});
      await showTrace(entry);notice(`${response.model}의 의견 초안을 저장했습니다. 담당자 판정을 확인하세요.`);
    }finally{busy(generate,false);}
  }));
  body.append(generate);
  if(!aiReviews.length)body.append(el("p","muted","생성된 AI 의견이 없습니다."));
  for(const record of aiReviews){
    const opinion=record.opinion;
    const box=el("div","ai-opinion");
    box.append(el("p","muted",`${record.created_at} · ${record.model} · ${opinion.status}`),
      el("p","",opinion.summary || "요약 없음"));
    if(opinion.questions_for_reviewer?.length){const list=el("ul");for(const question of opinion.questions_for_reviewer)list.append(el("li","",question));box.append(list);}
    body.append(box);
  }
  const reviews=await api(`/api/reviews?document=${encodeURIComponent(entry.document)}&item=${encodeURIComponent(item.id)}`);
  body.append(el("h3","","담당자 검토기록"));
  if (!reviews.length) body.append(el("p","muted","아직 검토기록이 없습니다."));
  for(const review of reviews)body.append(el("p","",`${review.created_at} · ${review.reviewer} · ${review.decision} — ${review.comment}`));
  const form=el("form","field-grid");
  const reviewer=el("input");reviewer.required=true;reviewer.placeholder="담당자";
  const decision=el("select");for(const value of ["확인필요","채택","수정","기각"]){const opt=el("option","",value);opt.value=value;decision.append(opt);}
  const comment=el("textarea");comment.required=true;comment.placeholder="판정 근거와 확인 내용을 입력하세요.";
  const a=el("label","field","담당자"),b=el("label","field","판정"),c=el("label","field wide","검토 의견");a.append(reviewer);b.append(decision);c.append(comment);
  form.append(a,b,c);const submit=el("button","primary","검토기록 저장");form.append(submit);
  form.addEventListener("submit",event=>{event.preventDefault();perform(async()=>{
    await api("/api/review",{document:entry.document,item:item.id,reviewer:reviewer.value,decision:decision.value,comment:comment.value});
    await refreshSummary();await showTrace(entry);notice("담당자 검토기록을 저장했습니다.");
  });});
  body.append(el("h3","","새 검토기록"),form);
  openDetail("저장 항목",`${item.id} · ${item.title}`,body);
}

function showPdf() {
  if(!state.current)return;
  const frame=el("iframe","pdf-frame");frame.title=`${state.current} 원문 PDF`;frame.src=`/api/pdf?document=${encodeURIComponent(state.current)}`;
  const body=el("div");body.append(frame);
  openDetail("원문 PDF",state.current,body);
}

function busy(button,on) {
  button.disabled=on;
  button.classList.toggle("busy",on);
  button.setAttribute("aria-busy",String(on));
}

async function save() {
  if(!state.current)return;
  const button=$("saveButton");busy(button,true);
  try{
    const result=await api("/api/save",{document:state.current,operation_id:crypto.randomUUID()});
    notice(`선택 ${result.selected_count}건 · 새 저장 ${result.saved_count}건 · 중복 ${result.duplicate_count}건 · 실패 ${result.failed_count}건`);
    await refreshSummary();await refreshDocuments();await refreshTrace();
  }catch(error){notice(error.message,true);}finally{busy(button,false);}
}

$("docSearch").addEventListener("input",renderDocuments);
$("traceSearch").addEventListener("input",()=>perform(refreshTrace));
$("candidatesTab").addEventListener("click",()=>setView("candidates"));
$("traceTab").addEventListener("click",()=>setView("trace"));
$("pdfButton").addEventListener("click",showPdf);
$("extractButton").addEventListener("click",()=>perform(async()=>{
  const button=$("extractButton");busy(button,true);
  try{
    const result=await api("/api/extract",{document:state.current});
    await refreshCandidates();await refreshSummary();await refreshDocuments();
    notice(`PDF에서 ${result.extracted}건 추출 · 새 후보 ${result.added}건 · 기존 수정 ${result.preserved_edits}건 보존`);
  }finally{busy(button,false);}
}));
$("pdfUpload").addEventListener("change",event=>{
  const file=event.target.files?.[0];if(!file)return;
  const progress=$("uploadProgress");
  if(file.size>25_000_000){notice("PDF 크기는 25 MB 이하여야 합니다.",true);event.target.value="";return;}
  const xhr=new XMLHttpRequest();xhr.open("POST","/api/upload");
  xhr.setRequestHeader("Content-Type","application/pdf");xhr.setRequestHeader("X-Filename",file.name);
  progress.textContent="업로드 0%";
  xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.textContent=`업로드 ${Math.round(e.loaded/e.total*100)}%`;};
  xhr.upload.onload=()=>{progress.textContent="PDF 항목 추출 중…";};
  xhr.onload=()=>perform(async()=>{
    const data=JSON.parse(xhr.responseText);
    if(xhr.status>=400)throw new Error(data.error||"업로드에 실패했습니다.");
    await refreshDocuments();await openDocument(data.document);await refreshSummary();
    notice(`${data.document} PDF를 등록하고 ${data.extracted}건을 추출했습니다.`);
    progress.textContent="완료";
  });
  xhr.onerror=()=>{notice("PDF 업로드 연결에 실패했습니다.",true);progress.textContent="실패";};
  xhr.send(file);event.target.value="";
});
$("saveButton").addEventListener("click",save);
$("selectAll").addEventListener("click",()=>perform(async()=>{
  const result=await api("/api/select-bulk",{document:state.current,selected:true});
  await refreshCandidates();await refreshSummary();await refreshDocuments();notice(`정상 후보 ${result.updated}건을 선택했습니다.`);
}));
$("clearAll").addEventListener("click",()=>perform(async()=>{
  await api("/api/select-bulk",{document:state.current,selected:false});
  await refreshCandidates();await refreshSummary();await refreshDocuments();notice("선택을 해제했습니다.");
}));
$("closeDetail").addEventListener("click",closeDetail);
$("backdrop").addEventListener("click",closeDetail);
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!$("detail").hidden)closeDetail();});
$("narrowDetail").addEventListener("click",()=>setWidth($("detail").getBoundingClientRect().width-100));
$("wideDetail").addEventListener("click",()=>setWidth($("detail").getBoundingClientRect().width+100));
$("defaultDetail").addEventListener("click",()=>setWidth(Math.min(980,innerWidth*.82)));
$("resizeHandle").addEventListener("keydown",event=>{
  if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
  event.preventDefault();const width=$("detail").getBoundingClientRect().width;
  setWidth(event.key==="Home"?560:event.key==="End"?innerWidth*.95:width+(event.key==="ArrowLeft"?30:-30));
});
$("resizeHandle").addEventListener("pointerdown",event=>{
  event.preventDefault();const handle=$("resizeHandle");handle.setPointerCapture(event.pointerId);
  const move=e=>setWidth(innerWidth-e.clientX);
  const end=()=>{handle.removeEventListener("pointermove",move);handle.removeEventListener("pointerup",end);};
  handle.addEventListener("pointermove",move);handle.addEventListener("pointerup",end);
});

perform(async()=>{
  const config=await api("/api/ai-config");
  state.cloudDemo=!!config.cloud_demo;
  if(state.cloudDemo){
    document.querySelector(".upload-control")?.remove();
    $("extractButton").style.display="none";
    document.querySelector(".intro .eyebrow").textContent="CLOUD DEMO";
    document.querySelector(".intro p:not(.eyebrow)").textContent="준비된 가상 PDF 37개의 후보를 검토하세요. 선택·수정·검토 기록은 이 브라우저의 시연 세션에 저장됩니다.";
  }
  await refreshSummary();await refreshDocuments();
  const requested=new URLSearchParams(location.search).get("document");
  const chosen=state.documents.find(x=>x.document===requested)||state.documents[0];if(chosen)await openDocument(chosen.document);
});
