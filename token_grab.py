"""mitmproxy 插件: 拦截 auth/login 响应自动保存新 token
用法: mitmdump -s token_grab.py --allow-hosts 'codoon|igofit' ...
捕获成功后写 token.txt 并落 token.fresh 标记文件；
同时把请求里的 platform_user_id / club_id 记到 captured_params.json
"""
import json, gzip, os

DIR = os.path.dirname(os.path.abspath(__file__))
PARAMS = os.path.join(DIR, "captured_params.json")


def request(flow):
    try:
        q = flow.request.query
        uid = q.get("platform_user_id") or ""
        club = q.get("club_id") or ""
        if not uid and not club:
            return
        data = {}
        if os.path.exists(PARAMS):
            try:
                with open(PARAMS, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        if uid:
            data["user_id"] = uid
        if club:
            data["club_id"] = str(club)
        with open(PARAMS, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def response(flow):
    if "auth/login" in flow.request.pretty_url and flow.response:
        raw = flow.response.content
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        try:
            d = json.loads(raw)
            tok = d.get("token", {}).get("access_token")
            if tok:
                with open(os.path.join(DIR, "token.txt"), "w") as f:
                    f.write(tok)
                with open(os.path.join(DIR, "token.fresh"), "w") as f:
                    f.write("1")
                print("[+] token 已捕获并保存")
        except Exception:
            pass
