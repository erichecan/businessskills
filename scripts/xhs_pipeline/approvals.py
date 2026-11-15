"""Integrity-checked approvals for PoStory XHS draft packages."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def package_digest(package: Path, note: dict) -> str:
    files = [package / "note.json", package / "caption.txt"]
    files.extend(package / name for name in note.get("card_files", []))
    if (package / "auto-review.json").is_file():
        files.append(package / "auto-review.json")
    digest = hashlib.sha256()
    for path in sorted((item.resolve() for item in files), key=str):
        if not path.is_relative_to(package.resolve()) or not path.is_file():
            raise RuntimeError(f"审批稿件缺少文件或路径越界：{path}")
        digest.update(str(path.relative_to(package.resolve())).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def approve_package(package: Path, approved_by: str) -> Path:
    package = package.expanduser().resolve()
    note_path = package / "note.json"
    if not note_path.is_file() or not (package / "caption.txt").is_file():
        raise RuntimeError("稿件包必须包含 note.json 和 caption.txt")
    note = json.loads(note_path.read_text(encoding="utf-8"))
    if note.get("pipeline_id") != "postory-xhs" or note.get("status") != "needs_human_review":
        raise RuntimeError("仅可人工补批 PoStory 小红书待处理稿件")
    if not approved_by.strip():
        raise RuntimeError("审批人不能为空")
    record = {
        "status": "approved",
        "approved_by": approved_by.strip(),
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "package_sha256": package_digest(package, note),
    }
    approval_path = package / "approval.json"
    try:
        with approval_path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise RuntimeError("该稿件已有审批记录；请勿覆盖或重复审批") from exc
    print(f"✅ 已记录人工补批：{package}")
    return approval_path


def record_auto_review(package: Path, note: dict, report: dict) -> dict:
    """Persist a fail-closed automated review and approve only a clean package."""
    package = package.resolve()
    passed = (
        report.get("decision") == "approve"
        and isinstance(report.get("checks"), dict)
        and set(report["checks"].values()) == {"pass"}
        and not report.get("reasons")
    )
    report_path = package / "auto-review.json"
    report_record = {
        "reviewer": "postory-auto-review-v1",
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **report,
        "decision": "approve" if passed else "hold",
    }
    report_path.write_text(json.dumps(report_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        return {"status": "hold", "review": report_record}

    note["status"] = "auto_approved"
    note["auto_review"] = {
        "reviewer": report_record["reviewer"],
        "reviewed_at": report_record["reviewed_at"],
    }
    (package / "note.json").write_text(json.dumps(note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    approval = {
        "status": "approved",
        "approval_type": "automated",
        "approved_by": report_record["reviewer"],
        "approved_at": report_record["reviewed_at"],
        "package_sha256": package_digest(package, note),
        "review_file": report_path.name,
    }
    approval_path = package / "approval.json"
    try:
        with approval_path.open("x", encoding="utf-8") as stream:
            json.dump(approval, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise RuntimeError("稿件已有审批记录；自动审核拒绝覆盖") from exc
    return {"status": "approved", "review": report_record}


def require_approval(package: Path, note: dict) -> dict:
    approval_path = package / "approval.json"
    if not approval_path.is_file():
        raise RuntimeError("稿件没有有效审批记录；请确认自动审稿通过，或运行 approve 人工补批")
    try:
        record = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("稿件审批记录无效") from exc
    if (record.get("status") != "approved" or not record.get("approved_by")
            or record.get("package_sha256") != package_digest(package, note)):
        raise RuntimeError("人工审批缺失或审批后的稿件内容已发生变化")
    return record
