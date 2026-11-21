from sqlalchemy import select,func
from fastapi import HTTPException
from .models import PrintJob,ProofSheet,Audit,ReferenceSheet,INITIAL_MODEL_VERSION
from .production_models import PressSample,PressHold
from .revision_models import ReferenceRevision,ReclassificationJob,ClassificationHistory
from .classifier import active_refs
from .services import get,now,audit

QUEUE='press-classify'
# 需要在发布后重新判读的色标：已有判读结论或正在判读/判读失败的全部历史送测张；未测量的新张不重判
RECLASS_STATES=('classifying','classified','failed')

def current_model_version(s):
    v=s.scalar(select(func.coalesce(func.max(ReferenceRevision.model_version),INITIAL_MODEL_VERSION)))
    return int(v)

def active_revision(s):
    return s.scalar(select(ReferenceRevision).where(ReferenceRevision.state=='reclassifying').with_for_update())

def _record_history(s,p,source):
    s.add(ClassificationHistory(proof_id=p.id,model_version=p.model_version,defect=p.defect,pass_flag=p.pass_flag,
        classification_state=p.classification_state,evidence=p.evidence,classified_at=p.classified_at or now(),source=source))

def publish(s,kind,key,payload,u,r):
    if kind not in ('correct','withdraw'):raise HTTPException(400,'未知修订类型')
    ref=s.scalar(select(ReferenceSheet).where(ReferenceSheet.id==key).with_for_update())
    if not ref:raise HTTPException(404,'参考色标不存在')
    if ref.state=='withdrawn':raise HTTPException(409,'该参考张已撤回，不能再修订；请重新登记参考数据')
    if active_revision(s):raise HTTPException(409,'上一版修订仍在复判，全部复核完成前不能发布新版')
    old_label=ref.label
    if kind=='correct':
        new_label=payload.new_label
        if new_label==old_label:raise HTTPException(409,'更正后的标签与原标签相同，无需修订')
    else:
        new_label=None
        # 撤回后仍须保证每个类别可分层留出
        from collections import Counter
        counts=Counter(x.label for x in active_refs(s) if x.id!=ref.id)
        from .constants import DEFECTS
        if sum(counts.values())<60 or any(counts.get(c,0)<2 for c in DEFECTS):
            raise HTTPException(409,'撤回后带标签参考不足或某类别少于2张，无法训练留出，请先更正标签或补样')
    version=current_model_version(s)+1
    rev=ReferenceRevision(model_version=version,kind=kind,reference_id=ref.id,reference_code=ref.code,
        old_label=old_label,new_label=new_label,reason=payload.reason,changed_by=u.email,
        created_at=now(),state='draft')
    s.add(rev);s.flush()
    # 参考本体变更：撤回置 withdrawn，更正改标签；原始数据行保留，可在修订单与审计中追溯
    if kind=='withdraw':ref.state='withdrawn'
    else:ref.label=new_label
    proofs=s.scalars(select(ProofSheet).where(ProofSheet.classification_state.in_(RECLASS_STATES)).order_by(ProofSheet.id)).all()
    stamp=now()
    jobs=[ReclassificationJob(revision_id=rev.id,proof_id=p.id,target_version=version,state='queued',
        created_at=stamp,updated_at=stamp) for p in proofs]
    s.add_all(jobs);s.flush()
    # 无待复核色标时修订即完成，避免永久停在 reclassifying 阻塞后续修订
    rev.state='reclassifying' if jobs else 'completed'
    rev.total_jobs=len(jobs);rev.published_at=now()
    audit(s,u,f'参考修订V{version} {kind} {ref.code} {old_label}->{new_label or "撤回"} 依据：{payload.reason}；待复核{len(jobs)}张')
    s.commit()
    # 入队在提交后；失败可由 resume 幂等补投，不重复建版
    for j in jobs:
        try:r.rpush(QUEUE,_msg(j.id,version))
        except Exception:
            break
    return rev

def _msg(job_id,version):
    import json
    return json.dumps({'type':'reclass','reclass_id':job_id,'model_version':version})

def enqueue_pending(s,r,rev=None):
    # 中断恢复：重新投递排队作业，以及 worker 中断残留的 running 作业（重置为 queued 防并发重复）；
    # succeeded/failed 不重投，不重复建版或停机单
    q=select(ReclassificationJob).where(ReclassificationJob.state.in_(('queued','running')))
    if rev:q=q.where(ReclassificationJob.revision_id==rev.id)
    pending=s.scalars(q.order_by(ReclassificationJob.id)).all()
    stamp=now()
    for j in pending:
        if j.state=='running':j.state='queued';j.updated_at=stamp
        r.rpush(QUEUE,_msg(j.id,j.target_version))
    s.commit()
    return len(pending)

def _open_hold(s,job_id,proof,rev):
    # 幂等：同工单已有活动停机单则挂接，不重复建单
    hold=s.scalar(select(PressHold).where(PressHold.job_id==job_id,PressHold.state=='active').with_for_update())
    if hold:
        if proof.id not in (hold.reproof_ids or []):
            hold.reproof_ids=[*(hold.reproof_ids or []),proof.id]
        return hold
    job=s.get(PrintJob,job_id)
    hold=PressHold(job_id=job_id,sample_id=None,
        reason=f'参考修订V{rev.model_version}复判 {proof.code} 结论翻为{proof.defect or "判读失败"}，撤销旧合格结论',
        created_at=now(),reproof_ids=[proof.id])
    s.add(hold)
    if job and job.status=='production':job.status='hold'
    return hold

def _finish_revision(s,rev):
    # 终态计数独立查询，避免同事务内待提交变更导致的脏读误判
    total=s.scalar(select(func.count()).select_from(ReclassificationJob)
        .where(ReclassificationJob.revision_id==rev.id))
    succeeded=s.scalar(select(func.count()).select_from(ReclassificationJob)
        .where(ReclassificationJob.revision_id==rev.id,ReclassificationJob.state=='succeeded'))
    failed=s.scalar(select(func.count()).select_from(ReclassificationJob)
        .where(ReclassificationJob.revision_id==rev.id,ReclassificationJob.state=='failed'))
    open_jobs=int(total or 0)-int(succeeded or 0)-int(failed or 0)
    rev.failed_jobs=int(failed or 0);rev.done_jobs=int(succeeded or 0)+int(failed or 0)
    if open_jobs<=0:
        rev.state='failed' if failed else 'completed'

def _mark_failed(job_id,rev_id,ex):
    # 独立会话落库失败状态，保证异常不会让作业永久停在 running
    from .db import Session as NewSession
    with NewSession() as fs:
        row=fs.get(ReclassificationJob,job_id)
        if row and row.state!='succeeded':
            row.state='failed';row.last_error=str(ex)[:500];row.updated_at=now()
        r2=fs.get(ReferenceRevision,rev_id)
        if r2:_finish_revision(fs,r2)
        fs.commit()

def run_reclass(s,job_id,r_msg=None,redis_client=None):
    # 队列单消费者串行处理，状态机幂等；不使用悲观行锁以避免 SQLite/MySQL 跨方言锁冲突
    job=s.get(ReclassificationJob,job_id)
    if not job:return
    if job.state=='succeeded':return  # 幂等：重复消息不重算
    rev=s.get(ReferenceRevision,job.revision_id)
    p=s.get(ProofSheet,job.proof_id)
    # 迟回的旧计算不覆盖新判读：色标已被更新版本复核过则直接收敛为成功
    if not p or p.model_version>job.target_version:
        job.state='succeeded';job.updated_at=now();_finish_revision(s,rev);s.commit();return
    job.state='running';job.attempts=job.attempts+1;job.updated_at=now();s.commit()
    try:
        from .classifier import classify
        label,evidence=classify(s,p)
        old_defect,old_version=p.defect,p.model_version
        _record_history(s,p,f'revision-{rev.id}')  # 已签印依据的原始证据先留痕再覆盖
        p.defect=label;p.evidence=evidence;p.pass_flag=label=='合格'
        p.classification_state='classified';p.classified_at=now();p.model_version=rev.model_version
        item=s.scalar(select(PressSample).where(PressSample.proof_id==p.id))
        if item:
            # 不回写已关闭历史；量产抽检状态随新判读翻转，不合格即停机
            if label=='合格':item.state='passed'
            else:
                item.state='held'
                _open_hold(s,p.job_id,p,rev)
        else:
            job_obj=s.get(PrintJob,p.job_id)
            if job_obj and job_obj.status=='production' and label!='合格':
                _open_hold(s,p.job_id,p,rev)
        job.state='succeeded';job.last_error='';job.detail={'old_defect':old_defect,'old_version':old_version,'new_defect':label}
        job.updated_at=now()
        audit(s,rev.changed_by,f'复判完成 {p.code} V{old_version}->{rev.model_version} 结论{old_defect}->{label}')
        _finish_revision(s,rev);s.commit()
    except Exception as ex:
        try:s.rollback()
        except Exception:pass
        try:_mark_failed(job_id,rev.id,ex)
        except Exception:
            import logging;logging.exception('mark reclassification failed job=%s',job_id)

def retry_failed(s,key,redis_client,u):
    rev=s.get(ReferenceRevision,key)
    if not rev:raise HTTPException(404,'修订单不存在')
    if rev.state not in ('reclassifying','failed'):raise HTTPException(409,'该修订已全部完成，无需重试')
    rows=s.scalars(select(ReclassificationJob).where(ReclassificationJob.revision_id==key,
        ReclassificationJob.state=='failed')).all()
    if not rows:
        queued=enqueue_pending(s,redis_client,rev)
        return {'retried':0,'requeued':queued}
    stamp=now()
    for j in rows:j.state='queued';j.updated_at=stamp
    rev.state='reclassifying'
    audit(s,u.email,f'修订V{rev.model_version} 失败复核重试 {len(rows)}张')
    s.commit()
    enqueue_pending(s,redis_client,rev)
    return {'retried':len(rows),'requeued':len(rows)}

def assert_current_basis(s,proof_id,action):
    # 签印/复机等关键动作拒绝旧依据：依据色标的模型版本必须等于当前版本，且修订期无未完成复核
    p=get(s,ProofSheet,proof_id)
    version=current_model_version(s)
    if p.model_version<version:
        raise HTTPException(409,f'该判读依据模型V{p.model_version}已过时（当前V{version}），请等待修订复判后以新判读为准再{action}')
    stale=s.scalar(select(ReclassificationJob.id).join(ReferenceRevision,
        ReclassificationJob.revision_id==ReferenceRevision.id).where(
        ReclassificationJob.proof_id==proof_id,
        ReclassificationJob.state.in_(('queued','running','failed'))))
    if stale:raise HTTPException(409,f'参考修订复核尚未全部成功，旧依据不能用于{action}')
    return p

def assert_not_reclassifying(s,proof_id):
    # 修订复判进行中禁止对同一色标手动判读，避免两个判读竞态互相覆盖
    busy=s.scalar(select(ReclassificationJob.id).where(
        ReclassificationJob.proof_id==proof_id,
        ReclassificationJob.state.in_(('queued','running'))))
    if busy:raise HTTPException(409,'该色标正在按参考修订重新判读，请等待复核完成')
