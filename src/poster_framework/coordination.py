"""External QC authorization routing. A1/A2 agents and their interfaces are untouched."""
import copy, json
from uuid import uuid4
from .types import (AgentRole, ArtifactPurpose, ExecutionIdentity, RepairAuthorization,
    ReviewDecision, RevisionAction, VersionDependency)
from .errors import AgentIdentityMismatchError, BudgetExceededError


async def judge_qc_review(workflow,state,review):
    # Import at runtime to avoid a workflow/module cycle.
    from .workflow import WorkflowStatus
    details=review.details
    if review.decision==ReviewDecision.pass_ and details.get('production_pass') is True:
        state['session'].memory.set_unresolved_issues(())
        return {'review':review,'status':WorkflowStatus.passed,'stop_reason':'','stop':True}
    session=state['session']
    identity=ExecutionIdentity(run_id=state['run_id'],role=AgentRole.creative_director,
        invocation_id='external-authorization-'+uuid4().hex,execution_mode=state['execution_mode'])
    context=session.create_context(identity,workflow._metadata_for(state,identity))
    current=state['visual'].output.content
    feedback={**copy.deepcopy(details['feedback']),'current_candidate':copy.deepcopy(current),
        'original_request':state['request'].model_dump(mode='json'),
        'brief_dependency':{'artifact_id':state['brief'].record_reference.artifact_id,'revision':state['brief'].record_reference.revision},
        'layout_dependency':{'artifact_id':state['layout'].record_reference.artifact_id,'revision':state['layout'].record_reference.revision}}
    def save(name,value):
        return context.files.write_bytes(context.files.invocation_path(name),json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8'),purpose=ArtifactPurpose.evidence,media_type='application/json')
    ref=save('review-feedback.json',feedback)
    session.memory.publish_artifact('review_feedback',ref,feedback, state['review_stage'].output.content_format,publishing_identity=identity)
    def stop(reason):
        session.memory.set_unresolved_issues((reason,))
        return {'review':review,'status':WorkflowStatus.needs_review,'stop_reason':reason,'stop':True}
    if review.decision==ReviewDecision.pass_:
        return stop('QC pass is not eligible for production release')
    if workflow._repair_authorizer is None:
        return stop('QC feedback awaits external A1 repair authorization')
    if state['revision_rounds']>=session.budget.max_revision_rounds:
        return stop('revision round budget is exhausted')
    # Isolate callback mutation from framework state and preserve the raw A4 report.
    try:
        response=await workflow._repair_authorizer(copy.deepcopy(feedback),context)
    except BudgetExceededError as error:
        return stop(str(error))
    if response is None:
        return stop('No external repair authorization was provided')
    authorization=RepairAuthorization.model_validate(response)
    candidate=state['candidate']
    if authorization.based_on_candidate!=VersionDependency(artifact_id=candidate.artifact_id,revision=candidate.revision):
        raise AgentIdentityMismatchError('Stale authorization candidate')
    if authorization.expected_versions!=current['based_on']:
        raise AgentIdentityMismatchError('Stale authorization Brief/Spec versions')
    workflow._validate_revision_target(state,authorization.action,authorization.target)
    layout=state['layout'].output.content
    known={e['id'] for e in layout['elements']} | {a['asset_id'] for a in current['assets']} | {e.get('copy_ref') for e in layout['elements']}
    if not set(authorization.preserve)<=known:
        raise AgentIdentityMismatchError('Authorization preserve names unknown objects')
    if authorization.action==RevisionAction.regenerate_asset:
        target=next(a for a in current['assets'] if a['source_ref']['artifact_id']==authorization.target.artifact_id)
        if set(authorization.preserve)&({target['asset_id']}|set(target.get('element_ids',[]))):
            raise AgentIdentityMismatchError('Authorization both repairs and preserves its target')
    # Preserve decisions made via an external callback, without pretending Agent.run was invoked.
    save('repair-authorization.json',{'event':'external_authorization','authorization':authorization.model_dump(mode='json'),'report_decision':review.decision.value})
    return {'review':review,'repair_authorization':authorization,'stop':False}
