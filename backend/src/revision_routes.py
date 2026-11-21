import os,redis
from fastapi import APIRouter,Depends
from sqlalchemy import select
from .db import db_session
from .auth import current,writers,admin
from .models import ReferenceSheet
from .revision_models import ReferenceRevision,ReclassificationJob,ClassificationHistory
from .revision_schemas import ReferenceCorrect,ReferenceWithdraw
from .services import pack
from . import revision_service
router=APIRouter();queue=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)

@router.get('/references')
def references(s=Depends(db_session),u=Depends(current)):
    return [pack(x) for x in s.scalars(select(ReferenceSheet).order_by(ReferenceSheet.id))]

@router.get('/reference-revisions')
def revisions(s=Depends(db_session),u=Depends(current)):
    rows=[pack(x) for x in s.scalars(select(ReferenceRevision).order_by(ReferenceRevision.id.desc()))]
    for row in rows:
        jobs=s.scalars(select(ReclassificationJob).where(ReclassificationJob.revision_id==row['id'])).all()
        row['progress']={'total':len(jobs),'succeeded':sum(j.state=='succeeded' for j in jobs),
            'failed':sum(j.state=='failed' for j in jobs),
            'pending':sum(j.state in ('queued','running') for j in jobs)}
        row['failures']=[{'proof_id':j.proof_id,'state':j.state,'attempts':j.attempts,'last_error':j.last_error}
            for j in jobs if j.state=='failed']
    return rows

@router.get('/reference-revisions/{key}/jobs')
def revision_jobs(key:int,s=Depends(db_session),u=Depends(current)):
    return [pack(x) for x in s.scalars(select(ReclassificationJob).where(ReclassificationJob.revision_id==key).order_by(ReclassificationJob.id))]

@router.post('/references/{key}/correct')
def correct(key:int,p:ReferenceCorrect,s=Depends(db_session),u=Depends(admin)):
    return pack(revision_service.publish(s,'correct',key,p,u,queue))

@router.post('/references/{key}/withdraw')
def withdraw(key:int,p:ReferenceWithdraw,s=Depends(db_session),u=Depends(admin)):
    return pack(revision_service.publish(s,'withdraw',key,p,u,queue))

@router.post('/reference-revisions/{key}/retry')
def retry(key:int,s=Depends(db_session),u=Depends(writers)):
    return revision_service.retry_failed(s,key,queue,u)

@router.get('/proofs/{key}/history')
def proof_history(key:int,s=Depends(db_session),u=Depends(current)):
    return [pack(x) for x in s.scalars(select(ClassificationHistory).where(
        ClassificationHistory.proof_id==key).order_by(ClassificationHistory.id.desc()))]
