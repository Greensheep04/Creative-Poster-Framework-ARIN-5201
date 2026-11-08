"""Explain missing audit evidence to A1 without inferring a repair root cause."""

from __future__ import annotations

from .contracts import (
    AuditInputV1, CheckId, ConstraintCheck, Coverage, KeywordCheck,
    ModelObservation, TextCheck,
)


def _message(target: str, doubt: str, missing: str, suggestion: str) -> str:
    return (f"检查对象：{target}；疑点：{doubt}；缺失证据：{missing}；"
            f"建议：{suggestion}")


def explain_unknowns(
    audit: AuditInputV1, observation: ModelObservation | None,
    keywords: tuple[KeywordCheck, ...], constraints: tuple[ConstraintCheck, ...],
    texts: tuple[TextCheck, ...], coverage: tuple[Coverage, ...],
    *, failure: str | None = None,
) -> tuple[tuple[Coverage, ...], tuple[str, ...]]:
    """Enrich existing unknowns; optional gaps do not change the audit decision."""
    by_check = {item.check_id: item for item in coverage}
    reasons: dict[CheckId, list[str]] = {}
    uncertainties: list[str] = []

    def add(check_ids: tuple[CheckId, ...], message: str) -> None:
        for check_id in check_ids:
            item = by_check[check_id]
            if item.state == "unknown":
                reasons.setdefault(check_id, []).append(message)
                if item.required:
                    uncertainties.append(message)

    if observation is None or failure:
        cause = f"已记录原因：{failure}" if failure else "未取得通过协议校验的模型观察"
        uncertainties.append(_message(
            "有效模型观察（当前候选）",
            f"审核观察尚未完成；{cause}，不能据此判断图片存在何种瑕疵",
            "当前成品的有效模型观察及可靠的关键词、约束、文字和布局检查",
            "A1 查看 calls 中的错误或校验记录，核实当前候选图片与审核输入后重新审核",
        ))

    if observation is not None:
        uncertainties.extend(_message(
            "模型不确定项（位置未确定）", f"模型记录：{item}",
            "可核对的补充观察及对应检查或元素的定位",
            "A1 对照原始要求与当前成品核实涉及的对象，补充证据后重新审核",
        ) for item in observation.uncertainties)

    if audit.approved_copy is None:
        add(("text_accuracy",), _message(
            "text_accuracy / approved_copy", "未提供正式文案，文字准确性检查尚未完成",
            "A1 确认的正式文案及 copy_id、required、match_policy",
            "A1 补充原始要求对应的正式文案，并核对 A2/A3 使用同一候选文案后重新审核",
        ))
    if audit.layout_context is None:
        add(("layout_geometry",), _message(
            "layout_geometry / layout_context", "未提供当前布局，声明框的几何检查尚未完成",
            "A2 当前布局的 element_id、bbox_xyxy 及文案引用",
            "A1 向 A2 取得当前候选的 layout_context，核对坐标属于最终画布后重新审核",
        ))

    missing_literals = {
        c.constraint_id for c in audit.user_constraints
        if c.name == "required_text" and not any(
            item.required and item.text == c.value for item in audit.approved_copy or ()
        )
    }
    for constraint in audit.user_constraints:
        if constraint.constraint_id in missing_literals:
            add(("user_constraints", "text_accuracy"), _message(
                f"constraint_id={constraint.constraint_id} / required_text / "
                f"copy_id=constraint:{constraint.constraint_id}",
                "固定文字未列入必需正式文案，尚不能完成逐字核对",
                "approved_copy 中与原始 required_text 一致且 required=true 的文字记录",
                "A1 补充该固定文字的正式文案并保留 exact 匹配，核对 A2/A3 的实际文字后重新审核",
            ))

    keyword_names = {item.keyword_id: item.text for item in audit.keywords}
    for check in keywords:
        if check.status == "unknown":
            doubt = ("未取得有效观察，尚不能判断关键词的画面表达"
                     if observation is None else f"关键词表达尚未确认；记录的观察：{check.evidence}")
            add(("keyword_alignment",), _message(
                f"keyword_id={check.keyword_id}（{keyword_names[check.keyword_id]}）", doubt,
                "能够支持该原始关键词的可辨认图像观察",
                "A1 对照原始关键词核实当前成品，取得可辨认证据后重新审核，再决定是否调整 A2/A3",
            ))

    constraint_names = {item.constraint_id: item.name for item in audit.user_constraints}
    for check in constraints:
        if check.status == "unknown" and check.constraint_id not in missing_literals:
            doubt = ("未取得有效观察，该用户约束尚未完成核实"
                     if observation is None else f"用户约束是否满足尚未确认；记录的观察：{check.evidence}")
            add(("user_constraints",), _message(
                f"constraint_id={check.constraint_id}（{constraint_names[check.constraint_id]}）", doubt,
                "能够核对该原始约束的可靠图像观察或固定文字转录",
                "A1 保留原始约束，核实对应成品证据后重新审核，再决定需要哪个模块修改",
            ))

    for check in texts:
        unknown_checks: list[CheckId] = []
        if check.status == "unknown":
            unknown_checks.append("text_accuracy")
        if check.readability == "unknown":
            unknown_checks.append("text_readability")
        if not unknown_checks:
            continue
        pending = "、".join(
            "文字准确性" if check_id == "text_accuracy" else "正常观看时的可读性"
            for check_id in unknown_checks
        )
        if observation is None:
            doubt = f"未取得有效观察，该文案的{pending}检查尚未完成"
        elif check.observed_text is None:
            doubt = f"文字无法可靠转录，尚不能判断{pending}"
        else:
            doubt = f"已记录转录 {check.observed_text!r}，但{pending}检查尚未确认"
        add(tuple(unknown_checks), _message(
            f"copy_id={check.copy_id} / {'、'.join(unknown_checks)}", doubt,
            f"能够核对当前成品{pending}的可靠文字观察",
            "A1 核对 A2/A3 的文字参数与当前实际渲染，取得可读证据后重新审核，再判断是否需要改文案或渲染",
        ))

    if observation is None:
        visual_unknowns: tuple[tuple[CheckId, str], ...] = (
            ("text_readability", "文字可读性"), ("layout_quality", "视觉布局质量"),
        )
        for check_id, name in visual_unknowns:
            add((check_id,), _message(
                check_id, f"未取得有效观察，{name}检查尚未完成",
                "当前成品的有效图像观察",
                "A1 核实审核调用与候选图片，恢复有效观察后重新审核",
            ))
    else:
        for check in observation.visual_checks:
            if check.status == "unknown":
                add((check.check_id,), _message(
                    check.check_id, f"该视觉检查尚未确认；记录的观察：{check.evidence}",
                    "可核对的成品视觉证据；未确认元素位置时不指定位置",
                    "A1 核实当前成品与该疑点，取得可辨认证据后重新审核，再决定是否安排 A2/A3 修改",
                ))

    enriched = tuple(
        item.model_copy(update={"reason": "\n".join(dict.fromkeys(reasons[item.check_id]))})
        if item.check_id in reasons else item
        for item in coverage
    )
    return enriched, tuple(dict.fromkeys(uncertainties))
