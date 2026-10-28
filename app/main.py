"""CloudDrop: move files between your devices through a short room code.

Start a room on one device, open the same room on another (scan the QR code
or type the code), and anything uploaded shows up on both. Files live in S3
and are deleted automatically after 24 hours.

Endpoints
  GET    /                                  home page: start or join a room
  GET    /r/{code}                          room page
  GET    /r/{code}/qr.svg                   QR code that opens the room
  GET    /health                            liveness (no dependencies)
  GET    /ready                             readiness (can we reach the bucket?)
  POST   /api/rooms                         create a room code
  GET    /api/rooms/{code}/files            list the room's files
  POST   /api/rooms/{code}/files            upload a file (multipart field "file")
  GET    /api/rooms/{code}/files/{id}       302 redirect to a short-lived signed S3 URL
  DELETE /api/rooms/{code}/files/{id}       delete a file
"""

import html
import io
import logging
import os
import socket
from functools import lru_cache

import segno
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.storage import FileStore, is_valid_file_id, is_valid_room, new_room_code

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("clouddrop")

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))

app = FastAPI(title="CloudDrop")


@lru_cache
def get_store() -> FileStore:
    bucket = os.environ.get("BUCKET_NAME")
    if not bucket:
        raise RuntimeError("BUCKET_NAME is not set")
    return FileStore(bucket=bucket, region=os.environ.get("AWS_REGION", "us-east-2"))


def checked_room(code: str) -> str:
    code = code.upper()
    if not is_valid_room(code):
        raise HTTPException(status_code=404, detail="room not found")
    return code


def checked_file_id(file_id: str) -> str:
    if not is_valid_file_id(file_id):
        raise HTTPException(status_code=400, detail="invalid file id")
    return file_id


@app.get("/health")
def health():
    return {"status": "ok", "host": socket.gethostname()}


@app.get("/ready")
def ready(store: FileStore = Depends(get_store)):
    if not store.ping():
        return JSONResponse(status_code=503, content={"status": "storage unavailable"})
    return {"status": "ready"}


@app.post("/api/rooms", status_code=201)
def create_room():
    return {"code": new_room_code()}


@app.get("/api/rooms/{code}/files")
def list_files(code: str, store: FileStore = Depends(get_store)):
    room = checked_room(code)
    files = []
    for f in store.list(room):
        item = f.__dict__ | {"download": f"/api/rooms/{room}/files/{f.id}"}
        if f.is_image:
            item["preview"] = store.download_url(room, f.id, attachment=False)
        files.append(item)
    return files


@app.post("/api/rooms/{code}/files", status_code=201)
async def upload_file(code: str, file: UploadFile, store: FileStore = Depends(get_store)):
    room = checked_room(code)
    # Read one byte past the limit so we can reject oversize uploads without
    # trusting the client's Content-Length header.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 10 MB)")
    stored = store.put(room, file.filename or "file", data, file.content_type)
    log.info("uploaded room=%s id=%s bytes=%d", room, stored.id, stored.size)
    return stored.__dict__


@app.get("/api/rooms/{code}/files/{file_id:path}")
def download_file(code: str, file_id: str, store: FileStore = Depends(get_store)):
    room, file_id = checked_room(code), checked_file_id(file_id)
    if not store.exists(room, file_id):
        raise HTTPException(status_code=404, detail="not found")
    return RedirectResponse(store.download_url(room, file_id), status_code=302)


@app.delete("/api/rooms/{code}/files/{file_id:path}", status_code=204)
def delete_file(code: str, file_id: str, store: FileStore = Depends(get_store)):
    room, file_id = checked_room(code), checked_file_id(file_id)
    if not store.exists(room, file_id):
        raise HTTPException(status_code=404, detail="not found")
    store.delete(room, file_id)
    log.info("deleted room=%s id=%s", room, file_id)


@app.get("/", response_class=HTMLResponse)
def home():
    return page(HOME)


@app.get("/r/{code}", response_class=HTMLResponse)
def room_page(code: str):
    room = checked_room(code)
    if room != code:
        return RedirectResponse(f"/r/{room}", status_code=307)
    return page(ROOM.replace("{{CODE}}", room))


@app.get("/r/{code}/qr.svg")
def room_qr(code: str, request: Request):
    room = checked_room(code)
    url = f"{request.base_url}r/{room}"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml")


def page(body: str) -> str:
    return LAYOUT.replace("{{BODY}}", body).replace(
        "{{HOST}}", html.escape(socket.gethostname())
    )


LAYOUT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CloudDrop</title>
<style>
  :root { --bg:#f4f6fb; --card:#fff; --text:#1c2333; --muted:#6b7385; --line:#e3e7ef;
          --accent:#4f46e5; --accent-soft:#eef0ff; --danger:#d93636; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1220; --card:#181c2e; --text:#e8eaf3; --muted:#9097ab; --line:#2a3046;
            --accent:#8b85ff; --accent-soft:#23264a; --danger:#ff6b6b; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
  main { max-width:760px; margin:0 auto; padding:40px 16px; }
  header { display:flex; align-items:center; gap:12px; margin-bottom:6px; }
  header a { color:inherit; text-decoration:none; display:flex; align-items:center; gap:12px; }
  .logo { width:40px; height:40px; border-radius:10px; background:var(--accent); color:#fff;
          display:grid; place-items:center; font-size:22px; }
  h1 { margin:0; font-size:28px; letter-spacing:-.02em; }
  h2 { margin:0 0 4px; font-size:17px; }
  .tagline { color:var(--muted); margin:0 0 28px; }
  .muted { color:var(--muted); font-size:14px; margin:0 0 14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:20px; margin-bottom:20px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:20px; }
  .grid .card { margin:0; }
  @media (max-width:620px) { .grid { grid-template-columns:1fr; } }
  .btn { border:1px solid var(--line); background:transparent; color:var(--text); border-radius:8px;
         padding:6px 12px; font:inherit; font-size:13px; cursor:pointer; text-decoration:none; }
  .btn:hover { border-color:var(--accent); color:var(--accent); }
  .btn.del:hover { border-color:var(--danger); color:var(--danger); }
  .primary { background:var(--accent); border-color:var(--accent); color:#fff;
             padding:10px 18px; font-size:15px; font-weight:600; }
  .primary:hover { color:#fff; opacity:.9; }
  .join { display:flex; gap:8px; }
  .join input { flex:1; min-width:0; padding:10px 12px; border:1px solid var(--line); border-radius:8px;
                background:var(--bg); color:var(--text); font:600 18px/1 ui-monospace,monospace;
                letter-spacing:.2em; text-transform:uppercase; }
  .steps { margin:0; padding-left:20px; color:var(--muted); }
  .room { display:flex; gap:20px; align-items:center; }
  .room img { width:132px; height:132px; flex:none; border-radius:10px; background:#fff; }
  .code { font:700 34px/1.1 ui-monospace,monospace; letter-spacing:.18em; margin:2px 0 8px; }
  @media (max-width:520px) { .room { flex-direction:column; align-items:flex-start; } }
  .drop { display:block; border:2px dashed var(--line); border-radius:12px; padding:28px 16px;
          text-align:center; cursor:pointer; transition:.15s; }
  .drop:hover, .drop.over { border-color:var(--accent); background:var(--accent-soft); }
  .drop strong { color:var(--accent); }
  .drop small { display:block; color:var(--muted); margin-top:4px; }
  #status { min-height:22px; margin:12px 0 0; font-size:14px; }
  #status.err { color:var(--danger); }
  .head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
  ul { list-style:none; margin:0; padding:0; }
  #list li { display:flex; align-items:center; gap:12px; padding:12px 0; border-top:1px solid var(--line); }
  #list li:first-child { border-top:0; }
  .icon { width:44px; height:44px; flex:none; border-radius:8px; background:var(--accent-soft);
          color:var(--accent); display:grid; place-items:center; font-size:11px; font-weight:700;
          overflow:hidden; }
  .icon img { width:100%; height:100%; object-fit:cover; }
  .meta { flex:1; min-width:0; }
  .name { font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sub { color:var(--muted); font-size:13px; }
  #list li.empty { display:block; color:var(--muted); text-align:center; padding:24px 0; }
  footer { color:var(--muted); font-size:12px; text-align:center; }
</style></head>
<body><main>
  <header><a href="/"><div class="logo">&#9729;</div><h1>CloudDrop</h1></a></header>
  {{BODY}}
  <footer>Served by {{HOST}} &middot; files are deleted automatically after 24 hours</footer>
</main></body></html>"""

HOME = """
  <p class="tagline">Move files between your phone and laptop. No accounts, no cables.</p>
  <div class="grid">
    <section class="card">
      <h2>Start a room</h2>
      <p class="muted">Get a code and a QR code to open on your other device.</p>
      <button class="btn primary" id="start">Start a room</button>
    </section>
    <section class="card">
      <h2>Join a room</h2>
      <p class="muted">Type the 6-character code shown on your other device.</p>
      <form class="join" id="join">
        <input id="code" maxlength="6" placeholder="ABC234" autocomplete="off" required>
        <button class="btn primary">Join</button>
      </form>
    </section>
  </div>
  <section class="card">
    <h2>How it works</h2>
    <ol class="steps">
      <li>Start a room on one device.</li>
      <li>Scan its QR code (or type the code) on the other device.</li>
      <li>Upload on either one. The file appears on both within a few seconds.</li>
    </ol>
  </section>
<script>
document.getElementById("start").onclick = async () => {
  const { code } = await (await fetch("/api/rooms", { method: "POST" })).json();
  location.href = "/r/" + code;
};
document.getElementById("join").onsubmit = e => {
  e.preventDefault();
  location.href = "/r/" + document.getElementById("code").value.trim().toUpperCase();
};
</script>"""

ROOM = """
  <p class="tagline">Anything uploaded here shows up on every device in this room.</p>
  <section class="card room">
    <img src="/r/{{CODE}}/qr.svg" alt="QR code for this room">
    <div>
      <p class="muted" style="margin:0">Room code</p>
      <div class="code">{{CODE}}</div>
      <p class="muted" style="margin:0">Scan the QR code with your phone camera,
        or open this site on another device and enter the code.</p>
    </div>
  </section>

  <section class="card">
    <label class="drop" id="drop">
      <input type="file" id="file" multiple hidden>
      <strong>Choose files</strong> or drag them here
      <small>Up to 10 MB each</small>
    </label>
    <p id="status"></p>
  </section>

  <section class="card">
    <div class="head"><h2>Files in this room</h2><span class="muted" id="count"></span></div>
    <ul id="list"><li class="empty">Loading&hellip;</li></ul>
  </section>
<script>
const ROOM = "{{CODE}}", api = "/api/rooms/" + ROOM + "/files";
const list = document.getElementById("list"), count = document.getElementById("count"),
      status = document.getElementById("status"), drop = document.getElementById("drop"),
      input = document.getElementById("file");
let shown = null;

function size(n) {
  return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
}
function say(msg, err) { status.textContent = msg; status.className = err ? "err" : ""; }

async function load() {
  const files = await (await fetch(api)).json();
  // Only redraw when the file list changed, so previews don't flicker.
  const ids = files.map(f => f.id).join();
  if (ids === shown) return;
  shown = ids;
  count.textContent = files.length + (files.length === 1 ? " file" : " files");
  list.replaceChildren();
  if (!files.length) {
    list.innerHTML = '<li class="empty">No files yet. Upload one here or from your other device.</li>';
    return;
  }
  for (const f of files) {
    const li = document.createElement("li");
    li.innerHTML = '<div class="icon"></div><div class="meta"><div class="name"></div>' +
      '<div class="sub"></div></div><a class="btn">Download</a><button class="btn del">Delete</button>';
    const icon = li.querySelector(".icon");
    if (f.preview) {
      const img = document.createElement("img");
      img.src = f.preview;
      img.alt = "";
      icon.append(img);
    } else {
      icon.textContent = f.name.includes(".") ? f.name.split(".").pop().slice(0, 4).toUpperCase() : "FILE";
    }
    li.querySelector(".name").textContent = f.name;
    li.querySelector(".sub").textContent = size(f.size) + " · " + new Date(f.last_modified).toLocaleString();
    li.querySelector("a").href = f.download;
    li.querySelector(".del").onclick = async () => {
      if (!confirm("Delete " + f.name + "?")) return;
      await fetch(f.download, { method: "DELETE" });
      say("Deleted " + f.name);
      load();
    };
    list.append(li);
  }
}

async function upload(files) {
  for (const file of files) {
    say("Uploading " + file.name + "…");
    const body = new FormData();
    body.append("file", file);
    const res = await fetch(api, { method: "POST", body });
    if (!res.ok) { say(file.name + ": " + ((await res.json()).detail || "upload failed"), true); return; }
  }
  say(files.length === 1 ? "Uploaded " + files[0].name : "Uploaded " + files.length + " files");
  input.value = "";
  load();
}

input.onchange = () => upload([...input.files]);
drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); };
load();
// Check for files from the other device every 3 seconds while the tab is open.
setInterval(() => { if (!document.hidden) load(); }, 3000);
</script>"""
