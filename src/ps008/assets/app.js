"use strict";
const $ = id => document.getElementById(id);
let csrf = "", currentReview = null, currentLab = null, working = false;
const escapeHTML = text => String(text ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labelOutcome = value => ({reached:"Destination reached",missing_route:"Missing route",loop:"Routing loop",invalid_adjacency:"Invalid adjacency"}[value] || value);
const newRequestID = () => `request-${crypto.randomUUID()}`;
function notice(message, good=false) { $("notice").textContent=message; $("notice").className=`notice${good ? " success" : ""}`; $("notice").hidden=!message; }
async function api(path, data) {
  const response=await fetch(path, {method:data === undefined ? "GET":"POST", headers:data === undefined ? {} : {"Content-Type":"application/json","X-CSRF-Token":csrf}, body:data === undefined ? undefined : JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok) throw new Error(result.error || (Array.isArray(result.detail) ? result.detail.map(x=>x.msg).join("; ") : result.detail) || "Request failed");
  return result;
}
async function task(fn) {
  if(working) return;
  working=true; $("workspace").disabled=true; document.querySelectorAll("button").forEach(x=>x.disabled=true); notice("");
  try { await fn(); } catch(error) { notice(error.message); } finally { working=false; $("workspace").disabled=false; document.querySelectorAll("button").forEach(x=>x.disabled=false); updateDecisions(); }
}
function updateDecisions() { const pending=currentReview?.status === "pending"; $("approve").disabled=!pending || working; $("reject").disabled=!pending || working; }
function drawTrace(trace) {
  document.querySelectorAll(".router-node,.network-link").forEach(x=>x.classList.remove("active"));
  trace.path.forEach(r=>$("node-"+r)?.classList.add("active"));
  trace.path.slice(1).forEach((r,i)=>{const pair=[trace.path[i],r].sort().join("-"); $("link-"+pair)?.classList.add("active");});
  $("trace-result").textContent=`${trace.path.join(" → ")} · ${labelOutcome(trace.outcome)}. ${trace.steps.map(s=>`${s.router}: ${s.route}${s.next_hop ? " via "+s.next_hop:" (connected)"}`).join("; ")}`;
}
function routeTable(rows) { return `<div class="table-wrap"><table><thead><tr><th>Destination</th><th>Next hop</th><th>Type</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${escapeHTML(r.network)}</td><td>${escapeHTML(r.next_hop || "direct")}</td><td class="route-kind">${escapeHTML(r.kind)}</td></tr>`).join("")}</tbody></table></div>`; }
function renderLab(lab) {
  currentLab=lab; $("revision").textContent=lab.revision; $("workspace").value=lab.workspace; $("demo-panel").hidden=lab.workspace !== "demo";
  $("route-tables").innerHTML=lab.topology.routers.map(router=>{
    const rows=[...router.connected.map(network=>({network,kind:"connected"})),...router.bootstrap_routes.filter(r=>!lab.routes[router.id].some(x=>x.network===r.network)).map(r=>({...r,kind:"bootstrap"})),...lab.routes[router.id].map(r=>({...r,kind:"managed"}))];
    return `<div class="router-table"><h3>${escapeHTML(router.id)}</h3>${routeTable(rows)}</div>`;
  }).join("");
}
function renderReview(review) {
  currentReview=review; $("review-empty").hidden=true; $("review-content").hidden=false;
  $("review-summary").textContent=review.candidate.change_summary;
  $("review-reason").textContent=`Reason: ${review.candidate.request.requested_change}`;
  $("commands").textContent=review.candidate.commands.join("\n");
  $("review-meta").textContent=`Revision ${review.candidate.base_revision} · ${review.candidate.request.request_id}`;
  $("review-status").textContent=review.status.replaceAll("_"," "); $("review-status").className=`pill ${["pending","deployed"].includes(review.status)?"good":"bad"}`;
  const validation=review.validation;
  const checks=validation.errors.length ? validation.errors.map(x=>`<div class="check error">Blocked · ${escapeHTML(x)}</div>`).join("") : validation.checks.map(x=>`<div class="check">Passed · ${escapeHTML(x.name)}<br>${escapeHTML(x.detail)}</div>`).join("");
  $("validation").innerHTML=checks+validation.warnings.map(x=>`<div class="check warning">Warning · ${escapeHTML(x)}</div>`).join("");
  const traces=validation.impact.traces || [];
  const representative=traces.find(x=>x.before.destination.endsWith(".0")) || traces[0];
  $("impact").innerHTML=representative ? `<div class="impact-title">FORWARDING IMPACT · ${traces.length} PREFIX-BOUNDARY PROBES</div>`+["before","after"].map(key=>{const t=representative[key];return `<div class="path-row"><span class="label">${key.toUpperCase()}</span><span>${escapeHTML(t.path.join(" → "))}</span><span class="outcome ${t.outcome !== "reached" ? "bad":""}">${escapeHTML(labelOutcome(t.outcome))}</span></div>`;}).join("")+`<details><summary>All forwarding probes</summary>${traces.map(x=>`<p class="field-hint">${escapeHTML(x.after.destination)}: ${escapeHTML(x.after.path.join(" → "))} · ${escapeHTML(labelOutcome(x.after.outcome))}</p>`).join("")}</details>` : "";
  const impact=validation.impact;
  $("route-diff").innerHTML=impact.before_routes ? ["before","after"].map(key=>`<p class="field-hint">${key.toUpperCase()} · managed routes on ${escapeHTML(review.candidate.router)}</p>${routeTable(impact[key+"_routes"][review.candidate.router].map(x=>({...x,kind:"managed"})))}${impact[key+"_routes"][review.candidate.router].length ? "":"<p class='no-routes'>No managed routes.</p>"}`).join("") : "";
  $("deployment-result").innerHTML=review.deployment ? `<div class="check ${review.deployment.success ? "":"warning"}">${escapeHTML(review.deployment.message)}${review.deployment.rolled_back?"<br>Snapshot restored. Routes, revision, and applied request IDs are unchanged.":""}</div>` : "";
  $("approval-hint").textContent=review.status === "pending" ? `Approval applies exactly this command. Review expires at ${new Date(review.expires_at).toLocaleTimeString()}.` : `Review ${review.status}. Generate a fresh review for another decision.`;
  updateDecisions();
}
async function refresh() {
  const [lab,audit]=await Promise.all([api("/api/lab"),api("/api/audit")]); renderLab(lab);
  $("audit").innerHTML=audit.events.length ? [...audit.events].reverse().map(e=>`<li><strong>${escapeHTML(e.action.replaceAll("_"," "))}</strong><div class="audit-meta">${escapeHTML(new Date(e.at).toLocaleString())} · revision ${e.revision}</div>${e.review_id ? `<button class="text-button" data-review="${escapeHTML(e.review_id)}" ${working ? "disabled" : ""}>Open review ${escapeHTML(e.review_id.slice(0,8))}</button>`:""}${e.message ? `<p>${escapeHTML(e.message)}</p>`:""}${e.errors?.length?`<p>${escapeHTML(e.errors.join("; "))}</p>`:""}</li>`).join("") : `<li><strong>Ready for your first review</strong><div class="audit-meta">Decisions and deployment outcomes will be recorded here.</div></li>`;
  drawTrace(await api(`/api/trace?source=${encodeURIComponent($("trace-source").value)}&destination=${encodeURIComponent($("trace-address").value)}`));
}
function resetReview() { currentReview=null; $("review-empty").hidden=false; $("review-content").hidden=true; $("review-status").textContent="Awaiting intent"; $("review-status").className="pill"; }
function fillIntent(request) { $("intent-type").value=request.intent_type; $("source").value=request.source; $("destination").value=request.destination; $("next-hop").value=request.next_hop || ""; $("reason").value=request.requested_change; $("request-id").value=request.request_id; toggleHop(); }
function toggleHop() { const remove=$("intent-type").value === "remove_static_route"; $("hop-field").hidden=remove; $("next-hop").required=!remove; $("next-hop").disabled=remove; }
$("intent-type").addEventListener("change",toggleHop);
$("intent-form").addEventListener("submit",event=>{event.preventDefault();task(async()=>{const data=Object.fromEntries(new FormData(event.target));data.protocol="static";if(data.intent_type === "remove_static_route")data.next_hop=null;resetReview();renderReview(await api("/api/reviews",data));await refresh();});});
$("trace-form").addEventListener("submit",event=>{event.preventDefault();task(async()=>drawTrace(await api(`/api/trace?source=${encodeURIComponent($("trace-source").value)}&destination=${encodeURIComponent($("trace-address").value)}`)));});
for(const decision of ["approve","reject"]) $(decision).addEventListener("click",()=>task(async()=>{try { renderReview(await api(`/api/reviews/${currentReview.review_id}/${decision}`,{})); } catch(error) { renderReview(await api(`/api/reviews/${currentReview.review_id}`));throw error;} finally {await refresh();} if(currentReview.status === "deployed")$("request-id").value=newRequestID();}));
$("workspace").addEventListener("change",()=>task(async()=>{await api("/api/workspace",{mode:$("workspace").value});resetReview();await refresh();}));
document.querySelectorAll("[data-scenario]").forEach(button=>button.addEventListener("click",()=>task(async()=>{const review=await api(`/api/demo/scenarios/${button.dataset.scenario}`,{});fillIntent(review.candidate.request);renderReview(review);await refresh();})));
$("refresh").addEventListener("click",()=>task(async()=>{if(currentReview)renderReview(await api(`/api/reviews/${currentReview.review_id}`));await refresh();}));
$("audit").addEventListener("click",event=>{const button=event.target.closest("[data-review]");if(button)task(async()=>{const review=await api(`/api/reviews/${button.dataset.review}`);fillIntent(review.candidate.request);renderReview(review);await refresh();$("review-status").scrollIntoView({block:"center",behavior:"smooth"});});});
$("export-audit").addEventListener("click",()=>task(async()=>{const data=await api("/api/audit");const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=`ps008-${currentLab.workspace}-audit.json`;a.click();URL.revokeObjectURL(url);}));
$("request-id").value=newRequestID();
task(async()=>{const session=await api("/api/session");csrf=session.csrf_token;await refresh();});
setInterval(()=>{if(!working && currentReview?.status === "pending" && Date.now() >= Date.parse(currentReview.expires_at))task(async()=>renderReview(await api(`/api/reviews/${currentReview.review_id}`)));},5000);
