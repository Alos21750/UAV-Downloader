"""Dependency-free responsive UI. User content is always rendered as text."""

HTML = '''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UAV Downloader Web</title><link rel="stylesheet" href="/app.css"><script src="/app.js" defer></script></head>
<body><main><header><div><p class="eyebrow">NAS · DOCKER</p><h1>UAV Downloader Web</h1>
<p>安全的單工下載佇列 / Your private download queue</p></div><button id="language">English</button></header>
<section id="auth"><h2 data-zh="連線至下載佇列" data-en="Connect to your queue">連線至下載佇列</h2>
<p data-zh="輸入存取權杖。僅保留在此分頁記憶體，重新整理後需重填。" data-en="Enter your access token. It stays in this tab’s memory only; reload to forget it.">輸入存取權杖。僅保留在此分頁記憶體，重新整理後需重填。</p>
<label for="token">Access token</label><div class="row"><input id="token" type="password" autocomplete="off" spellcheck="false"><button id="connect" class="primary" data-zh="連線" data-en="Connect">連線</button></div></section>
<div id="workspace" hidden><section><div class="section-head"><h2 data-zh="新增下載" data-en="Add downloads">新增下載</h2><button id="disconnect" data-zh="登出" data-en="Disconnect">登出</button></div>
<label for="urls" data-zh="影片網址（每行一個，最多 100 個）" data-en="Video URLs (one per line, up to 100)">影片網址（每行一個，最多 100 個）</label>
<textarea id="urls" rows="4" placeholder="https://jable.tv/videos/…"></textarea>
<div class="settings"><label><span data-zh="畫質" data-en="Quality">畫質</span><select id="resolution"><option value="highest">Highest</option><option>1080</option><option>720</option><option>480</option><option>360</option><option value="lowest">Lowest</option></select></label>
<label><span data-zh="片段執行緒" data-en="Segment workers">片段執行緒</span><input id="workers" type="number" min="1" max="16" value="4"></label>
<label><span data-zh="限速 MiB/s（0 不限）" data-en="Limit MiB/s (0 unlimited)">限速 MiB/s（0 不限）</span><input id="limit" type="number" min="0" max="1024" step="0.1" value="0"></label></div>
<button id="add" class="primary" data-zh="加入佇列" data-en="Add to queue">加入佇列</button>
<p class="hint" data-zh="下載至管理者掛載的 downloads 資料夾。同時只下載一部影片；不含 Watcher、搜尋或 AI 字幕。" data-en="Files go to the administrator’s mounted downloads folder. One active video; no Watcher, search or AI subtitles.">下載至管理者掛載的 downloads 資料夾。同時只下載一部影片；不含 Watcher、搜尋或 AI 字幕。</p></section>
<section><div class="section-head"><h2 data-zh="下載佇列" data-en="Download queue">下載佇列</h2><button id="clear" data-zh="清除已結束紀錄" data-en="Clear finished">清除已結束紀錄</button></div>
<div id="halted" hidden><p data-zh="儲存裝置錯誤：佇列已停止。確認磁碟健康與空間後再繼續。" data-en="Storage error: queue halted. Check disk health and free space before continuing.">儲存裝置錯誤：佇列已停止。確認磁碟健康與空間後再繼續。</p><button id="resume" data-zh="已檢查磁碟，繼續佇列" data-en="Disk checked — resume queue">已檢查磁碟，繼續佇列</button></div>
<div id="jobs"></div></section></div><p id="notice" role="status" aria-live="polite"></p>
<footer>Private LAN / VPN only · Token required · UAV Downloader by ALOS</footer></main></body></html>'''

CSS = '''*{box-sizing:border-box}body{margin:0;background:#f1f3f5;color:#18222d;font:15px system-ui,-apple-system,sans-serif}main{max-width:1060px;margin:auto;padding:32px 22px}header,.section-head,.row{display:flex;align-items:center;justify-content:space-between;gap:16px}header{margin-bottom:30px;align-items:flex-start}h1{font-size:30px;letter-spacing:-1px;margin:0 0 8px}h2{font-size:19px;margin:0 0 18px}p{line-height:1.6}.eyebrow{color:#226563;font-size:11px;letter-spacing:2px;font-weight:800;margin:0 0 9px}header p{margin-bottom:0;color:#526373}section{background:white;border:1px solid #dce2e8;border-radius:14px;padding:24px;margin-bottom:20px;box-shadow:0 3px 16px #152c4110}input,select,textarea{font:inherit;padding:11px;border:1px solid #c1cbd5;border-radius:7px;background:white;color:inherit;width:100%;margin-top:7px}textarea{resize:vertical;min-height:104px}input:focus,select:focus,textarea:focus{outline:2px solid #27807a;outline-offset:2px}button{font:inherit;font-weight:650;border:1px solid #c1cbd5;border-radius:7px;background:white;color:#253d50;padding:10px 15px;cursor:pointer;white-space:nowrap}button:hover{background:#eff5f5}button:disabled{opacity:.55;cursor:wait}.primary{background:#226563;color:white;border-color:#226563}.primary:hover{background:#174e4b}.row input{margin:0;min-width:0}.settings{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin:18px 0}.hint,footer{font-size:12px;color:#667683}.job{border-top:1px solid #e2e7ec;padding:20px 0 4px}.job:first-child{border:0}.job-url{overflow-wrap:anywhere;font-size:14px;margin:0 0 10px}.job-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.status{font-size:12px;padding:5px 9px;border-radius:5px;background:#edf1f5}.status.completed{background:#e2f3e9;color:#18623d}.status.failed,.status.interrupted{background:#fff0df;color:#815019}progress{width:100%;height:9px;accent-color:#226563;display:block;margin:16px 0 8px}.meta{font-size:12px;color:#667683}.actions{display:flex;gap:8px;flex-wrap:wrap}.actions button{padding:7px 11px;font-size:12px}details{margin:12px 0;color:#667683;font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px system-ui}#halted{background:#fff1df;border-radius:9px;padding:16px;margin-bottom:16px}#notice{color:#a33c25;min-height:24px}footer{text-align:center;margin:28px 0}[hidden]{display:none!important}@media(max-width:600px){main{padding:22px 14px}h1{font-size:25px}header{gap:8px}header button{padding:8px}section{padding:18px}.settings{grid-template-columns:1fr;gap:14px}.section-head{align-items:flex-start}.section-head button{font-size:12px;padding:8px}.row{gap:8px}.job-head{align-items:flex-start;flex-direction:column}}'''

JS = r'''"use strict";
let token = "", timer = null, language = "zh", busy = false;
const $ = id => document.getElementById(id);
const say = (zh,en) => language === "zh" ? zh : en;
const labels = {pending:["等待中","Pending"],running:["下載中","Running"],cancelling:["正在取消","Cancelling"],completed:["完成","Completed"],failed:["失敗","Failed"],cancelled:["已取消","Cancelled"],interrupted:["中斷，請重試","Interrupted — retry"]};
function notice(message) { $("notice").textContent = message; }
function disconnect() { token=""; $("token").value=""; clearTimeout(timer); $("workspace").hidden=true; $("auth").hidden=false; $("jobs").replaceChildren(); }
async function api(path, body) {
  const response = await fetch(path, {method:body === undefined ? "GET" : "POST", cache:"no-store", credentials:"omit", headers:{"Authorization":"Bearer "+token,"Content-Type":"application/json"}, body:body === undefined ? undefined : JSON.stringify(body)});
  if (response.status === 401) { disconnect(); throw new Error(say("權杖不正確或已失效","Token is invalid or expired")); }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function node(tag, text, className) {const element=document.createElement(tag); if(text !== undefined)element.textContent=text;if(className)element.className=className;return element;}
function render(data) {
  $("halted").hidden=!data.halted;
  const container=$("jobs"), fragment=document.createDocumentFragment();
  if(!data.jobs.length) fragment.append(node("p",say("佇列是空的。加入影片網址即可開始。","Your queue is empty. Add a video URL to begin."),"hint"));
  for(const job of data.jobs) {
    const card=node("article",undefined,"job"), head=node("div",undefined,"job-head");
    const title=node("p",job.url,"job-url"), label=labels[job.status]||[job.status,job.status];
    head.append(title,node("span",label[language==="zh"?0:1],"status "+job.status));card.append(head);
    const progress=node("progress");progress.max=job.total||1;if(job.total)progress.value=Math.min(job.done,job.total);else if(job.status!=="running")progress.value=job.status==="completed"?1:0;card.append(progress);
    const count=job.total ? Math.floor(job.done)+" / "+Math.floor(job.total)+" "+job.unit : say("等待來源或進度","Waiting for source/progress");
    card.append(node("p",count+" · "+(job.speed_bps/1048576).toFixed(2)+" MiB/s · "+job.resolution,"meta"));
    const actions=node("div",undefined,"actions");
    if(["pending","running"].includes(job.status)) {const button=node("button",say("取消","Cancel"));button.onclick=()=>action("/api/cancel",{id:job.id});actions.append(button);}
    if(["failed","cancelled","interrupted","completed"].includes(job.status)) {const button=node("button",say("重試","Retry"));button.onclick=()=>action("/api/retry",{id:job.id});actions.append(button);}
    card.append(actions);
    const details=node("details");details.append(node("summary",say("事件紀錄","Event log")),node("pre",job.logs.join("\n")));card.append(details);fragment.append(card);
  }
  container.replaceChildren(fragment);
}
async function refresh() { clearTimeout(timer);if(!token)return;try{render(await api("/api/queue"));}catch(error){notice(error.message);}finally{if(token)timer=setTimeout(refresh,2000);}}
async function action(path,body) {if(busy)return;busy=true;try{await api(path,body);notice("");await refresh();}catch(error){notice(error.message);}finally{busy=false;}}
$("connect").onclick=async()=>{token=$("token").value.trim();$("token").value="";try{const data=await api("/api/queue");$("auth").hidden=true;$("workspace").hidden=false;notice("");render(data);timer=setTimeout(refresh,2000);}catch(error){token="";notice(error.message);}};
$("token").onkeydown=event=>{if(event.key==="Enter")$("connect").click();};
$("disconnect").onclick=disconnect;
$("add").onclick=async()=>{const urls=$("urls").value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);if(!urls.length||urls.length>100){notice(say("請輸入 1 至 100 個網址","Enter 1 to 100 URLs"));return;}await action("/api/queue",{jobs:urls.map(url=>({url,resolution:$("resolution").value,max_workers:Number($("workers").value),speed_limit_mib:Number($("limit").value)}))});};
$("clear").onclick=()=>action("/api/clear",{});
$("resume").onclick=()=>{if(confirm(say("已確認磁碟健康及剩餘空間？","Have you checked disk health and free space?")))action("/api/resume",{});};
$("language").onclick=()=>{language=language==="zh"?"en":"zh";document.documentElement.lang=language==="zh"?"zh-Hant":"en";$("language").textContent=language==="zh"?"English":"繁體中文";for(const element of document.querySelectorAll("[data-zh]"))element.textContent=element.dataset[language];if(token)refresh();};
'''
