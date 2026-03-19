from flask import Flask, request, jsonify, render_template_string
import threading
import time
import socket
import os
import json
from urllib.parse import quote_plus

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

DEFAULT_SHOW_TIME = 5.0       # seconds sentence is visible
DEFAULT_BLANK_TIME = 2.0      # seconds blank screen between sentences

TEXT_COLOR = "#FFFFFF"
BACKGROUND_COLOR = "#000000"   # black = transparent on glasses
BLANK_COLOR = "#000000"        # keep black during blank
FONT_SIZE = "56px"
FONT_FAMILY = "Consolas, monospace"
MAX_CHARS = 240
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5000
CONTENT_FILE = os.getenv("AR_CONTENT_FILE", "content.json")

DEFAULT_BLOCKS = {
    1: [
    {"data": "Block 1 - Sentence 1", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 1 - Sentence 2", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 1 - Sentence 3", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 1 - Sentence 4", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    ],
    2: [
    {"data": "Block 2 - Sentence 1", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 2 - Sentence 2", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 2 - Sentence 3", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 2 - Sentence 4", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    ],
    3: [
    {"data": "Block 3 - Sentence 1", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 3 - Sentence 2", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 3 - Sentence 3", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 3 - Sentence 4", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    ],
    4: [
    {"data": "Block 4 - Sentence 1", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 4 - Sentence 2", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 4 - Sentence 3", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    {"data": "Block 4 - Sentence 4", "show_time": DEFAULT_SHOW_TIME, "blank_time": DEFAULT_BLANK_TIME},
    ],
}

def validate_blocks(blocks):
  if len(blocks) != 4:
    raise ValueError(f"Exactly 4 blocks are required. There are {len(blocks)} blocks in the input.")

  for b, sents in blocks.items():
    if len(sents) != 4:
      raise ValueError(f"Block {b} must contain exactly 4 sentences.")

    for i, sent in enumerate(sents, 1):
      if not isinstance(sent, dict):
        raise ValueError(f"Block {b}, sentence {i} must be an object.")

      data = str(sent.get("data", ""))
      show_time = float(sent.get("show_time", DEFAULT_SHOW_TIME))
      blank_time = float(sent.get("blank_time", DEFAULT_BLANK_TIME))

      if not data.strip():
        raise ValueError(f"Block {b}, sentence {i} data must not be empty.")
      if len(data) > MAX_CHARS:
        raise ValueError(f"Block {b}, sentence {i} exceeds {MAX_CHARS} chars.")
      if show_time <= 0:
        raise ValueError(f"Block {b}, sentence {i} show_time must be > 0.")
      if blank_time < 0:
        raise ValueError(f"Block {b}, sentence {i} blank_time must be >= 0.")

      sent["data"] = data
      sent["show_time"] = show_time
      sent["blank_time"] = blank_time

def normalize_blocks(raw_blocks):
  normalized = {}
  for block_id in range(1, 5):
    if str(block_id) in raw_blocks:
      sentences = raw_blocks[str(block_id)]
    elif block_id in raw_blocks:
      sentences = raw_blocks[block_id]
    else:
      raise ValueError(f"Missing block {block_id}.")

    normalized[block_id] = []
    for sentence in sentences:
      if isinstance(sentence, str):
        normalized[block_id].append({
          "data": sentence,
          "show_time": DEFAULT_SHOW_TIME,
          "blank_time": DEFAULT_BLANK_TIME,
        })
      else:
        normalized[block_id].append(dict(sentence))

  validate_blocks(normalized)
  return normalized

def ensure_content_file(path):
  if os.path.exists(path):
    return
  with open(path, "w", encoding="utf-8") as f:
    json.dump({"blocks": DEFAULT_BLOCKS}, f, indent=2)

def load_blocks_from_file(path):
  ensure_content_file(path)
  with open(path, "r", encoding="utf-8") as f:
    raw = json.load(f)

  raw_blocks = raw.get("blocks", raw)
  if not isinstance(raw_blocks, dict):
    raise ValueError("JSON must contain an object of blocks.")

  return normalize_blocks(raw_blocks)

BLOCKS = load_blocks_from_file(CONTENT_FILE)

# ============================================================
# VALIDATION
# ============================================================

validate_blocks(BLOCKS)

# ============================================================
# SHARED STATE
# ============================================================

state_lock = threading.Lock()

state = {
    "selected_block": 1,
    "running": False,
    "paused": False,
    "phase": "blank",          # "show" or "blank"
    "sentence_index": 0,       # 0..3
    "current_text": "",
    "last_change": time.monotonic(),
}

# ============================================================
# ENGINE
# ============================================================

def reset_to_blank_locked():
    state["running"] = False
    state["paused"] = False
    state["phase"] = "blank"
    state["sentence_index"] = 0
    state["current_text"] = ""
    state["last_change"] = time.monotonic()

def start_block_locked(block_id):
    state["selected_block"] = block_id
    state["running"] = True
    state["paused"] = False
    state["phase"] = "show"
    state["sentence_index"] = 0
    state["current_text"] = BLOCKS[block_id][0]["data"]
    state["last_change"] = time.monotonic()

def pause_locked():
    if state["running"]:
        state["paused"] = True

def resume_locked():
    if state["running"] and state["paused"]:
        state["paused"] = False
        state["last_change"] = time.monotonic()

def stop_locked():
    reset_to_blank_locked()

def engine_loop():
    while True:
        time.sleep(0.05)

        with state_lock:
            if not state["running"] or state["paused"]:
                continue

            now = time.monotonic()
            dt = now - state["last_change"]

            if state["phase"] == "show":
                block_id = state["selected_block"]
                sentence = BLOCKS[block_id][state["sentence_index"]]
                if dt >= sentence["show_time"]:
                    state["phase"] = "blank"
                    state["current_text"] = ""
                    state["last_change"] = now

            else:  # blank
                block_id = state["selected_block"]
                sentence = BLOCKS[block_id][state["sentence_index"]]
                if dt >= sentence["blank_time"]:
                    next_idx = state["sentence_index"] + 1
                    if next_idx >= 4:
                        reset_to_blank_locked()
                    else:
                        block_id = state["selected_block"]
                        state["sentence_index"] = next_idx
                        state["phase"] = "show"
                        state["current_text"] = BLOCKS[block_id][next_idx]["data"]
                        state["last_change"] = now

threading.Thread(target=engine_loop, daemon=True).start()

# ============================================================
# HTML
# ============================================================

DISPLAY_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AR Display</title>
  <style>
    html, body {
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      cursor: none;
      background: {{ bg_color }};
      font-family: {{ font_family }};
    }

    #screen {
      width: 100vw;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      box-sizing: border-box;
      padding: 3vh 4vw;
      background: {{ bg_color }};
      color: {{ text_color }};
      user-select: none;
      -webkit-user-select: none;
      text-align: center;
    }

    #text {
      width: 100%;
      max-width: 92vw;
      font-size: {{ font_size }};
      line-height: 1.15;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: break-word;

      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 6;
      overflow: hidden;
    }
  </style>
</head>
<body>
  <div id="screen">
    <div id="text"></div>
  </div>

  <script>
    const textEl = document.getElementById("text");
    const screenEl = document.getElementById("screen");
    const bgColor = {{ bg_color|tojson }};
    const blankColor = {{ blank_color|tojson }};
    const textColor = {{ text_color|tojson }};

    async function enterFullscreen() {
      try {
        if (!document.fullscreenElement) {
          await document.documentElement.requestFullscreen();
        }
      } catch (e) {}
    }

    async function refresh() {
      try {
        const r = await fetch("/api/state", { cache: "no-store" });
        const s = await r.json();

        if (s.phase === "show" && s.current_text) {
          screenEl.style.background = bgColor;
          document.body.style.background = bgColor;
          textEl.style.color = textColor;
          textEl.textContent = s.current_text;
        } else {
          screenEl.style.background = blankColor;
          document.body.style.background = blankColor;
          textEl.textContent = "";
        }
      } catch (e) {}
    }

    document.addEventListener("click", enterFullscreen);
    document.addEventListener("touchstart", enterFullscreen, { passive: true });

    refresh();
    setInterval(refresh, 150);
  </script>
</body>
</html>
"""

CONTROL_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AR Control Dashboard</title>
  <style>
    body {
      font-family: Arial, Helvetica, sans-serif;
      margin: 24px;
      line-height: 1.4;
    }
    .row {
      margin-bottom: 16px;
    }
    button, select {
      font-size: 16px;
      padding: 10px 14px;
      margin-right: 8px;
    }
    .card {
      border: 1px solid #ccc;
      padding: 16px;
      margin-top: 16px;
    }
    .mono {
      font-family: Consolas, monospace;
      white-space: pre-wrap;
    }
    .block {
      margin-top: 12px;
      padding: 12px;
      border: 1px solid #ddd;
    }
    .active {
      border: 2px solid #000;
    }
  </style>
</head>
<body>
  <h2>AR Control Dashboard</h2>

  <div class="row">
    <label for="blockSel"><b>Select block:</b></label>
    <select id="blockSel">
      <option value="1">Block 1</option>
      <option value="2">Block 2</option>
      <option value="3">Block 3</option>
      <option value="4">Block 4</option>
    </select>
  </div>

  <div class="row">
    <button onclick="startBlock()">Start</button>
    <button onclick="pauseBlock()">Pause</button>
    <button onclick="resumeBlock()">Resume</button>
    <button onclick="stopBlock()">Stop</button>
  </div>

  <div class="card">
    <h3>Status</h3>
    <div id="status" class="mono"></div>
  </div>

  <div class="card">
    <h3>Blocks</h3>
    {% for block_id, sents in blocks.items() %}
      <div class="block" id="block-{{ block_id }}">
        <b>Block {{ block_id }}</b><br><br>
        {% for s in sents %}
          {{ loop.index }}. {{ s.data }} (show: {{ s.show_time }}s, blank: {{ s.blank_time }}s)<br>
        {% endfor %}
      </div>
    {% endfor %}
  </div>

  <div class="card">
    <h3>Display URL</h3>
    <div class="mono"><a href="{{ display_url }}" target="_blank" rel="noopener noreferrer">{{ display_url }}</a></div>
  </div>

  <div class="card" style="text-align:center;">
    <h3>/display QR Code</h3>
    <img src="{{ display_qr_url }}" alt="Display link QR code" style="width: 360px; max-width: 90vw; height: auto; border: 1px solid #ddd; padding: 8px; background: #fff;">
  </div>

  <script>
    const blockSel = document.getElementById("blockSel");
    const statusEl = document.getElementById("status");

    blockSel.addEventListener("change", async () => {
      await post("/api/select_block", { block: parseInt(blockSel.value, 10) });
      refresh();
    });

    async function post(url, data = {}) {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
      });
      return await r.json();
    }

    async function startBlock() {
      await post("/api/start", { block: parseInt(blockSel.value, 10) });
      refresh();
    }

    async function pauseBlock() {
      await post("/api/pause");
      refresh();
    }

    async function resumeBlock() {
      await post("/api/resume");
      refresh();
    }

    async function stopBlock() {
      await post("/api/stop");
      refresh();
    }

    async function refresh() {
      const r = await fetch("/api/state", { cache: "no-store" });
      const s = await r.json();

      if (document.activeElement !== blockSel) {
        blockSel.value = String(s.selected_block);
      }

      statusEl.textContent =
        "selected_block : " + s.selected_block + "\\n" +
        "running        : " + s.running + "\\n" +
        "paused         : " + s.paused + "\\n" +
        "phase          : " + s.phase + "\\n" +
        "sentence_index : " + s.sentence_index + "\\n" +
        "current_text   : " + s.current_text;

      for (let i = 1; i <= 4; i++) {
        document.getElementById("block-" + i).classList.remove("active");
      }
      document.getElementById("block-" + s.selected_block).classList.add("active");
    }

    refresh();
    setInterval(refresh, 300);
  </script>
</body>
</html>
"""

# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def root():
    return control()

@app.route("/control")
def control():
  ip = get_local_ip()
  display_url = f"http://{ip}:{SERVER_PORT}/display"
  return render_template_string(
        CONTROL_HTML,
        blocks=BLOCKS,
    display_url=display_url,
    display_qr_url=f"https://api.qrserver.com/v1/create-qr-code/?size=720x720&data={quote_plus(display_url)}",
    )

@app.route("/display")
def display():
    return render_template_string(
        DISPLAY_HTML,
        bg_color=BACKGROUND_COLOR,
        blank_color=BLANK_COLOR,
        text_color=TEXT_COLOR,
        font_size=FONT_SIZE,
        font_family=FONT_FAMILY,
    )

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(dict(state))

@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    block_id = int(data.get("block", 1))
    if block_id not in BLOCKS:
        return jsonify({"ok": False, "error": "invalid block"}), 400
    with state_lock:
        start_block_locked(block_id)
    return jsonify({"ok": True})

@app.route("/api/select_block", methods=["POST"])
def api_select_block():
    data = request.get_json(silent=True) or {}
    block_id = int(data.get("block", 1))
    if block_id not in BLOCKS:
        return jsonify({"ok": False, "error": "invalid block"}), 400
    with state_lock:
        state["selected_block"] = block_id
    return jsonify({"ok": True})

@app.route("/api/pause", methods=["POST"])
def api_pause():
    with state_lock:
        pause_locked()
    return jsonify({"ok": True})

@app.route("/api/resume", methods=["POST"])
def api_resume():
    with state_lock:
        resume_locked()
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    with state_lock:
        stop_locked()
    return jsonify({"ok": True})

@app.route("/api/config")
def api_config():
    return jsonify({
    "default_show_time": DEFAULT_SHOW_TIME,
    "default_blank_time": DEFAULT_BLANK_TIME,
        "text_color": TEXT_COLOR,
        "background_color": BACKGROUND_COLOR,
        "blank_color": BLANK_COLOR,
        "font_size": FONT_SIZE,
        "font_family": FONT_FAMILY,
    "content_file": CONTENT_FILE,
        "blocks": BLOCKS,
    })

# ============================================================
# UTIL
# ============================================================

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def find_available_port(host, preferred_port):
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        test_sock.bind((host, preferred_port))
        return preferred_port
    except OSError:
        pass
    finally:
        test_sock.close()

    fallback_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        fallback_sock.bind((host, 0))
        return fallback_sock.getsockname()[1]
    finally:
        fallback_sock.close()

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    ip = get_local_ip()
    host = os.getenv("AR_HOST", "0.0.0.0")
    preferred_port = int(os.getenv("AR_PORT", "5000"))
    port = find_available_port(host, preferred_port)

    SERVER_HOST = host
    SERVER_PORT = port

    if port != preferred_port:
        print(f"Port {preferred_port} is busy. Using port {port} instead.")

    local_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"Control page : http://{local_host}:{port}/control")
    print(f"Display page : http://{ip}:{port}/display")
    app.run(host=host, port=port, debug=False, threaded=True)