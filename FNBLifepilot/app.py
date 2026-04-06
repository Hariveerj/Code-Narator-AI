# ================================================================
# BNF LifePilot — Flask Application
# ================================================================
import os
from flask import Flask, send_from_directory
from database.db import get_conn

from routes.accounts import accounts_bp
from routes.dashboard import dashboard_bp
from routes.shield import shield_bp
from routes.ebucks import ebucks_bp
from routes.agent import agent_bp
from routes.evaluation import evaluation_bp
from routes.ai import ai_bp

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ── Register blueprints ───────────────────────────────────────
app.register_blueprint(accounts_bp, url_prefix="/api/accounts")
app.register_blueprint(dashboard_bp, url_prefix="/api/dashboard")
app.register_blueprint(shield_bp, url_prefix="/api/shield")
app.register_blueprint(ebucks_bp, url_prefix="/api/ebucks")
app.register_blueprint(agent_bp, url_prefix="/api/agent")
app.register_blueprint(evaluation_bp, url_prefix="/api/evaluation")
app.register_blueprint(ai_bp, url_prefix="/api/ai")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ── Initialise DB on import ───────────────────────────────────
get_conn()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print(f"🚀 BNF LifePilot running → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
