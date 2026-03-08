import json
import queue
import threading
import uuid

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from database import Database
from pipeline import run_analysis_pipeline

app = Flask(__name__)
db = Database()

# In-memory map of active job queues
_job_queues: dict[str, queue.Queue] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400

    if not url.startswith("http"):
        url = "https://" + url

    job_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    _job_queues[job_id] = q

    db.create_analysis(job_id, url)

    t = threading.Thread(
        target=run_analysis_pipeline,
        args=(job_id, url, q, db),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id: str):
    """Server-Sent Events endpoint for live progress updates."""
    q = _job_queues.get(job_id)

    # If queue is gone but we have saved results, replay them
    if q is None:
        saved = db.get_analysis(job_id)
        if saved and saved.get("results_json"):
            def replay():
                results = json.loads(saved["results_json"])
                yield f"data: {json.dumps({'type': 'complete', 'data': results})}\n\n"
            return Response(stream_with_context(replay()), mimetype="text/event-stream")
        return jsonify({"error": "Job not found"}), 404

    def generate():
        while True:
            try:
                event = q.get(timeout=90)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("complete", "error"):
                    _job_queues.pop(job_id, None)
                    break
            except queue.Empty:
                # Heartbeat to keep connection alive
                yield "data: {\"type\": \"heartbeat\"}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/history")
def history():
    return jsonify(db.get_all_analyses())


@app.route("/results/<job_id>")
def results(job_id: str):
    row = db.get_analysis(job_id)
    if not row:
        return jsonify({"error": "Not found"}), 404
    if row.get("results_json"):
        row["results"] = json.loads(row["results_json"])
    return jsonify(row)


if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)
