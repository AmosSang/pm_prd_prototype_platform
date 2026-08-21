"""Flask 应用工厂。一期蓝图随任务卡逐步注册。"""
import os

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    # T0.1 阶段：环境就绪探针 + Hello 占位页
    @app.get("/api/health")
    def health():
        return jsonify(code=0, data={"status": "ok", "service": "server"}), 200

    @app.get("/")
    def index():
        return "<h1>Product Plan Platform - server placeholder</h1>", 200

    return app


app = create_app()

if __name__ == "__main__":
    # 直接运行时把 platform/ 加入 sys.path，使 `server.config` 可导入
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from server.config import PORT, WEB_ORIGIN

    # 开发期放开 Vite dev server 的跨域（生产由 Nginx 同域转发，无跨域）
    from flask_cors import CORS

    CORS(app, origins=[WEB_ORIGIN])
    app.run(host="0.0.0.0", port=PORT, debug=True)
