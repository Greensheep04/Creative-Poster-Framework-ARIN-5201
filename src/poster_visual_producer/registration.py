from __future__ import annotations
from poster_framework import AgentRegistration, AgentRole, ContentFormat, RunBudget
from .contracts import PosterSpec
BRIEF_FORMAT=ContentFormat(name="poster.creative-brief",version="0.1.0")
SPEC_FORMAT=ContentFormat(name="poster.spec",version="0.1.0")
CANDIDATE_FORMAT=ContentFormat(name="poster.candidate",version="0.1.0")

def project_budget():
    return RunBudget(max_revision_rounds=2,max_image_operations=3,max_critic_vision_calls=3)

def validate_candidate(content):
    if not isinstance(content,dict) or content.get("status")!="rendered":
        raise ValueError("A3 must publish a rendered candidate")
    if content.get("canvas")!={"width":720,"height":1280}:
        raise ValueError("Invalid candidate canvas")
    if not content.get("candidate_ref") or not content.get("render_manifest_ref"):
        raise ValueError("Candidate must contain framework-owned image and evidence references")

def registration(generator,fonts,**options):
    from .agent import VisualProducerAgent
    return AgentRegistration(
        role=AgentRole.visual_producer,
        instance_factory=lambda config:VisualProducerAgent(generator,fonts,**options),
        input_formats=frozenset({SPEC_FORMAT}),output_formats=frozenset({CANDIDATE_FORMAT}),
        input_validator=lambda content:PosterSpec.model_validate(content),
        output_validator=validate_candidate,
    )
