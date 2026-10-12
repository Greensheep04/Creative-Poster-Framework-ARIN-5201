"""A4's strict LocalManifest v1 and complete audit core; no production observations are invented."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from poster_framework import JSONValue

Text = Annotated[str, Field(min_length=1)]
Status = Literal['pass', 'fail', 'unknown']
BASIC_CHECKS = ('keyword_alignment', 'user_constraints', 'text_readability', 'layout_quality')
ALL_CHECKS = BASIC_CHECKS + ('text_accuracy', 'layout_geometry')

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

class Keyword(StrictModel):
    keyword_id: Text
    text: Text

class Constraint(StrictModel):
    constraint_id: Text
    name: Text
    value: JSONValue
    @model_validator(mode='after')
    def required_text(self):
        if self.name == 'required_text' and (not isinstance(self.value,str) or not self.value.strip()):
            raise ValueError('required_text must be a nonempty string')
        return self

class ImagePath(StrictModel):
    path: Text

class ApprovedCopy(StrictModel):
    copy_id: Text
    text: Text
    required: bool = True
    match_policy: Literal['exact','typographic'] = 'exact'

class LayoutElement(StrictModel):
    element_id: Text
    bbox_xyxy: tuple[float,float,float,float]
    copy_id: Text | None = None
    @field_validator('bbox_xyxy',mode='before')
    @classmethod
    def finite_box(cls, value):
        import math
        if not isinstance(value,(tuple,list)) or len(value)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):
            raise ValueError('bbox must contain four finite numbers')
        return value

class LayoutContext(StrictModel):
    elements: list[LayoutElement]

class Asset(StrictModel):
    asset_key: Text
    element_ids: list[str] = Field(default_factory=list)
    description: str | None = None

class AssetContext(StrictModel):
    assets: list[Asset]

class RenderContext(StrictModel):
    messages: list[str] = Field(default_factory=list)


def unique(rows, key):
    ids=[getattr(r,key) for r in rows]
    if len(ids)!=len(set(ids)):
        raise ValueError('Duplicate '+key)

class LocalManifestV1(StrictModel):
    schema_version: Literal[1] = 1
    sample_id: Text
    keywords: Annotated[list[Keyword],Field(min_length=2,max_length=5)]
    user_constraints: list[Constraint]
    image: ImagePath
    approved_copy: list[ApprovedCopy] | None = None
    layout_context: LayoutContext | None = None
    asset_context: AssetContext | None = None
    design_context: str | None = None
    render_context: RenderContext | None = None
    required_checks: list[str] = Field(default_factory=lambda:list(BASIC_CHECKS))

    @model_validator(mode='after')
    def consistency(self):
        unique(self.keywords,'keyword_id'); unique(self.user_constraints,'constraint_id')
        unique(self.approved_copy or [],'copy_id')
        if self.layout_context:
            unique(self.layout_context.elements,'element_id')
            ids={c.copy_id for c in self.approved_copy or []}
            if any(e.copy_id and e.copy_id not in ids for e in self.layout_context.elements):
                raise ValueError('Layout references unknown approved copy')
        if self.asset_context: unique(self.asset_context.assets,'asset_key')
        if not set(BASIC_CHECKS)<=set(self.required_checks) or not set(self.required_checks)<=set(ALL_CHECKS) or len(self.required_checks)!=len(set(self.required_checks)):
            raise ValueError('Basic checks cannot be removed; unknown/duplicate checks are forbidden')
        if required_copies(self) and 'text_accuracy' not in self.required_checks:
            object.__setattr__(self,'required_checks',[*self.required_checks,'text_accuracy'])
        return self


def required_copies(manifest):
    copies={c.copy_id:c.text for c in manifest.approved_copy or [] if c.required}
    for c in manifest.user_constraints:
        if c.name=='required_text': copies['constraint:'+c.constraint_id]=c.value
    return copies

class KeywordCheck(StrictModel):
    keyword_id: Text
    status: Status
    evidence: str

class ConstraintCheck(StrictModel):
    constraint_id: Text
    status: Status
    evidence: str

class TextCheck(StrictModel):
    copy_id: Text
    observed_text: str | None
    status: Status
    readability: Status

class Coverage(StrictModel):
    check_id: Literal['keyword_alignment','user_constraints','text_readability','layout_quality','text_accuracy','layout_geometry']
    required: bool
    state: Literal['assessed','not_assessed','unknown']
    status: Status | None
    reason: str | None

class Issue(StrictModel):
    code: Text
    severity: Literal['blocking','major','minor']
    element_id: str | None
    bbox_xyxy: tuple[float,float,float,float] | None
    source: Literal['program','model']
    evidence: Text
    suggestion: Text

class RepairHint(StrictModel):
    stage: Literal['brief','layout','asset','render']
    element_id: str | None
    asset_key: str | None
    reason: str
    preserve: list[str]

class AuditReportV3(BaseModel):
    # The transport's documented call metadata is kept as JSON, never guessed.
    model_config = ConfigDict(extra='allow', frozen=True)
    schema_version: Literal[3]
    sample_id: Text
    image_sha256: Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    decision: Status
    keyword_checks: list[KeywordCheck]
    constraint_checks: list[ConstraintCheck]
    text_checks: list[TextCheck]
    coverage: list[Coverage]
    issues: list[Issue]
    uncertainties: list[str]
    repair_hint: RepairHint | None
    model: JSONValue
    prompt_version: Text
    execution_mode: Text
    calls: JSONValue
    vision_calls: JSONValue
    @model_validator(mode='after')
    def unique_checks(self):
        for rows,key in ((self.keyword_checks,'keyword_id'),(self.constraint_checks,'constraint_id'),(self.text_checks,'copy_id'),(self.coverage,'check_id')):
            unique(rows,key)
        if {c.check_id for c in self.coverage}!=set(ALL_CHECKS):
            raise ValueError('A complete report must include coverage for all six checks')
        return self
