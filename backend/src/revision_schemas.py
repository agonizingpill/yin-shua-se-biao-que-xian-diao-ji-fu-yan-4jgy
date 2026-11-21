from typing import Literal
from pydantic import BaseModel,Field,field_validator
from .constants import DEFECTS
from .production_schemas import Inputs
class ReferenceCorrect(Inputs):
    new_label:Literal['合格','乳化','偏色']
    reason:str=Field(min_length=5,max_length=300)
    @field_validator('reason')
    @classmethod
    def reason_required(cls,v):
        if not v.strip():raise ValueError('修订依据不能为空')
        return v.strip()
class ReferenceWithdraw(Inputs):
    reason:str=Field(min_length=5,max_length=300)
    @field_validator('reason')
    @classmethod
    def reason_required(cls,v):
        if not v.strip():raise ValueError('撤回依据不能为空')
        return v.strip()
class ReclassRetry(Inputs):
    pass
