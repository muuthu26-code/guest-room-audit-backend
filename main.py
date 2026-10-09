import sqlite3
import json
import time
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io

app = FastAPI(title="Guest Room Audit API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "guest_room_audits.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audits (
                id TEXT PRIMARY KEY,
                room_no TEXT,
                auditor TEXT,
                audit_date TEXT,
                audit_time TEXT,
                observation TEXT,
                general_photos TEXT,
                items TEXT,
                deleted_at INTEGER DEFAULT NULL
            )
        """)
        conn.commit()

init_db()

class ItemData(BaseModel):
    status: Optional[str] = None
    remarks: Optional[str] = ""
    photos: Optional[List[str]] = []

class MetaData(BaseModel):
    date: str
    time: Optional[str] = ""
    auditor: Optional[str] = ""
    roomNo: Optional[str] = ""
    observation: Optional[str] = ""

class AuditReport(BaseModel):
    id: str
    meta: MetaData
    generalPhotos: Optional[List[str]] = []
    items: Dict[str, ItemData]

@app.get("/api/audits")
def get_active_audits():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, room_no, auditor, audit_date, audit_time FROM audits WHERE deleted_at IS NULL ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]

@app.get("/api/audits/trash")
def get_trashed_audits():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, room_no, auditor, audit_date, deleted_at FROM audits WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
        return [dict(row) for row in cursor.fetchall()]

@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audits WHERE id = ?", (audit_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Audit not found")
        data = dict(row)
        return {
            "id": data["id"],
            "meta": {
                "date": data["audit_date"],
                "time": data["audit_time"],
                "auditor": data["auditor"],
                "roomNo": data["room_no"],
                "observation": data["observation"]
            },
            "generalPhotos": json.loads(data["general_photos"] or "[]"),
            "items": json.loads(data["items"] or "{}")
        }

@app.post("/api/audits")
def save_audit(report: AuditReport):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO audits (id, room_no, auditor, audit_date, audit_time, observation, general_photos, items, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(id) DO UPDATE SET
                room_no = excluded.room_no,
                auditor = excluded.auditor,
                audit_date = excluded.audit_date,
                audit_time = excluded.audit_time,
                observation = excluded.observation,
                general_photos = excluded.general_photos,
                items = excluded.items
        """, (
            report.id, report.meta.roomNo, report.meta.auditor,
            report.meta.date, report.meta.time, report.meta.observation,
            json.dumps(report.generalPhotos),
            json.dumps({k: v.dict() for k, v in report.items.items()})
        ))
        conn.commit()
    return {"status": "success", "id": report.id}

@app.delete("/api/audits/{audit_id}/trash")
def move_to_trash(audit_id: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE audits SET deleted_at = ? WHERE id = ?", (int(time.time() * 1000), audit_id))
        conn.commit()
    return {"status": "success"}

@app.post("/api/audits/{audit_id}/restore")
def restore_from_trash(audit_id: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE audits SET deleted_at = NULL WHERE id = ?", (audit_id,))
        conn.commit()
    return {"status": "success"}

@app.get("/api/audits/{audit_id}/pdf")
def generate_pdf(audit_id: str):
    report_data = get_audit(audit_id)
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    p.setFont("Helvetica-Bold", 16)
    p.drawString(40, height - 40, "Housekeeping Guest Room Audit")
    
    meta = report_data["meta"]
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 65, f"Room Number: {meta['roomNo']}")
    p.drawString(200, height - 65, f"Auditor: {meta['auditor']}")
    p.drawString(380, height - 65, f"Date: {meta['date']} {meta['time']}")
    
    p.setLineWidth(0.5)
    p.line(40, height - 75, width - 40, height - 75)

    y_position = height - 100
    p.setFont("Helvetica-Bold", 12)
    p.drawString(40, y_position, "Audit Findings Summary")
    
    y_position -= 20
    p.setFont("Helvetica", 10)
    items = report_data["items"]
    
    for idx, item_key in enumerate(items):
        item = items[item_key]
        status = item["status"] or "Not Checked"
        remarks = item["remarks"] or ""
        text_line = f"Item #{int(item_key) + 1}: Status - {status.upper()} | Remarks: {remarks}"
        
        p.drawString(40, y_position, text_line[:90])
        y_position -= 18
        
        if y_position < 50:
            p.showPage()
            p.setFont("Helvetica", 10)
            y_position = height - 40

    p.showPage()
    p.save()
    buffer.seek(0)
    
    return Response(content=buffer.getvalue(), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=Room_Audit_{meta['roomNo'] or 'Report'}.pdf"
    })