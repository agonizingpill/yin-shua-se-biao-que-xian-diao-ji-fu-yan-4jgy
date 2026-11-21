import {esc,formData,fixed,localStamp} from '../utils/format.js';
import {badge,notify,empty} from '../components/press.js';
import {api,send} from '../api.js';

const REV_STATE={draft:'草稿',reclassifying:'复判中',completed:'全部完成',failed:'存在失败'};
const KIND={correct:'标签更正',withdraw:'参考撤回'};
const JOB_STATE={queued:'排队',running:'处理中',succeeded:'成功',failed:'失败'};

function refBadge(state){
  return state==='withdrawn'
    ? `<span class="status fail">已撤回</span>`
    : `<span class="status pass">训练有效</span>`;
}

export function render(s){
  const current=s.revisions?.length?s.revisions[0].model_version:1;
  return `<div class="head"><div><div class="eyebrow">REFERENCE REVISION / 10</div>
  <h1>错标的色标，不能放行复验。</h1>
  <p>更正标签或撤回参考张即形成新判读版本，系统逐张重新判读历史色标：旧合格结论被撤销并重新判读，触发的停机单保持停机，全部复核成功前拒绝签印与复机。</p></div>
  <span class="mono">当前判读模型 V${current}</span></div>

  <div class="production-layout">
    <section class="section">
      <h2>参考色标库（${s.references?.length||0} 张）</h2>
      <p class="muted">仅“训练有效”的参考进入 SVM 训练；撤回张保留原始数据但永不入训练。送测色标始终不插入参考。</p>
      <div class="tablewrap"><table><thead><tr><th>参考编号</th><th>四色密度</th><th>网点扩大</th><th>标签</th><th>状态</th><th>修订操作</th></tr></thead>
      <tbody>${(s.references||[]).map(r=>`<tr>
        <td class="mono">${esc(r.code)}</td>
        <td class="mono">${fixed(r.density_c)}/${fixed(r.density_m)}/${fixed(r.density_y)}/${fixed(r.density_k)}</td>
        <td class="mono">${fixed(r.dot_gain,1)}%</td>
        <td>${esc(r.label)}</td>
        <td>${refBadge(r.state)}</td>
        <td>${r.state==='withdrawn'?'<small>已撤回，留档</small>':`
          <button data-correct="${r.id}" ${s.user.role!=='admin'?'disabled':''}>更正标签</button>
          <button data-withdraw="${r.id}" ${s.user.role!=='admin'?'disabled':''}>撤回</button>`}</td>
      </tr>`).join('')}</tbody></table></div>
      <p class="note">更正与撤回仅主管可操作。修订期间继续接收新样，但上一版复核未全部成功前不能发布新版；撤回后任一类别少于 2 张或总数不足 60 张将拒绝。</p>
    </section>

    <section>
      <h2>判读版本与复核进度</h2>
      ${(s.revisions||[]).length===0?empty('尚无修订，当前为初始版本 V1：120 训练 / 30 留出，按 seed42 分层互斥。'):
        s.revisions.map(rev=>`<article class="section hold" data-rev="${rev.id}">
          <div class="eyebrow">模型 V${rev.model_version} · ${KIND[rev.kind]||rev.kind}</div>
          <h3 class="mono">${esc(rev.reference_code)}：${esc(rev.old_label)} → ${esc(rev.new_label||'撤回')}
            <span class="status ${rev.state==='completed'?'pass':rev.state==='failed'?'fail':'adjusting'}">${REV_STATE[rev.state]||rev.state}</span></h3>
          <p class="muted">依据：${esc(rev.reason)}<br>操作人 ${esc(rev.changed_by)} · 发布 ${esc(localStamp(rev.published_at))}</p>
          <div class="metricstrip">
            <div>待复核<b>${rev.progress.total}</b></div>
            <div>成功<b>${rev.progress.succeeded}</b></div>
            <div>失败<b>${rev.progress.failed}</b></div>
            <div>排队/处理中<b>${rev.progress.pending}</b></div>
          </div>
          ${rev.failures?.length?`<div class="empty">失败原因：${rev.failures.map(f=>`色标#${f.proof_id}（尝试${f.attempts}次）：${esc(f.last_error||'未知错误')}`).join('；')}</div>`:''}
          <div class="actions">
            <button data-jobs="${rev.id}">查看复核明细</button>
            ${rev.state!=='completed'?`<button class="primary" data-retry="${rev.id}" ${s.user.role==='viewer'?'disabled':''}>重试失败/未完复核</button>`:''}
          </div>
          <div class="rev-jobs" id="rev-jobs-${rev.id}" hidden></div>
        </article>`).join('')}
    </section>
  </div>

  <section class="section" style="margin-top:30px">
    <h2>色标采用版本与判读沿革</h2>
    <div class="tablewrap"><table><thead><tr><th>色标编号</th><th>当前结论</th><th>采用版本</th><th>判读时间</th><th>判读沿革（原始证据保留）</th></tr></thead>
    <tbody>${s.proofs.filter(p=>p.classification_state==='classified'||p.classification_state==='failed').map(p=>`<tr>
      <td class="mono">${esc(p.code)}</td>
      <td>${p.defect?badge(p.pass_flag?'pass':'fail',p.defect):badge(p.classification_state)}</td>
      <td class="mono">V${p.model_version}</td>
      <td><small>${esc(localStamp(p.classified_at))}</small></td>
      <td><button data-history="${p.id}">查看判读历史</button><div class="proof-history" id="history-${p.id}" hidden></div></td>
    </tr>`).join('')}</tbody></table></div>
    <p class="note">每次判读与修订复判都写入不可变历史；已签印所依据的原始证据保留，迟回的旧版本计算不会覆盖新判读。</p>
  </section>

  <dialog id="rev-dialog">
    <form id="rev-form" method="dialog">
      <h3 id="rev-dialog-title">更正参考标签</h3>
      <input type="hidden" name="ref_id">
      <input type="hidden" name="kind">
      <label id="new-label-wrap">更正后标签
        <select name="new_label"><option>合格</option><option>乳化</option><option>偏色</option></select>
      </label>
      <label>修订依据（至少 5 字）<textarea name="reason" required minlength="5" placeholder="复验核对发现错标/取样污染，具体依据…"></textarea></label>
      <div class="actions"><button class="primary" value="confirm">确认发布新版本</button><button value="cancel" formnovalidate>取消</button></div>
    </form>
  </dialog>`;
}

export function bind(s,reload){
  const dlg=document.querySelector('#rev-dialog');
  const open=(id,kind)=>{
    const ref=s.references.find(x=>x.id===Number(id));
    dlg.querySelector('#rev-dialog-title').textContent=kind==='correct'?`更正标签：${ref.code}`:`撤回参考：${ref.code}`;
    dlg.querySelector('[name=ref_id]').value=id;
    dlg.querySelector('[name=kind]').value=kind;
    dlg.querySelector('#new-label-wrap').hidden=kind!=='correct';
    dlg.showModal();
  };
  document.querySelectorAll('[data-correct]').forEach(b=>b.onclick=()=>open(b.dataset.correct,'correct'));
  document.querySelectorAll('[data-withdraw]').forEach(b=>b.onclick=()=>open(b.dataset.withdraw,'withdraw'));
  dlg.querySelector('#rev-form').onsubmit=async ev=>{
    const d=formData(ev.target);
    if (ev.submitter?.value!=='confirm')return;
    try{
      const path=`/references/${d.ref_id}/${d.kind}`;
      const body=d.kind==='correct'?{new_label:d.new_label,reason:d.reason}:{reason:d.reason};
      const rev=await send(path,body);
      notify(`已发布判读模型 V${rev.model_version}，正在后台复核 ${rev.total_jobs} 张历史色标`);
      await reload();
    }catch(e){notify(e.message)}
  };
  document.querySelectorAll('[data-retry]').forEach(b=>b.onclick=async()=>{
    try{const r=await send(`/reference-revisions/${b.dataset.retry}/retry`,{});notify(`已重试 ${r.retried} 张，重新投递 ${r.requeued} 张，不重复建版`);await reload()}
    catch(e){notify(e.message)}
  });
  document.querySelectorAll('[data-jobs]').forEach(b=>b.onclick=async()=>{
    const box=document.querySelector('#rev-jobs-'+b.dataset.jobs);
    box.hidden=!box.hidden;
    if(!box.hidden&&!box.dataset.loaded){
      try{
        const jobs=await api(`/reference-revisions/${b.dataset.jobs}/jobs`);
        box.innerHTML=`<div class="tablewrap"><table><thead><tr><th>色标</th><th>目标版本</th><th>状态</th><th>尝试</th><th>失败原因</th></tr></thead><tbody>${jobs.map(j=>{
          const p=s.proofs.find(x=>x.id===j.proof_id);
          return `<tr><td class="mono">${esc(p?.code||('#'+j.proof_id))}</td><td>V${j.target_version}</td>
          <td>${badge(j.state==='succeeded'?'pass':j.state==='failed'?'fail':'adjusting',JOB_STATE[j.state]||j.state)}</td>
          <td>${j.attempts}</td><td><small>${esc(j.last_error||'—')}</small></td></tr>`;
        }).join('')}</tbody></table></div>`;
        box.dataset.loaded='1';
      }catch(e){notify(e.message)}
    }
  });
  document.querySelectorAll('[data-history]').forEach(b=>b.onclick=async()=>{
    const box=document.querySelector('#history-'+b.dataset.history);
    box.hidden=!box.hidden;
    if(!box.hidden&&!box.dataset.loaded){
      try{
        const rows=await api(`/proofs/${b.dataset.history}/history`);
        box.innerHTML=`<div class="tablewrap"><table><thead><tr><th>版本</th><th>结论</th><th>来源</th><th>时间</th></tr></thead><tbody>${rows.map(h=>`<tr>
          <td class="mono">V${h.model_version}</td><td>${esc(h.defect||h.classification_state)}</td>
          <td><small>${esc(h.source)}</small></td><td><small>${esc(localStamp(h.classified_at))}</small></td></tr>`).join('')}</tbody></table></div>`;
        box.dataset.loaded='1';
      }catch(e){notify(e.message)}
    }
  });
}
