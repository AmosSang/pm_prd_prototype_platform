"""Flask 应用工厂。一期蓝图随任务卡逐步注册。"""
import os
import sys

from flask import Flask, jsonify

# 支持 `python app.py` 直接运行（platform/ 根加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.proto_proxy import bp as proto_proxy_bp
from server.shots import bp as shots_bp


def create_app() -> Flask:
    app = Flask(__name__)

    app.register_blueprint(proto_proxy_bp)
    app.register_blueprint(shots_bp)

    @app.get("/api/health")
    def health():
        return jsonify(code=0, data={"status": "ok", "service": "server"}), 200

    @app.get("/")
    def index():
        return "<h1>Product Plan Platform - server placeholder</h1>", 200

    return app


app = create_app()


if __name__ == "__main__":
    # 开发期放开 Vite dev server 的跨域（生产由 Nginx 同域转发，无跨域）
    from flask_cors import CORS

    from server.config import PORT, WEB_ORIGIN

    CORS(app, origins=[WEB_ORIGIN])
    app.run(host="0.0.0.0", port=PORT, debug=True)
