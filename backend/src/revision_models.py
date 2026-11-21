from sqlalchemy import String,Integer,Boolean,ForeignKey,JSON,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base

class ReferenceRevision(Base):
    # 一次参考色标更正/撤回发布，形成新的判读模型版本；训练集只取 active 参考
    __tablename__='reference_revisions'
    id:Mapped[int]=mapped_column(primary_key=True)
    model_version:Mapped[int]=mapped_column(unique=True)
    kind:Mapped[str]=mapped_column(String(20))          # correct 更正标签 / withdraw 撤回参考张
    reference_id:Mapped[int]=mapped_column(ForeignKey('reference_sheets.id'))
    reference_code:Mapped[str]=mapped_column(String(40))
    old_label:Mapped[str]=mapped_column(String(20))
    new_label:Mapped[str|None]=mapped_column(String(20),nullable=True)
    reason:Mapped[str]=mapped_column(String(300))
    changed_by:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(40))
    published_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
    state:Mapped[str]=mapped_column(String(20),default='draft')  # draft / reclassifying / completed / failed
    total_jobs:Mapped[int]=mapped_column(default=0)
    done_jobs:Mapped[int]=mapped_column(default=0)
    failed_jobs:Mapped[int]=mapped_column(default=0)

class ReclassificationJob(Base):
    # 修订发布后对既有判读张逐张重判；重投按 proof+revision 幂等，不重复建版、不重复停机单
    __tablename__='reclassification_jobs'
    __table_args__=(UniqueConstraint('revision_id','proof_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    revision_id:Mapped[int]=mapped_column(ForeignKey('reference_revisions.id'))
    proof_id:Mapped[int]=mapped_column(ForeignKey('proof_sheets.id'))
    target_version:Mapped[int]=mapped_column()
    state:Mapped[str]=mapped_column(String(20),default='queued')  # queued/running/succeeded/failed
    attempts:Mapped[int]=mapped_column(default=0)
    last_error:Mapped[str]=mapped_column(String(500),default='')
    created_at:Mapped[str]=mapped_column(String(40))
    updated_at:Mapped[str]=mapped_column(String(40))
    detail:Mapped[dict|None]=mapped_column(JSON,nullable=True)

class ClassificationHistory(Base):
    # 每张色标历次判读的不可变留痕：签印/复验所依据的原始证据在此保留，不被重判覆盖
    __tablename__='classification_history'
    id:Mapped[int]=mapped_column(primary_key=True)
    proof_id:Mapped[int]=mapped_column(ForeignKey('proof_sheets.id'))
    model_version:Mapped[int]=mapped_column()
    defect:Mapped[str|None]=mapped_column(String(20),nullable=True)
    pass_flag:Mapped[bool]=mapped_column(Boolean,default=False)
    classification_state:Mapped[str]=mapped_column(String(20))
    evidence:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    classified_at:Mapped[str]=mapped_column(String(40))
    source:Mapped[str]=mapped_column(String(40))       # worker 常规判读 / revision-<id> 修订重判
