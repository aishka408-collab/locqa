"""Qazaq Loc QA Main Entrypoint."""
import uvicorn
from qazaq_loc_qa.backend.api.main import app

if __name__ == "__main__":
    uvicorn.run("qazaq_loc_qa.backend.main:app", host="127.0.0.1", port=8000, reload=True)
