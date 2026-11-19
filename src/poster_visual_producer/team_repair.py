"""Choose the owner of an observed production defect."""
def repair_action(report):
    if report.get("decision") != "fail":
        return None
    issues = [i for i in report.get("issues", []) if i.get("severity") in {"blocking", "major"}]
    codes = {i["code"].lower() for i in issues}
    if codes & {"copy_too_long", "brief_conflict", "copy_content_wrong", "creative_concept_invalid"}:
        return "revise_brief"
    if codes & {"render_corrupt", "render_incomplete", "compositing_error"}:
        return "rerender"
    if any(k["status"] == "fail" for k in report.get("keyword_checks", [])) or "keyword_missing" in codes:
        return "regenerate_asset"
    if any(t.get("status") == "fail" or t.get("readability") == "fail" for t in report.get("text_checks", [])):
        return "revise_layout"
    if any(any(term in code for term in ("spacing", "overlap", "contrast", "layout", "text_mismatch", "unreadable", "clipping")) for code in codes):
        return "revise_layout"
    if any(c["status"] == "fail" for c in report.get("constraint_checks", [])):
        return "revise_brief"
    return None


def asset_constraints(feedback):
    keywords = {k["keyword_id"]: k["text"] for k in feedback["original_request"]["keywords"]}
    missing = [keywords[k["keyword_id"]] for k in feedback["report"]["keyword_checks"]
               if k["status"] == "fail" and k["keyword_id"] in keywords]
    if missing:
        return ["Make these subjects unmistakable in the foreground: " + ", ".join(missing)[:880]]
    return ["Give the main subject a clear silhouette and a prominent foreground position."]
