import os,json,logging
from pathlib import Path
import redis
from .db import Session
from .models import ProofSheet
from .classifier import classify
from .services import now
from .sampling_service import finish
from . import revision_service
r=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True);ARTIFACTS=Path(os.environ.get('ARTIFACTS_DIR','/artifacts'));ARTIFACTS.mkdir(exist_ok=True,parents=True)

def resume_interrupted():
    # 中断后恢复未完复核：仅重新投递未完成作业，不重复建版、不重复生成停机单
    try:
        with Session() as s:
            n=revision_service.enqueue_pending(s,r)
            if n:logging.warning('resumed %s pending reclassification jobs',n)
    except Exception:logging.exception('resume failed')

def handle_classify(proof_id,expected_version=None):
    with Session() as s:
        p=s.get(ProofSheet,proof_id)
        if not p or p.classification_state!='classifying':return
        try:
            label,evidence=classify(s,p)
            # 迟回的旧计算不覆盖新判读：显式声明的旧版本结果，或色标已被更新版本接管时丢弃
            fresh=s.get(ProofSheet,proof_id)
            if (expected_version is not None and expected_version<evidence.get('model_version',1)) or \
               (expected_version is None and fresh.model_version>=evidence.get('model_version',1)
                    and fresh.classification_state!='classifying'):
                logging.warning('stale classify result discarded proof=%s',proof_id);return
            from .revision_models import ClassificationHistory
            # 每次判读都留痕：首判与重判的原始证据均可追溯，且不随色标当前结论被覆盖
            s.add(ClassificationHistory(proof_id=p.id,model_version=evidence['model_version'],defect=label,
                pass_flag=(label=='合格'),classification_state='classified',evidence=evidence,
                classified_at=now(),source='worker'))
            p.defect=label;p.evidence=evidence;p.pass_flag=label=='合格'
            p.classification_state='classified';p.classified_at=now();p.model_version=evidence['model_version']
            finish(s,p);s.commit()
            (ARTIFACTS/f'proof-{p.id}.json').write_text(json.dumps(p.evidence,ensure_ascii=False))
        except Exception:
            s.rollback();p=s.get(ProofSheet,proof_id)
            if p and p.classification_state=='classifying':
                p.classification_state='failed';p.evidence={'error':'参考不足或训练失败，请查看参考修订页'};finish(s,p);s.commit()
            logging.exception('classification failed')

def handle_reclass(reclass_id):
    with Session() as s:
        try:
            revision_service.run_reclass(s,reclass_id,None,r)
        except Exception:logging.exception('reclassification failed job=%s',reclass_id)

def main():
    resume_interrupted()
    while True:
        Path('/tmp/worker-heartbeat').touch();entry=r.blpop(revision_service.QUEUE,timeout=2)
        if not entry:
            continue
        raw=entry[1]
        try:
            msg=json.loads(raw)
        except (ValueError,TypeError):
            msg={'type':'classify','proof_id':int(raw)}  # 兼容旧版裸 id 消息
        if msg.get('type')=='reclass':
            handle_reclass(int(msg['reclass_id']))
        else:
            handle_classify(int(msg.get('proof_id',raw)),msg.get('model_version'))

if __name__=='__main__':
    main()
