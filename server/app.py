"""Flask 应用工厂。一期蓝图随任务卡逐步注册。"""
import os
import sys

from flask import Flask, jsonify

# 支持 `python app.py` 直接运行（platform/ 根加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.auth import apply_auth_to_app
from server.auth import bp as auth_bp
from server.models import init_tables
from server.proto_proxy import bp as proto_proxy_bp
from server.shots import bp as shots_bp


def create_app() -> Flask:
    app = Flask(__name__)

    apply_auth_to_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(proto_proxy_bp)
    app.register_blueprint(shots_bp)

    init_tables()

    # T2.2：API 登录拦截。放行：/api/auth/*、/api/health；
    # 非 /api/ 前缀（/proto/*、/vendor/*、静态资源）放行——原型 iframe 走沙箱隔离，
    # 页面数据接口才需要登录态。
    @app.before_request
    def require_login():
        from flask import request, session

        p = request.path
        if p.startswith("/api/auth/") or p == "/api/health" or not p.startswith("/api/"):
            return None
        if not session.get("uid"):
            return jsonify(code=401, msg="未登录"), 401
        return None

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
