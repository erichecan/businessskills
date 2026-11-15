"""Local-only setup page for the PoStory XHS pipeline configuration."""
from __future__ import annotations

import html
import json
import os
import shutil
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from xhs_pipeline.browser import start as start_browser  # noqa: E402
from xhs_pipeline.config import DEFAULT_CONFIG, PipelineConfigError, validate_registry, load_registry  # noqa: E402

HOST = "127.0.0.1"
PORT = 8791
PIPELINE_ID = "postory-xhs"


def _load_document() -> dict | None:
    path = Path(os.environ.get("XHS_PIPELINES_CONFIG", DEFAULT_CONFIG)).expanduser()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _template_document() -> dict:
    template = REPO / "config" / "xhs-pipelines.example.json"
    return json.loads(template.read_text(encoding="utf-8"))


def _field(label: str, key: str, value: str, *, kind: str = "text", help_text: str = "") -> str:
    if kind == "checkbox":
        checked = " checked" if value.lower() == "true" else ""
        control = f'<input type="checkbox" name="{key}" value="true"{checked}>'
    else:
        control = f'<input type="{kind}" name="{key}" value="{html.escape(value, quote=True)}">'
    hint = f'<small>{html.escape(help_text)}</small>' if help_text else ""
    return f'<label>{html.escape(label)}{control}{hint}</label>'


def _render(message: str = "", *, error: bool = False) -> bytes:
    doc = _load_document()
    pipeline = None
    if doc:
        pipeline = next((p for p in doc.get("pipelines", []) if p.get("id") == PIPELINE_ID), None)
    if not pipeline and doc and doc.get("pipelines"):
        pipeline = doc["pipelines"][0]
    if pipeline is None:
        pipeline = _template_document()["pipelines"][0]

    workspace = pipeline.get("workspace", {})
    collection = pipeline.get("collection", {})
    generation = pipeline.get("generation", {})
    rewrite = generation.get("rewrite", {})
    publishing = pipeline.get("publishing", {})
    browser = publishing.get("browser", {})
    enabled = str(pipeline.get("enabled", False)).lower()

    controls = "".join([
        _field("流水线名称", "name", pipeline.get("name", "PoStory 小红书宣传")),
        _field("启用流水线", "enabled", enabled, kind="checkbox",
               help_text="保存为启用后，只有明确选择 postory-xhs 才可运行。"),
        _field("小红书账号标识", "account_id", publishing.get("account_id", "postory-xhs")),
        _field("扫码后页面显示的账号名", "verified_account_label", publishing.get("verified_account_label", ""),
               help_text="登录后由你核对填写；未验证前发布入口会被拦截。"),
        _field("已核对为 Postory 账号", "identity_verified",
               str(publishing.get("identity_verified", False)).lower(), kind="checkbox"),
        _field("数据根目录", "data_root", workspace.get("data_root", "")),
        _field("品牌与改写 Prompt 文件", "prompt_file", rewrite.get("prompt_file", "")),
        _field("内容方向/目标受众", "content_scope", collection.get("content_scope", ""),
               help_text="公开来源会按下方矩阵自动采集、去重并归入素材池。"),
        _field("Chromium 可执行文件", "browser_executable", browser.get("executable", "auto")),
        _field("独立 Chromium Profile", "browser_profile", browser.get("user_data_dir", "")),
        _field("CDP 端口", "cdp_port", str(browser.get("cdp_port", 19341)), kind="number"),
        _field("本流水线代理端口", "proxy_port", str(browser.get("proxy_port", 19342)), kind="number"),
        _field("时区", "timezone", publishing.get("timezone", "America/Toronto")),
        '<label>发布前人工确认<input type="checkbox" checked disabled><small>该安全项固定开启，配置页不提供关闭选项。</small></label>',
    ])
    source_rows = "".join(
        f'<li><strong>{html.escape(str(source.get("name", source.get("id", ""))))}</strong>'
        f'<span>{"启用" if source.get("enabled") else "暂停"}</span>'
        f'<small>{html.escape("、".join(source.get("tags", [])))}</small></li>'
        for source in collection.get("sources", [])
    )
    notice = f'<div class="notice {"error" if error else "success"}">{html.escape(message)}</div>' if message else ""
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PoStory 流水线配置</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f6fa;color:#202536;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:980px;margin:36px auto;padding:0 20px}}header,.card{{background:white;border:1px solid #e6e8ef;border-radius:16px;padding:24px;margin-bottom:18px}}
h1{{margin:0 0 8px;font-size:25px}}p{{color:#687086;line-height:1.6}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}}
label{{display:block;font-weight:650;margin:12px 0}}input:not([type=checkbox]){{display:block;width:100%;margin-top:7px;padding:11px;border:1px solid #d5d9e3;border-radius:8px;font-size:14px}}
input[type=checkbox]{{margin:0 9px 0 0;accent-color:#e83b78}}small{{display:block;color:#7c8497;font-weight:400;margin-top:5px}}
button{{border:0;border-radius:8px;padding:11px 16px;background:#e83b78;color:#fff;font-size:14px;font-weight:650;cursor:pointer;margin:4px 8px 0 0}}
button.secondary{{background:#273246}}.notice{{padding:12px 14px;border-radius:9px;margin:0 0 16px}}.success{{background:#e8f6ed;color:#21643a}}.error{{background:#fff0ed;color:#8d3024}}
.danger{{background:#fff8e8;border:1px solid #f3d99c;padding:12px;border-radius:9px;color:#745500}}
ul.sources{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:10px}}ul.sources li{{border:1px solid #e6e8ef;border-radius:10px;padding:12px}}ul.sources span{{float:right;color:#23834b;font-size:12px}}ul.sources small{{margin-top:7px}}
@media(max-width:700px){{main{{margin:14px auto}}.grid{{grid-template-columns:1fr}}header,.card{{padding:18px}}}}
</style><main><header><h1>PoStory 小红书生产线</h1><p>本地配置页 · 账号、素材、数据和 Chromium 登录态按流水线隔离。保存配置不会启动采集或发布。</p></header>
{notice}<form method="post" action="/save"><section class="card"><h2>流水线与内容定位</h2><div class="grid">{controls}</div>
<h3>自动采集来源</h3><p>从公开来源发现文章与案例，不抓取小红书站内内容，不需要手动导入目录。</p><ul class="sources">{source_rows}</ul>
<p class="danger">采集只保存可访问的公开页面正文和来源信息。发布仍需人工确认；不会导出 cookie。</p>
<button type="submit">保存并校验配置</button></section></form>
<section class="card"><h2>账号登录</h2><p>保存并启用配置后，可以打开 Postory 专属 Chromium。请在窗口中扫码登录并核对账号；登录信息保存在独立 profile 内。</p>
<form method="post" action="/browser-login"><button class="secondary" type="submit">启动专属 Chromium 登录</button></form></section>
<section class="card"><h2>运行隔离</h2><p>流水线 ID：<code>{html.escape(str(pipeline.get("id", PIPELINE_ID)))}</code><br>数据根目录：<code>{html.escape(str(workspace.get("data_root", "未配置")))}</code><br>账号标识：<code>{html.escape(str(publishing.get("account_id", "未配置")))}</code></p>
<p>原职场内容流水线停机标记仍然有效。新流程必须显式选择 <code>postory-xhs</code> 才能运行。</p></section></main></html>'''
    return page.encode("utf-8")


def _save_form(values: dict[str, list[str]]) -> Path:
    config_path = Path(os.environ.get("XHS_PIPELINES_CONFIG", DEFAULT_CONFIG)).expanduser().resolve()
    document = _load_document() or _template_document()
    pipelines = document.setdefault("pipelines", [])
    pipeline = next((p for p in pipelines if p.get("id") == PIPELINE_ID), None)
    if pipeline is None:
        if len(pipelines) >= 1:
            raise PipelineConfigError("当前内部配置页暂支持单条 postory-xhs 配置；通用多客户界面放在下一阶段")
        pipeline = _template_document()["pipelines"][0]
        pipelines.append(pipeline)

    def value(name: str, default: str = "") -> str:
        return (values.get(name) or [default])[0].strip()

    def checked(name: str) -> bool:
        return value(name).lower() in {"true", "on", "1"}

    pipeline["id"] = PIPELINE_ID
    pipeline["name"] = value("name", pipeline.get("name", "PoStory 小红书宣传"))
    pipeline["enabled"] = checked("enabled")
    pipeline.setdefault("workspace", {})["data_root"] = value("data_root")
    collection = pipeline.setdefault("collection", {})
    template_collection = _template_document()["pipelines"][0]["collection"]
    public_sources = collection.get("sources", [])
    if (not public_sources or not any(source.get("enabled") for source in public_sources)
            or any(source.get("provider") != "google_news_rss" for source in public_sources)):
        collection["sources"] = template_collection["sources"]
        collection["dedupe"] = template_collection["dedupe"]
        collection["editorial_matrix"] = template_collection["editorial_matrix"]
        collection["request_delay_seconds"] = template_collection["request_delay_seconds"]
    collection["content_scope"] = value(
        "content_scope", "中小企业经营者的营销问题、关键指标与有效动作"
    )
    # New PoStory workflow is fully driven by public-source collection. Keep
    # imported-directory materials out of this pipeline.
    pipeline.setdefault("generation", {})["material_sources"] = []
    rewrite = pipeline["generation"].setdefault("rewrite", {})
    rewrite["prompt_file"] = value("prompt_file")
    rewrite["style_profile"] = "postory_smb_marketing_authority"
    publishing = pipeline.setdefault("publishing", {})
    publishing["account_id"] = value("account_id", "postory-xhs")
    publishing["timezone"] = value("timezone", "America/Toronto")
    publishing["final_submit_requires_approval"] = True
    publishing["identity_verified"] = checked("identity_verified")
    publishing["verified_account_label"] = value("verified_account_label")
    browser = publishing.setdefault("browser", {})
    browser.update({
        "engine": "chromium",
        "executable": value("browser_executable", "auto"),
        "user_data_dir": value("browser_profile"),
        "cdp_port": int(value("cdp_port", "19341")),
        "proxy_port": int(value("proxy_port", "19342")),
    })

    validate_registry(document)
    data_root = Path(os.path.expandvars(pipeline["workspace"]["data_root"])).expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        data_root.chmod(0o700)
    except OSError:
        pass
    prompt_path = Path(os.path.expandvars(rewrite["prompt_file"])).expanduser().resolve()
    prompt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    prompt_example = REPO / "config" / "postory-xhs-rewrite.example.md"
    if not prompt_path.exists():
        shutil.copyfile(prompt_example, prompt_path)
        prompt_path.chmod(0o600)
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(config_path)
    try:
        config_path.chmod(0o600)
    except OSError:
        pass
    return config_path


class Handler(BaseHTTPRequestHandler):
    def _respond(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            self._respond(_render("仅允许本机访问。", error=True), 403)
            return
        self._respond(_render())

    def do_POST(self):
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            self._respond(_render("仅允许本机访问。", error=True), 403)
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}:
            self._respond(_render("请求来源校验失败。", error=True), 403)
            return
        length = int(self.headers.get("Content-Length", "0"))
        values = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        if self.path == "/save":
            try:
                path = _save_form(values)
                self._respond(_render(f"配置已保存并通过校验：{path}"))
            except Exception as exc:
                self._respond(_render(str(exc), error=True), 400)
            return
        if self.path == "/browser-login":
            try:
                # The login form has no configuration fields. Use the last
                # explicitly saved config instead of resetting it from the
                # empty form submission.
                registry = load_registry()
                pipeline = next(
                    (item for item in registry["pipelines"] if item.get("id") == PIPELINE_ID),
                    None,
                )
                if pipeline is None or not pipeline.get("enabled"):
                    raise PipelineConfigError("请先保存并启用 postory-xhs 配置，再启动扫码登录。")
                os.environ["XHS_PIPELINE_ID"] = PIPELINE_ID
                ctx = start_browser(open_creator=True)
                self._respond(_render(f"已启动 {ctx.pipeline_id} 专属 Chromium。请扫码并核对登录账号。"))
            except Exception as exc:
                self._respond(_render(str(exc), error=True), 400)
            return
        self._respond(_render("未知操作。", error=True), 404)

    def log_message(self, fmt, *args):
        return


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PoStory pipeline setup: http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
