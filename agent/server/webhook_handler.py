# agent/server/webhook_handler.py
from flask import Flask, request, jsonify
import subprocess
import threading

app = Flask(__name__)

@app.route("/trigger-test", methods=["POST"])
def trigger_test():
    data = request.json
    spec = data.get("spec")
    if not spec:
        return jsonify({"error": "Missing 'spec' in payload."}), 400

    def run_test():
        subprocess.run(["python", "main.py", "--spec", spec])

    threading.Thread(target=run_test).start()
    return jsonify({"status": "started", "spec": spec})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)