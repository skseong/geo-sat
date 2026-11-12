"use strict";
const navLinks=[["/dashboard.html","검토 현황"],["/","문서함"],["/qa.html","규격 질의"],["/trace.html","추적성 대조"],["/ai.html","AI 의견 검토"],["/reviews.html","검토 기록"]];
const nav=document.createElement("nav");nav.className="main-nav";nav.setAttribute("aria-label","주 메뉴");
for(const [href,label] of navLinks){const link=document.createElement("a");link.href=href;link.textContent=label;if(location.pathname===href||(href==="/"&&location.pathname==="/index.html")){link.className="active";link.setAttribute("aria-current","page");}nav.append(link);}
document.querySelector(".topbar").insertBefore(nav,document.querySelector(".environment"));
