"""
Qazaq Loc QA – FastAPI Backend
Supports: upload, analyze (11 analyzers), fixes (accept/reject/manual), apply, undo,
stats, report, glossary, file download, export (markdown/csv), and web UI.
State is persisted to state/ on every mutation so it survives restarts.
"""
import hashlib
import io
import csv
import shutil
import uuid
import datetime
import threading
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal

from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, HTMLResponse
from fastapi import Response
from pydantic import BaseModel
from openpyxl import Workbook

from qazaq_loc_qa.backend.utils.arb_parser import ARBFile
from qazaq_loc_qa.backend.api.state import load_analyses, save_analyses
from qazaq_loc_qa.backend.fixes.engine import validate_fix
from qazaq_loc_qa.backend.analysis.orchestrator import run_all_analyzers
from qazaq_loc_qa.backend.ai.config_store import AIConfigStore
from qazaq_loc_qa.backend.ai.review import (
    MAX_AI_BATCH_SIZE,
    MAX_AI_REVIEW_ITEMS,
    start_review_job,
    get_job,
    stop_job,
)

app = FastAPI(title="Qazaq Loc QA Backend API", version="0.1.0")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    # Avoid leaking stack traces to the client; log to stderr.
    import sys
    print(f"[QazaqLocQA] Unhandled error on {request.url}: {exc!r}", file=sys.stderr)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
BACKUP_DIR = BASE_DIR / "state" / "backups"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Safety limits
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB per file
ALLOWED_EXTENSIONS = {".arb", ".json"}
MAX_FILES_PER_UPLOAD = 20
_STATE_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# In-memory store – restored from disk on startup
# ---------------------------------------------------------------------------
analyses_runtime: Dict[str, Dict[str, Any]] = {}

def _restore_runtime() -> None:
    """Re-load ARBFile objects from uploaded files for every persisted analysis."""
    raw = load_analyses()
    interrupted_jobs = False
    with _STATE_LOCK:
        for aid, data in raw.items():
            files: Dict[str, ARBFile] = {}
            restore_errors = []
            for fid in data.get("file_ids", []):
                if not fid or "/" in fid or "\\" in fid or ".." in fid:
                    restore_errors.append(f"Skipped unsafe persisted file id: {fid!r}")
                    continue
                fixed_p = UPLOAD_DIR / f"fixed_{fid}"
                orig_p = UPLOAD_DIR / fid
                p_to_load = fixed_p if fixed_p.is_file() else orig_p
                if p_to_load.is_file():
                    try:
                        files[fid] = ARBFile.load(p_to_load)
                    except Exception as exc:
                        restore_errors.append(f"Could not restore ARB file {fid!r}: {exc}")
                else:
                    restore_errors.append(f"Persisted ARB file is missing: {fid!r}")
            ai_job_status = data.get("ai_job_status")
            ai_job_errors = data.get("ai_job_errors", [])
            if not isinstance(ai_job_errors, list):
                ai_job_errors = []
            if ai_job_status == "running":
                ai_job_status = "stopped"
                ai_job_errors = ai_job_errors + [
                    "AI review was interrupted by server restart; completed findings were retained."
                ]
                interrupted_jobs = True
            analyses_runtime[aid] = {
                "file_ids": data.get("file_ids", []),
                "source_file_id": data.get("source_file_id"),
                "target_file_id": data.get("target_file_id"),
                "files": files,
                "errors": data.get("errors", []),
                "restore_errors": restore_errors,
                "fixes": data.get("fixes", {}),
                "history": data.get("history", []),
                "ai_findings": data.get("ai_findings", []),
                "ai_decisions": data.get("ai_decisions", {}),
                "ai_job_id": data.get("ai_job_id"),
                "ai_job_status": ai_job_status,
                "ai_job_processed": data.get("ai_job_processed", 0),
                "ai_job_total": data.get("ai_job_total", 0),
                "ai_job_errors": ai_job_errors,
                "ai_job_usage": data.get("ai_job_usage"),
            }
    if interrupted_jobs or any(data.get("restore_errors") for data in analyses_runtime.values()):
        _persist()


def _persist() -> None:
    """Serialise the current state to disk."""
    with _STATE_LOCK:
        out: Dict[str, Any] = {}
        for aid, data in analyses_runtime.items():
            out[aid] = {
                "file_ids": data.get("file_ids", []),
                "source_file_id": data.get("source_file_id"),
                "target_file_id": data.get("target_file_id"),
                "errors": data.get("errors", []),
                "restore_errors": data.get("restore_errors", []),
                "fixes": data.get("fixes", {}),
                "history": data.get("history", []),
                "ai_findings": data.get("ai_findings", []),
                "ai_decisions": data.get("ai_decisions", {}),
                "ai_job_id": data.get("ai_job_id"),
                "ai_job_status": data.get("ai_job_status"),
                "ai_job_processed": data.get("ai_job_processed", 0),
                "ai_job_total": data.get("ai_job_total", 0),
                "ai_job_errors": data.get("ai_job_errors", []),
                "ai_job_usage": data.get("ai_job_usage"),
            }
        save_analyses(out)


_restore_runtime()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _spreadsheet_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _export_findings(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = list(data.get("errors", []))
    for finding in data.get("ai_findings", []):
        findings.append({
            "key": finding.get("key", ""),
            "category": f"ai_{finding.get('category') or 'review'}",
            "confidence": finding.get("confidence", ""),
            "source": finding.get("source", ""),
            "current": finding.get("current_translation", ""),
            "suggestion": finding.get("suggested_translation", ""),
            "explanation": finding.get("explanation", ""),
        })
    return findings


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class UploadResponse(BaseModel):
    file_ids: List[str]
    message: str = "Files uploaded successfully"


class FileInfo(BaseModel):
    file_id: str
    filename: str


class CompareResponse(BaseModel):
    only_in_file1: List[str]
    only_in_file2: List[str]
    common_keys: List[str]


class AnalyzeRequest(BaseModel):
    file_ids: List[str]
    source_file_id: Optional[str] = None
    target_file_id: Optional[str] = None
    category_filter: Optional[List[str]] = None


class ErrorItem(BaseModel):
    key: str
    category: str
    error_type: Optional[str] = None
    description: Optional[str] = None
    explanation: Optional[str] = None
    source: Optional[str] = None
    original: Optional[str] = None
    current: Optional[str] = None
    suggestion: Optional[str] = None
    confidence: Optional[str] = None


class FixAction(BaseModel):
    key: str
    action: Literal["accept", "reject", "manual"]
    new_value: Optional[str] = None


class StatsResponse(BaseModel):
    total_keys: int
    error_count: int
    accepted_fixes: int
    rejected_fixes: int
    pending_fixes: int = 0
    categories: Dict[str, int] = {}
    confidence: Dict[str, int] = {}
    source_file_id: Optional[str] = None
    target_file_id: Optional[str] = None


class GlossaryTerm(BaseModel):
    ru_term: str
    kk: str
    variants: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _safe_basename(name: Optional[str]) -> str:
    if not name:
        return "upload.arb"
    # Strip any path components / traversal attempts
    name = name.replace("\\", "/").split("/")[-1]
    name = name.strip()
    if not name or name in (".", ".."):
        name = "upload.arb"
    # Remove dangerous characters
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    p = Path(name)
    if p.suffix == "":
        name = name + ".arb"
    elif p.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file extension (allowed: .arb, .json)")
    return name


def _store_uploaded_file(upload: UploadFile) -> str:
    safe_name = _safe_basename(upload.filename)
    file_id = f"{uuid.uuid4().hex}_{safe_name}"
    dest = UPLOAD_DIR / file_id
    # Path traversal guard
    if not str(dest.resolve()).startswith(str(UPLOAD_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file path")
    size = 0
    with dest.open("wb") as out_file:
        while True:
            chunk = upload.file.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out_file.close()
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large: exceeds {MAX_UPLOAD_BYTES} bytes limit",
                )
            out_file.write(chunk)
    # Validate UTF-8 content
    try:
        dest.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Invalid UTF-8 in file '{upload.filename}': {exc}")
    # Validate extension/content: must look like ARB/JSON-ish
    if dest.suffix.lower() not in ALLOWED_EXTENSIONS:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Unsupported file extension")
    return file_id


def _validate_file_id(file_id: str) -> str:
    if not file_id or "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="Invalid file id")
    # Must be under UPLOAD_DIR
    path = (UPLOAD_DIR / file_id).resolve()
    if not str(path).startswith(str(UPLOAD_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file id")
    return file_id


def _load_arb(file_id: str) -> ARBFile:
    file_id = _validate_file_id(file_id)
    path = UPLOAD_DIR / file_id
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    try:
        arb = ARBFile.load(path)
    except Exception as exc:  # corrupted encoding / unreadable file
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ARB file '{file_id}': {exc}",
        ) from exc
    if len(arb) == 0:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or empty ARB file '{file_id}': no key/value pairs found.",
        )
    return arb


def _get_analysis(analysis_id: str) -> Dict[str, Any]:
    data = analyses_runtime.get(analysis_id)
    if not data:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/upload", response_model=UploadResponse)
async def upload_files(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES_PER_UPLOAD})")
    file_ids = []
    for f in files:
        try:
            file_ids.append(_store_uploaded_file(f))
        except HTTPException:
            # cleanup already handled inside helper
            raise
    return UploadResponse(file_ids=file_ids)


@app.get("/files", response_model=List[FileInfo])
async def list_files():
    result = []
    for p in UPLOAD_DIR.iterdir():
        if p.is_file() and not p.name.startswith("fixed_"):
            _validate_file_id(p.name)
            parts = p.name.split("_", 1)
            result.append(FileInfo(file_id=p.name, filename=parts[1] if len(parts) > 1 else p.name))
    return result


@app.get("/compare", response_model=CompareResponse)
async def compare_files(file1: str, file2: str):
    arb1 = _load_arb(file1)
    arb2 = _load_arb(file2)
    keys1 = set(arb1.keys())
    keys2 = set(arb2.keys())
    return CompareResponse(
        only_in_file1=sorted(keys1 - keys2),
        only_in_file2=sorted(keys2 - keys1),
        common_keys=sorted(keys1 & keys2),
    )


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    if not request.file_ids:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(set(request.file_ids)) != len(request.file_ids):
        raise HTTPException(status_code=400, detail="Duplicate file ids are not allowed")

    arb_files: Dict[str, ARBFile] = {}
    for fid in request.file_ids:
        arb_files[fid] = _load_arb(fid)

    errors: List[Dict[str, Any]] = []

    # Identify source and target ARB files
    ru_fid = request.source_file_id
    kk_fid = request.target_file_id

    if not ru_fid:
        ru_fid = next((fid for fid in request.file_ids if "ru" in fid.lower()), None)
    if not kk_fid:
        kk_fid = next(
            (fid for fid in request.file_ids
             if any(tag in fid.lower() for tag in ("kk", "kz", "uz", "en"))),
            None,
        )

    # Fallback if no language tags / explicit selection
    if not ru_fid and len(request.file_ids) >= 2:
        ru_fid = request.file_ids[0]
    if not kk_fid:
        kk_fid = next((fid for fid in request.file_ids if fid != ru_fid), None)
    if not kk_fid and len(request.file_ids) == 1:
        kk_fid = request.file_ids[0]

    if request.source_file_id and request.source_file_id not in arb_files:
        raise HTTPException(status_code=400, detail="Source file must be included in file_ids")
    if request.target_file_id and request.target_file_id not in arb_files:
        raise HTTPException(status_code=400, detail="Target file must be included in file_ids")
    if ru_fid and ru_fid == kk_fid:
        if (
            len(request.file_ids) == 1
            and request.source_file_id is None
            and request.target_file_id is None
        ):
            ru_fid = None
        else:
            raise HTTPException(status_code=400, detail="Source and target must be different files")

    if ru_fid and kk_fid and ru_fid in arb_files and kk_fid in arb_files:
        ru_arb = arb_files[ru_fid]
        kk_arb = arb_files[kk_fid]
        errors = run_all_analyzers(ru_arb, kk_arb)
    elif kk_fid and kk_fid in arb_files:
        # Run analyzers where single file is checked or with empty source
        kk_arb = arb_files[kk_fid]
        empty_arb = ARBFile([])
        errors = run_all_analyzers(empty_arb, kk_arb)

    if request.category_filter:
        filter_set = set(request.category_filter)
        errors = [e for e in errors if e.get("category") in filter_set]

    analysis_id = uuid.uuid4().hex
    analyses_runtime[analysis_id] = {
        "file_ids": list(request.file_ids),
        "source_file_id": ru_fid,
        "target_file_id": kk_fid,
        "files": arb_files,
        "errors": errors,
        "fixes": {},
        "history": [],
    }
    _persist()
    return {"analysis_id": analysis_id, "file_ids": request.file_ids, "errors": errors}


@app.get("/analysis/{analysis_id}/errors")
async def get_errors(
    analysis_id: str,
    category: Optional[str] = Query(None, description="Filter by category"),
    confidence: Optional[str] = Query(None, description="Filter by confidence (high/medium/low)")
):
    data = _get_analysis(analysis_id)
    errs = data["errors"]
    if category:
        errs = [e for e in errs if e.get("category") == category]
    if confidence:
        errs = [e for e in errs if e.get("confidence") == confidence]
    return errs


@app.post("/analysis/{analysis_id}/fixes")
async def submit_fixes(analysis_id: str, fixes: List[FixAction]):
    with _STATE_LOCK:
        data = _get_analysis(analysis_id)
        target_arb = data.get("files", {}).get(data.get("target_file_id"))
        for fix in fixes:
            if target_arb is None or fix.key not in target_arb:
                raise HTTPException(status_code=422, detail=f"Fix key is not present in the target file: {fix.key}")
            if fix.action in ("accept", "manual") and fix.new_value is None:
                raise HTTPException(status_code=422, detail="A new_value is required for accepted or manual fixes")
            if fix.action in ("accept", "manual") and fix.new_value is not None:
                original_val: Optional[str] = None
                search_order = [
                    data.get("source_file_id"),
                    data.get("target_file_id"),
                ] + list(data["files"].keys())
                for fid in search_order:
                    if not fid:
                        continue
                    try:
                        _validate_file_id(fid)
                    except HTTPException:
                        continue
                    arb = data["files"].get(fid)
                    if arb is not None and fix.key in arb:
                        original_val = arb.get(fix.key)
                        break
                if original_val is not None and not validate_fix(original_val, fix.new_value):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Placeholder mismatch for key '{fix.key}': "
                               f"original has different placeholders than suggested fix."
                    )
            data["fixes"][fix.key] = {"action": fix.action, "new_value": fix.new_value}
        _persist()
    return {"message": "Fixes recorded", "count": len(fixes)}


def _fixable_file_ids(data: Dict[str, Any]) -> List[str]:
    """Return the file ids that may receive fixes.

    Fixes must only ever be applied to the *target* file.  The previous
    implementation iterated over every uploaded file, which could rewrite the
    Russian source (and any other language) with Kazakh fixes.
    """
    target = data.get("target_file_id")
    source = data.get("source_file_id")
    if target and target == source:
        return []
    if target and target in data.get("files", {}):
        return [target]
    candidates = [fid for fid in data.get("files", {}) if fid != source]
    if candidates:
        # Legacy analyses without an explicit target: pick exactly one
        # non-source file so that other languages are never modified.
        return [candidates[0]]
    ids = list(data.get("files", {}).keys())
    return ids[:1]


@app.post("/analysis/{analysis_id}/apply")
async def apply_fixes(analysis_id: str):
    with _STATE_LOCK:
        data = _get_analysis(analysis_id)
        applied_files: Dict[str, str] = {}

        file_ids = _fixable_file_ids(data)
        if not file_ids:
            raise HTTPException(status_code=409, detail="No safe target file is available for fixes")

        for file_id in file_ids:
            _validate_file_id(file_id)
            arb = data["files"][file_id]
            orig_path = UPLOAD_DIR / file_id
            fixed_path = UPLOAD_DIR / f"fixed_{file_id}"

            if fixed_path.is_file():
                backup_src = fixed_path
            else:
                backup_src = orig_path

            ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            backup_path = BACKUP_DIR / f"{analysis_id}_{file_id}_{ts}.arb"
            # Guard: backup path must stay inside BACKUP_DIR
            if not str(backup_path.resolve()).startswith(str(BACKUP_DIR.resolve())):
                raise HTTPException(status_code=400, detail="Invalid backup path")
            shutil.copy2(backup_src, backup_path)
            backup_hash = _sha256(backup_path)

            new_arb = ARBFile([e for e in arb.entries])
            for key, fix in data["fixes"].items():
                if fix["action"] in ("accept", "manual") and key in new_arb and fix["new_value"] is not None:
                    new_arb.set(key, fix["new_value"])

            new_arb.dump(fixed_path)
            data["files"][file_id] = new_arb

            history_entry = {
                "timestamp": ts,
                "description": f"Applied fixes to {file_id}",
                "file_id": file_id,
                "fixed_path": str(fixed_path),
                "snapshot_path": str(backup_path),
                "snapshot_hash": backup_hash,
            }
            data["history"].append(history_entry)
            applied_files[file_id] = str(fixed_path)

        _persist()
    return {"applied_files": applied_files}


@app.post("/analysis/{analysis_id}/undo")
async def undo_last(analysis_id: str):
    with _STATE_LOCK:
        data = _get_analysis(analysis_id)
        history = data.get("history", [])
        if not history:
            raise HTTPException(status_code=404, detail="No history to undo")

        entry = history[-1]
        backup_path = Path(entry["snapshot_path"])
        fixed_path = Path(entry["fixed_path"])
        stored_hash = entry["snapshot_hash"]
        file_id = entry.get("file_id")

        # Security: ensure paths stay within allowed directories
        try:
            if not str(backup_path.resolve()).startswith(str(BACKUP_DIR.resolve())):
                raise HTTPException(status_code=400, detail="Invalid backup path")
            if file_id is not None:
                _validate_file_id(file_id)
            # fixed_path must be inside UPLOAD_DIR
            if not str(fixed_path.resolve()).startswith(str(UPLOAD_DIR.resolve())):
                raise HTTPException(status_code=400, detail="Invalid fixed path")
        except HTTPException:
            raise

        if not backup_path.is_file():
            raise HTTPException(status_code=500, detail="Backup file missing - cannot undo")
        actual_hash = _sha256(backup_path)
        if actual_hash != stored_hash:
            raise HTTPException(status_code=500, detail="Backup file is corrupted (hash mismatch) - cannot undo")

        shutil.copy2(backup_path, fixed_path)

        if file_id and file_id in data["files"] and fixed_path.is_file():
            data["files"][file_id] = ARBFile.load(fixed_path)

        data["history"].pop()
        _persist()
    return {"restored": str(fixed_path), "from_backup": str(backup_path)}


@app.get("/analysis/{analysis_id}/stats", response_model=StatsResponse)
async def get_stats(analysis_id: str):
    data = _get_analysis(analysis_id)
    total_keys = sum(len(arb) for arb in data["files"].values())
    error_count = len(data["errors"])
    accepted = sum(1 for f in data["fixes"].values() if f["action"] in ("accept", "manual"))
    rejected = sum(1 for f in data["fixes"].values() if f["action"] == "reject")
    pending = max(0, error_count - accepted - rejected)

    cats: Dict[str, int] = {}
    confs: Dict[str, int] = {}
    for err in data["errors"]:
        c = err.get("category", "other")
        cats[c] = cats.get(c, 0) + 1
        cf = (err.get("confidence") or "medium").lower()
        confs[cf] = confs.get(cf, 0) + 1

    return StatsResponse(
        total_keys=total_keys,
        error_count=error_count,
        accepted_fixes=accepted,
        rejected_fixes=rejected,
        pending_fixes=pending,
        categories=cats,
        confidence=confs,
        source_file_id=data.get("source_file_id"),
        target_file_id=data.get("target_file_id"),
    )


@app.get("/analysis/{analysis_id}/report")
async def get_report(analysis_id: str):
    data = _get_analysis(analysis_id)
    categories: Dict[str, int] = {}
    for error in data["errors"]:
        cat = error.get("category", "other")
        categories[cat] = categories.get(cat, 0) + 1
    report = {
        "analysis_id": analysis_id,
        "file_ids": data.get("file_ids", []),
        "source_file_id": data.get("source_file_id"),
        "target_file_id": data.get("target_file_id"),
        "error_count": len(data["errors"]),
        "categories": categories,
        "errors": data["errors"],
        "restore_errors": data.get("restore_errors", []),
        "ai_findings": data.get("ai_findings", []),
        "ai_decisions": data.get("ai_decisions", {}),
        "ai_job_id": data.get("ai_job_id"),
        "ai_job_status": data.get("ai_job_status"),
        "ai_job_processed": data.get("ai_job_processed", 0),
        "ai_job_total": data.get("ai_job_total", 0),
        "ai_job_errors": data.get("ai_job_errors", []),
        "ai_job_usage": data.get("ai_job_usage"),
        "fixes": data["fixes"],
        "history": data.get("history", []),
    }
    return JSONResponse(report)


@app.get("/analysis/{analysis_id}/export/json")
async def export_json(analysis_id: str):
    """Full JSON export (same payload as /report, explicit export URL)."""
    return await get_report(analysis_id)


# ---------------------------------------------------------------------------
# Download & Export Endpoints
# ---------------------------------------------------------------------------
@app.get("/analysis/{analysis_id}/download/{file_id}")
async def download_fixed_arb(analysis_id: str, file_id: str):
    _get_analysis(analysis_id)
    file_id = _validate_file_id(file_id)
    fixed_path = UPLOAD_DIR / f"fixed_{file_id}"
    if not fixed_path.is_file():
        orig_path = UPLOAD_DIR / file_id
        if orig_path.is_file():
            fixed_path = orig_path
        else:
            raise HTTPException(status_code=404, detail="File not found")

    parts = file_id.split("_", 1)
    orig_name = parts[1] if len(parts) > 1 else file_id
    out_filename = f"fixed_{orig_name}"
    # ensure download path stays inside UPLOAD_DIR
    if not str(fixed_path.resolve()).startswith(str(UPLOAD_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return FileResponse(
        path=str(fixed_path),
        filename=out_filename,
        media_type="application/json"
    )


@app.get("/analysis/{analysis_id}/export/markdown")
async def export_markdown(analysis_id: str):
    data = _get_analysis(analysis_id)
    findings = _export_findings(data)
    lines = [
        f"# Qazaq Loc QA Report: Analysis {analysis_id}",
        f"**Date:** {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"**Files:** {', '.join(data.get('file_ids', []))}",
        f"**Total Findings:** {len(findings)}",
        "",
        "## Summary of Findings",
        "| Key | Category | Confidence | Original | Current | Suggestion |",
        "|---|---|---|---|---|---|",
    ]
    for err in findings:
        k = err.get("key", "").replace("|", "\\|")
        c = err.get("category", "")
        conf = err.get("confidence", "")
        orig = (err.get("source") or err.get("original") or "").replace("|", "\\|").replace("\n", " ")
        curr = (err.get("current") or "").replace("|", "\\|").replace("\n", " ")
        sug = (err.get("suggestion") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {k} | {c} | {conf} | {orig} | {curr} | {sug} |")

    lines.append("")
    lines.append("## Fixes Decisions")
    for key, fix in data.get("fixes", {}).items():
        lines.append(f"- **{key}**: `{fix.get('action')}` -> `{fix.get('new_value')}`")

    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


@app.get("/analysis/{analysis_id}/export/csv")
async def export_csv(analysis_id: str):
    data = _get_analysis(analysis_id)
    findings = _export_findings(data)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Key", "Category", "Confidence", "Original", "Current", "Suggestion", "Explanation"])

    def _flat(value) -> str:
        # Keep one CSV record per error: embedded newlines from ARB strings
        # would otherwise split a row across several physical lines.
        return str(value or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")

    for err in findings:
        writer.writerow([
            _spreadsheet_safe(_flat(err.get("key", ""))),
            _spreadsheet_safe(_flat(err.get("category", ""))),
            _spreadsheet_safe(_flat(err.get("confidence", ""))),
            _spreadsheet_safe(_flat(err.get("source") or err.get("original") or "")),
            _spreadsheet_safe(_flat(err.get("current", ""))),
            _spreadsheet_safe(_flat(err.get("suggestion", ""))),
            _spreadsheet_safe(_flat(err.get("explanation", ""))),
        ])
    return PlainTextResponse(output.getvalue(), media_type="text/csv")


@app.get("/analysis/{analysis_id}/export/excel")
async def export_excel(analysis_id: str):
    data = _get_analysis(analysis_id)
    findings = _export_findings(data)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Errors"
    header = ["Key", "Category", "Confidence", "Original", "Current", "Suggestion", "Explanation"]
    sheet.append(header)
    for err in findings:
        sheet.append([_spreadsheet_safe(value) for value in [
            err.get("key", ""),
            err.get("category", ""),
            err.get("confidence", ""),
            err.get("source") or err.get("original") or "",
            err.get("current") or "",
            err.get("suggestion") or "",
            err.get("explanation") or err.get("description") or "",
        ]])
    # Stats sheet
    stats_sheet = workbook.create_sheet("Summary")
    stats_sheet.append(["total_keys", "error_count"])
    stats_sheet.append([sum(len(arb) for arb in data.get("files", {}).values()), len(findings)])
    buf = io.BytesIO()
    workbook.save(buf)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="qazaq_loc_qa_report.xlsx"'},
    )



# ---------------------------------------------------------------------------
# AI Provider & Review Endpoints
# ---------------------------------------------------------------------------
_cfg_store = AIConfigStore()


@app.get("/ai/providers")
async def ai_list_providers():
    out = []
    for c in _cfg_store.list_configs():
        out.append({
            "id": c.get("id"),
            "type": c.get("type"),
            "name": c.get("name"),
            "base_url": c.get("base_url", ""),
            "model": c.get("model", ""),
            "enabled": c.get("enabled", True),
            "timeout": c.get("timeout", 30),
            "max_retries": c.get("max_retries", 2),
            "batch_size": c.get("batch_size", 8),
            "concurrency": c.get("concurrency", 2),
            "cost_per_1k_input": c.get("cost_per_1k_input", 0.0),
            "cost_per_1k_output": c.get("cost_per_1k_output", 0.0),
            "has_api_key": _cfg_store.has_secret(c.get("id")),
            "api_key_env": c.get("api_key_env"),
        })
    return out


@app.post("/ai/providers")
async def ai_add_provider(payload: Dict[str, Any]):
    cfg = {
        "id": payload.get("id"),
        "type": payload.get("type", "openai"),
        "name": payload.get("name", payload.get("type", "provider")),
        "base_url": payload.get("base_url", ""),
        "model": payload.get("model", ""),
        "enabled": payload.get("enabled", True),
        "timeout": int(payload.get("timeout", 30)),
        "max_retries": int(payload.get("max_retries", 2)),
        "batch_size": int(payload.get("batch_size", 8)),
        "concurrency": int(payload.get("concurrency", 2)),
        "cost_per_1k_input": float(payload.get("cost_per_1k_input", 0.0)),
        "cost_per_1k_output": float(payload.get("cost_per_1k_output", 0.0)),
        "api_key_env": payload.get("api_key_env"),
    }
    saved = _cfg_store.add_or_update(cfg)
    if payload.get("api_key"):
        _cfg_store.set_secret(saved["id"], payload["api_key"])
    return {"id": saved["id"], "message": "Provider saved"}


@app.delete("/ai/providers/{provider_id}")
async def ai_delete_provider(provider_id: str):
    if _cfg_store.delete(provider_id):
        return {"message": "Deleted"}
    raise HTTPException(status_code=404, detail="Provider not found")


@app.post("/ai/providers/{provider_id}/test")
async def ai_test_provider(provider_id: str):
    from qazaq_loc_qa.backend.ai.providers import create_provider, ProviderConfig
    cfg = _cfg_store.get(provider_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Provider not found")
    pcfg = ProviderConfig(
        id=cfg["id"], type=cfg["type"], name=cfg.get("name", cfg["type"]),
        base_url=cfg.get("base_url", ""), model=cfg.get("model", ""),
        enabled=cfg.get("enabled", True), timeout=cfg.get("timeout", 30),
        max_retries=cfg.get("max_retries", 2), batch_size=cfg.get("batch_size", 8),
        concurrency=cfg.get("concurrency", 2),
    )
    api_key = _cfg_store.get_secret(provider_id)
    if cfg.get("type") != "ollama" and not api_key:
        return {"ok": False, "message": "API key not configured"}
    provider = create_provider(pcfg, api_key=api_key)
    return provider.test_connection()


@app.post("/analysis/{analysis_id}/ai/review")
async def ai_start_review(analysis_id: str, payload: Dict[str, Any]):
    data = _get_analysis(analysis_id)
    provider_id = payload.get("provider_id")
    if not provider_id:
        raise HTTPException(status_code=400, detail="provider_id required")
    cfg = _cfg_store.get(provider_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Provider not found")
    if not cfg.get("enabled", True):
        raise HTTPException(status_code=400, detail="Provider disabled")
    try:
        max_items = int(payload.get("max_items", 50))
        batch_size = int(payload.get("batch_size", cfg.get("batch_size", 8)))
        budget_value = payload.get("max_tokens_budget")
        token_budget = int(budget_value) if budget_value is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="AI review limits must be integers") from exc
    if not 0 <= max_items <= MAX_AI_REVIEW_ITEMS:
        raise HTTPException(status_code=422, detail=f"max_items must be between 0 and {MAX_AI_REVIEW_ITEMS}")
    if not 1 <= batch_size <= MAX_AI_BATCH_SIZE:
        raise HTTPException(status_code=422, detail=f"batch_size must be between 1 and {MAX_AI_BATCH_SIZE}")
    if token_budget is not None and token_budget < 0:
        raise HTTPException(status_code=422, detail="max_tokens_budget cannot be negative")
    options = {
        "max_items": max_items,
        "batch_size": batch_size,
        "max_tokens_budget": token_budget,
    }
    job_id = start_review_job(analysis_id, provider_id, options, data)
    return {"job_id": job_id, "message": "AI review started"}


@app.get("/ai/review/{job_id}")
async def ai_review_status(job_id: str):
    job = get_job(job_id)
    if job:
        return {
            "job_id": job.job_id,
            "analysis_id": job.analysis_id,
            "status": job.status,
            "processed": job.processed,
            "total": job.total,
            "findings": job.findings,
            "errors": job.errors,
            "usage": job.usage,
        }
    # Fallback after restart: reconstruct from persisted analysis state
    for aid, data in analyses_runtime.items():
        if data.get("ai_job_id") == job_id:
            return {
                "job_id": job_id,
                "analysis_id": aid,
                "status": data.get("ai_job_status") or "unknown",
                "processed": data.get("ai_job_processed", len(data.get("ai_findings", []))),
                "total": data.get("ai_job_total", len(data.get("ai_findings", []))),
                "findings": data.get("ai_findings", []),
                "errors": data.get("ai_job_errors", []),
                "usage": data.get("ai_job_usage") or {},
            }
    raise HTTPException(status_code=404, detail="Job not found")


@app.post("/ai/review/{job_id}/stop")
async def ai_review_stop(job_id: str):
    if stop_job(job_id):
        return {"message": "Stop requested"}
    raise HTTPException(status_code=404, detail="Job not found")


@app.get("/analysis/{analysis_id}/ai/findings")
async def ai_get_findings(analysis_id: str):
    data = _get_analysis(analysis_id)
    return data.get("ai_findings", [])


@app.post("/analysis/{analysis_id}/ai/findings/decision")
async def ai_finding_decision(analysis_id: str, payload: Dict[str, Any]):
    with _STATE_LOCK:
        data = _get_analysis(analysis_id)
        key = payload.get("key")
        action = payload.get("action", "accept")
        new_value = payload.get("new_value")
        if action not in ("accept", "reject", "manual"):
            raise HTTPException(status_code=422, detail="Unsupported AI finding action")
        if action == "manual" and not isinstance(new_value, str):
            raise HTTPException(status_code=422, detail="A string new_value is required")
        findings = data.setdefault("ai_findings", [])
        target = None
        for f in findings:
            if f.get("key") == key:
                target = f
                break
        if target is None:
            raise HTTPException(status_code=404, detail="Finding not found")
        decision_value = None
        if action == "accept":
            val = new_value if new_value is not None else target.get("suggested_translation")
            if not isinstance(val, str):
                raise HTTPException(status_code=422, detail="Finding has no string suggestion to accept")
            target_arb = data.get("files", {}).get(data.get("target_file_id"))
            if target_arb is None or key not in target_arb:
                raise HTTPException(status_code=422, detail="Finding key is not present in the target file")
            # placeholder validation
            original_val = None
            source_fid = data.get("source_file_id")
            target_fid = data.get("target_file_id")
            for fid in ([source_fid, target_fid] + list(data.get("files", {}).keys())):
                if not fid:
                    continue
                arb = data["files"].get(fid)
                if arb is not None and key in arb:
                    original_val = arb.get(key)
                    break
            if original_val is not None and val is not None:
                from qazaq_loc_qa.backend.fixes.engine import validate_fix
                if not validate_fix(original_val, val):
                    raise HTTPException(status_code=422, detail="Placeholder mismatch")
            data.setdefault("fixes", {})[key] = {"action": "accept", "new_value": val}
            decision_value = val
            target["review_status"] = "accepted"
        elif action == "reject":
            target["review_status"] = "rejected"
        elif action == "manual":
            target_arb = data.get("files", {}).get(data.get("target_file_id"))
            if target_arb is None or key not in target_arb:
                raise HTTPException(status_code=422, detail="Finding key is not present in the target file")
            original_val = target_arb.get(key) or ""
            source_arb = data.get("files", {}).get(data.get("source_file_id"))
            if source_arb is not None and key in source_arb:
                original_val = source_arb.get(key) or ""
            if not validate_fix(original_val, new_value):
                raise HTTPException(status_code=422, detail="Placeholder mismatch")
            data.setdefault("fixes", {})[key] = {"action": "manual", "new_value": new_value}
            decision_value = new_value
            target["review_status"] = "accepted"
        data.setdefault("ai_decisions", {})[key] = {"action": action, "new_value": decision_value}
        _persist()
    return {"message": "Decision recorded"}


@app.get("/ai/usage")
async def ai_usage_summary():
    # aggregate from jobs in memory
    from qazaq_loc_qa.backend.ai.review import JOBS
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "estimated_cost": 0.0, "jobs": len(JOBS)}
    for j in JOBS.values():
        u = j.usage
        total["prompt_tokens"] += u.get("prompt_tokens", 0)
        total["completion_tokens"] += u.get("completion_tokens", 0)
        total["total_tokens"] += u.get("total_tokens", 0)
        total["estimated_cost"] += u.get("estimated_cost", 0.0)
    return total

# ---------------------------------------------------------------------------
# Glossary Endpoints
# ---------------------------------------------------------------------------
@app.get("/glossary")
async def get_glossary():
    from qazaq_loc_qa.backend.glossary.service import glossary_service
    return glossary_service.get_all_terms()


@app.post("/glossary")
async def add_glossary_term(term: GlossaryTerm):
    from qazaq_loc_qa.backend.glossary.service import glossary_service
    glossary_service.add_term(term.ru_term, term.kk, term.variants)
    return {"message": "Term added", "ru_term": term.ru_term}


@app.delete("/glossary/{ru_term}")
async def delete_glossary_term(ru_term: str):
    from qazaq_loc_qa.backend.glossary.service import glossary_service
    if glossary_service.delete_term(ru_term):
        return {"message": "Term deleted", "ru_term": ru_term}
    raise HTTPException(status_code=404, detail="Term not found")


# ---------------------------------------------------------------------------
# Web UI Endpoint (Full SPA)
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
async def serve_ui():
    ui_html_path = STATIC_DIR / "index.html"
    if ui_html_path.is_file():
        return HTMLResponse(content=ui_html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Qazaq Loc QA API is running. UI file missing.</h1>", status_code=200)
