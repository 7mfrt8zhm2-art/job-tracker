#!/usr/bin/env python3
"""
채용 공고 트래커 자동 관리 및 동기화 스크립트 (manage_jobs.py)
- 중복 공고 자동 판별 (회사명 + 직무명 또는 공고 URL 기준)
- Firebase 실시간 DB(RTDB) 최신 상태(삭제/숨김/상태변경) 보존 및 동기화
- jobs_backup.json 및 job_tracker.md 자동 갱신
"""
import json
import os
import re
import sys
import urllib.request

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_JSON_PATH = os.path.join(REPO_DIR, "jobs_backup.json")
MARKDOWN_PATH = os.path.join(REPO_DIR, "job_tracker.md")
FIREBASE_BASE_URL = "https://job-tracker-99cf9-default-rtdb.firebaseio.com"


def normalize_str(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def is_same_job(a: dict, b: dict) -> bool:
    if not a or not b:
        return False
    if a.get("id") and b.get("id") and int(a["id"]) == int(b["id"]):
        return True
    comp_a, pos_a = normalize_str(a.get("company")), normalize_str(a.get("position"))
    comp_b, pos_b = normalize_str(b.get("company")), normalize_str(b.get("position"))
    if comp_a and pos_a and comp_a == comp_b and pos_a == pos_b:
        return True
    url_a = str(a.get("url") or "").strip().rstrip("/")
    url_b = str(b.get("url") or "").strip().rstrip("/")
    if url_a and url_b and url_a == url_b and comp_a == comp_b:
        return True
    return False


def load_backup_jobs() -> list:
    if os.path.exists(BACKUP_JSON_PATH):
        with open(BACKUP_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [j for j in data if j]
    return []


def fetch_firebase_state():
    """네트워크가 허용된 환경에서 Firebase RTDB의 최신 상태와 삭제 ID 목록을 가져옴."""
    try:
        req_jobs = urllib.request.Request(f"{FIREBASE_BASE_URL}/jobPostings.json")
        raw_jobs = json.loads(urllib.request.urlopen(req_jobs, timeout=5).read().decode("utf-8"))
        jobs = [j for j in (raw_jobs if isinstance(raw_jobs, list) else (raw_jobs or {}).values()) if j]

        req_del = urllib.request.Request(f"{FIREBASE_BASE_URL}/deletedJobIds.json")
        raw_del = json.loads(urllib.request.urlopen(req_del, timeout=5).read().decode("utf-8"))
        del_ids = set(int(x) for x in (raw_del if isinstance(raw_del, list) else (raw_del or {}).values()) if x is not None)
        return jobs, del_ids, True
    except Exception:
        return None, set(), False


def push_firebase_state(jobs: list, max_id: int):
    """네트워크가 허용된 환경에서 Firebase RTDB에 직접 최신 공고 목록을 반영함."""
    try:
        data_bytes = json.dumps(jobs, ensure_ascii=False).encode("utf-8")
        put_req = urllib.request.Request(
            f"{FIREBASE_BASE_URL}/jobPostings.json",
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        urllib.request.urlopen(put_req, timeout=5)

        max_bytes = json.dumps(max_id).encode("utf-8")
        max_req = urllib.request.Request(
            f"{FIREBASE_BASE_URL}/syncedMaxId.json",
            data=max_bytes,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        urllib.request.urlopen(max_req, timeout=5)
        return True
    except Exception:
        return False


def regenerate_markdown(jobs: list):
    """jobs_backup.json 기준으로 job_tracker.md 문서를 최신화함."""
    lines = [
        "# 채용 공고 트래커 요약 노트\n",
        "- 실시간 웹사이트: [채용 공고 트래커](https://teal-dango-51cabe.netlify.app)\n",
    ]
    for j in jobs:
        if not j or j.get("deleted"):
            continue
        status_icon = "🟢" if j.get("status") == "지원완료" else ("⚪" if j.get("status") == "마감_불합격" else "🟡")
        tasks_md = "\n".join([f"  - {t}" for t in j.get("tasks", [])])
        reqs_md = "\n".join([f"  - {r}" for r in j.get("requirements", [])])
        prefs_md = "\n".join([f"  - {p}" for p in j.get("preferences", [])])
        src_link = f" (출처: [포털 링크]({j['sourceUrl']}))" if j.get("sourceUrl") and j.get("sourceUrl") != j.get("url") else ""

        section = f"""---

### {j["id"]}. {j["company"]} - {j["position"]}
* **상태**: {status_icon} {j.get("status", "서류준비중")}
* **부서/분야**: {j.get("department", "")}
* **근무지/형태**: {j.get("location", "")} | {j.get("employmentType", "")}
* **접수 마감**: **{j.get("deadline", "채용시 마감")}**
* **등록일**: {j.get("addedDate", "")}
* **주요 업무**:
{tasks_md}
* **자격 요건(필수)**:
{reqs_md}
* **우대 사항**:
{prefs_md}
* **전형 절차**: {j.get("process", "")}
* **공고 링크**: [{j["company"]} 공식 채용 사이트]({j.get("url", "")}){src_link}
"""
        lines.append(section)

    with open(MARKDOWN_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def upsert_jobs(new_jobs_input: list):
    backup_jobs = load_backup_jobs()
    fb_jobs, deleted_ids, fb_online = fetch_firebase_state()

    base_jobs = fb_jobs if (fb_online and fb_jobs) else backup_jobs
    base_jobs = [j for j in base_jobs if j and not j.get("deleted") and int(j.get("id", 0)) not in deleted_ids]

    max_id = max([0] + [int(j.get("id", 0)) for j in backup_jobs] + [int(j.get("id", 0)) for j in base_jobs])

    for new_job in new_jobs_input:
        matched_idx = -1
        for idx, existing in enumerate(base_jobs):
            if is_same_job(existing, new_job):
                matched_idx = idx
                break

        if matched_idx >= 0:
            # 기존 공고 업데이트 (사용자의 상태, 숨김, 수동변경 플래그, 고유 ID는 보존)
            existing = base_jobs[matched_idx]
            preserved_id = existing["id"]
            preserved_status = existing.get("status", "서류준비중")
            preserved_hidden = existing.get("hidden", False)
            preserved_override = existing.get("manualStatusOverride", False)

            updated = dict(existing)
            updated.update(new_job)
            updated["id"] = preserved_id
            updated["status"] = preserved_status
            updated["hidden"] = preserved_hidden
            if preserved_override:
                updated["manualStatusOverride"] = True
            base_jobs[matched_idx] = updated
            print(f"[UPDATED] Job #{preserved_id}: {updated['company']} - {updated['position']}")
        else:
            max_id += 1
            new_entry = dict(new_job)
            new_entry["id"] = max_id
            new_entry.setdefault("status", "서류준비중")
            new_entry.setdefault("hidden", False)
            base_jobs.append(new_entry)
            print(f"[ADDED] Job #{max_id}: {new_entry['company']} - {new_entry['position']}")

    with open(BACKUP_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(base_jobs, f, ensure_ascii=False, indent=2)

    regenerate_markdown(base_jobs)
    push_firebase_state(base_jobs, max_id)
    print(f"Saved {len(base_jobs)} jobs to {BACKUP_JSON_PATH} and {MARKDOWN_PATH}.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            payload = [payload]
        upsert_jobs(payload)
    else:
        # 인자 없이 실행하면 현재 백업 JSON과 마크다운을 정돈 및 동기화
        upsert_jobs([])

# Mirror permanent repo ($HOME/job-tracker) to /tmp/job-tracker for MCP git server compatibility
import shutil
TMP_REPO_DIR = "/tmp/job-tracker"
if os.path.abspath(REPO_DIR) != os.path.abspath(TMP_REPO_DIR):
    if os.path.exists(TMP_REPO_DIR):
        shutil.rmtree(TMP_REPO_DIR)
    shutil.copytree(REPO_DIR, TMP_REPO_DIR)
    print(f"Mirrored permanent repository to {TMP_REPO_DIR} for Git MCP sync.")
