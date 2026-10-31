import tempfile
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import Callable, Dict, List

from ..analyzers.base_analyzers import run_base_analyzers
from ..analyzers.kazakh_analyzers import run_kazakh_analyzers

router = APIRouter()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


async def _run_analyzer(
    ru: UploadFile,
    kk: UploadFile,
    analyzer: Callable[[str, str], List[Dict]],
) -> JSONResponse:
    with tempfile.TemporaryDirectory(prefix="qazaq-loc-qa-") as temp_dir:
        paths = []
        for upload, filename in ((ru, "ru.arb"), (kk, "kk.arb")):
            content = await upload.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Uploaded file exceeds the 10 MB limit")
            path = Path(temp_dir) / filename
            path.write_bytes(content)
            paths.append(str(path))
        findings = analyzer(paths[0], paths[1])
    return JSONResponse(content={"findings": findings})

@router.post("/analyze/base", summary="Run basic ARB analysis (untranslated, placeholders, etc.)")
async def analyze_base(
    ru: UploadFile = File(..., description="Русский файл intl_ru.arb"),
    kk: UploadFile = File(..., description="Казахский файл intl_kk.arb"),
) -> JSONResponse:
    return await _run_analyzer(ru, kk, run_base_analyzers)

@router.post("/analyze/kazakh", summary="Run Kazakh‑specific analysis (suffix harmony, orthography, etc.)")
async def analyze_kazakh(
    ru: UploadFile = File(..., description="Русский файл intl_ru.arb"),
    kk: UploadFile = File(..., description="Казахский файл intl_kk.arb"),
) -> JSONResponse:
    return await _run_analyzer(ru, kk, run_kazakh_analyzers)
