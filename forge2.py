#!/usr/bin/env python3
"""fakerun 主程序 — 校内道路轨迹伪造提交
用法: python3 -u forge2.py [--dry]
依赖: config.json(从 config.example.json 复制填写), token.txt, route_template.json
"""
import urllib.request, urllib.parse, json, gzip, time, random, math, sys, subprocess, os, tempfile

DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(DIR, "config.json")
if not os.path.exists(CFG_PATH) and os.path.exists(os.path.join(DIR, "config.example.json")):
    import shutil
    shutil.copyfile(os.path.join(DIR, "config.example.json"), CFG_PATH)
CFG = json.load(open(CFG_PATH, encoding="utf-8"))
try:
    TOKEN = open(os.path.join(DIR, "token.txt"), encoding="utf-8").read().strip()
except FileNotFoundError:
    TOKEN = ""
UID = CFG["user_id"]
CLUB_ID = CFG.get("club_id", 0)
BRAND = CFG["brand"]
DEV = CFG["device_info"]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.59(0x18003733) NetType/WIFI Language/zh_CN")
BASE = "https://mini-club.codoon.com"

TARGET_M = CFG.get("target_m", 3300)
BASE_PACE = CFG.get("base_pace", 300)   # 秒/km
PACE_MIN = CFG.get("pace_min", 280)     # 每公里最快秒数(下限)
PACE_MAX = CFG.get("pace_max", 330)     # 每公里最慢秒数(上限)

ROUTES = CFG.get("route_templates") or [CFG.get("route_template", "route_template.json")]
ROUTES = [r for r in ROUTES if os.path.exists(os.path.join(DIR, r))] or ["route_template.json"]

def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*6371000*math.asin(math.sqrt(h))

def load_route(name):
    poly = [tuple(p) for p in json.load(open(os.path.join(DIR, name), encoding="utf-8"))]
    arcs = [0.0]
    for k in range(len(poly)-1):
        arcs.append(arcs[-1] + hav(poly[k], poly[k+1]))
    return poly, arcs

def at_arc(poly, arcs, s):
    s = max(0.0, min(arcs[-1], s))
    for k in range(len(arcs)-1):
        if arcs[k+1] >= s:
            f = (s - arcs[k]) / (arcs[k+1] - arcs[k]) if arcs[k+1] > arcs[k] else 0
            return (poly[k][0] + (poly[k+1][0]-poly[k][0])*f,
                    poly[k][1] + (poly[k+1][1]-poly[k][1])*f)
    return poly[-1]

def pick_route():
    return random.choice(ROUTES)

def route_turn(poly):
    """累计转向：正值=逆时针，负值=顺时针。"""
    turn = 0.0
    for i in range(1, len(poly) - 1):
        x1 = poly[i][1] - poly[i-1][1]
        y1 = poly[i][0] - poly[i-1][0]
        x2 = poly[i+1][1] - poly[i][1]
        y2 = poly[i+1][0] - poly[i][0]
        turn += math.atan2(x1*y2 - y1*x2, x1*x2 + y1*y2)
    return turn

_normal_turn = None

def route_label(name, poly):
    """反着常规方向（以 route_template 为基准）的路线加「（反向）」。"""
    global _normal_turn
    if _normal_turn is None:
        base = CFG.get("route_template", "route_template.json")
        if not os.path.exists(os.path.join(DIR, base)):
            base = ROUTES[0]
        try:
            base_poly, _ = load_route(base)
            _normal_turn = route_turn(base_poly)
        except Exception:
            _normal_turn = 0.0
    if _normal_turn and route_turn(poly) * _normal_turn < 0:
        return name + "（反向）"
    return name

def make_course(total, target_m):
    """两种模式: 全程单向+折返尾 / 随机起点往返"""
    need = target_m + 120   # 留够余量: 单点最大约 34m, 余量不足会让最后几个点叠在同一位置
    if random.random() < 0.55:
        start = random.uniform(0, 60)
        course, desc = [start, total, start + need - total], "全程单向+折返尾"
    else:
        start = random.uniform(0, max(1, total - need/2))
        course, desc = [start, start + need/2, start], "随机起点往返"
    return course, desc

class Walker:
    def __init__(self, course):
        self.course = course; self.seg = 0; self.s = course[0]
    def step(self, d):
        target = self.course[self.seg + 1]
        direction = 1 if target >= self.course[self.seg] else -1
        self.s += direction * d
        if (direction > 0 and self.s >= target) or (direction < 0 and self.s <= target):
            if self.seg + 2 < len(self.course): self.seg += 1
            self.s = target
        return self.s

def call(method, path, body=None, extra=None):
    q = {"platform_source_type": 0, "platform_user_id": UID, "platform_language": "zh", "platform_app_brand": BRAND}
    if extra: q.update(extra)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(q), data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN); req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    req.add_header("Referer", "https://servicewechat.com/%s/942/page-frame.html" % CFG.get("appid", "wx2961574bbec73239"))
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
        if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
        return json.loads(raw) if raw else {}

ELEV_BASE = 50.0     # 校区海拔约 50m
ELEV_HILL = 11.8      # 6.5km 时的单段爬升（两段共 23.6m，服务端实测约 16m）


def elevation_at(cum):
    """按里程比例生成两段缓坡；6.5km 服务端约 16m，短距离按比例缩小。"""
    hill = ELEV_HILL * (TARGET_M / 6500.0)
    p = (cum / TARGET_M) % 1.0
    if p < 0.25:
        x = p / 0.25
        h = hill * (0.5 - 0.5 * math.cos(math.pi * x))
    elif p < 0.5:
        x = (p - 0.25) / 0.25
        h = hill * (0.5 + 0.5 * math.cos(math.pi * x))
    elif p < 0.75:
        x = (p - 0.5) / 0.25
        h = hill * (0.5 - 0.5 * math.cos(math.pi * x))
    else:
        x = (p - 0.75) / 0.25
        h = hill * (0.5 + 0.5 * math.cos(math.pi * x))
    return ELEV_BASE + h + random.gauss(0, 0.01)


def mkpoint(i, sim_s, lat, lon, cum, typ, ts, step_m):
    # 真实iPhone点位指纹——字段和取值域都经过服务端验证, 勿改
    return {"distance": round(step_m, 1), "elevation": round(elevation_at(cum), 1),
            "latitude": round(lat, 7), "longitude": round(lon, 7),
            "v_accuracy": round(random.uniform(2.8, 3.3), 6), "h_accuracy": 0, "gyroscope": "",
            "time_stamp": int(ts * 1000), "to_start_dost_time": int(sim_s * 1000),
            "to_start_distance": round(cum * .97, 1), "type": typ,
            "time_str": time.strftime("%m.%d %H:%M:%S", time.localtime(ts))}

def dry(route_override=None):
    name = route_override if route_override and os.path.exists(os.path.join(DIR, route_override)) else pick_route()
    poly, arcs = load_route(name)
    total = arcs[-1]
    course, desc = make_course(total, TARGET_M)
    w = Walker(course); cum = 0.0; pts = []
    while cum < TARGET_M:
        pace = max(PACE_MIN, min(PACE_MAX, BASE_PACE + 10*math.sin(cum/500) + random.gauss(0, 5)))
        dt = random.uniform(3.0, 7.0)
        step_m = 1000.0/pace * dt
        s = w.step(step_m); la, lo = at_arc(poly, arcs, s); cum += step_m
        pts.append((la + random.gauss(0, 1.5)/111320, lo + random.gauss(0, 1.5)/85200))
    print(f"路线: {route_label(name, poly)} ({total:.0f}m) | 模式: {desc} | {len(pts)}点 {cum:.0f}m")
    la=[p[0] for p in pts]; lo=[p[1] for p in pts]
    print(f"坐标范围: lat {min(la):.5f}~{max(la):.5f} lon {min(lo):.5f}~{max(lo):.5f}")

def run(route_override=None):
    if not TOKEN or not UID:
        print("[!] 还没有登录凭证：请先运行 run_now.pyw（或 refresh.ps1）抓取 token。", flush=True)
        return
    name = route_override if route_override and os.path.exists(os.path.join(DIR, route_override)) else pick_route()
    poly, arcs = load_route(name)
    total = arcs[-1]
    course, desc = make_course(total, TARGET_M)
    print(f"[*] 路线: {route_label(name, poly)} ({total:.0f}m) | 模式: {desc}", flush=True)
    run_pace = max(PACE_MIN, min(PACE_MAX, BASE_PACE + random.uniform(-6, 6)))  # 每次整体配速微调
    pace_phase = random.uniform(0, 2*math.pi)
    pace_period = random.uniform(150, 210)
    slow_period = random.uniform(780, 1020)
    slow_offset = random.uniform(0, slow_period)
    speed = 1000.0 / run_pace
    # 步幅随速度自适应：快跑 ~1.5m，慢跑/走 ~0.8m；步频 = 速度 / 步幅
    stride = max(0.75, min(1.58, 0.35 + 0.28 * speed)) * random.uniform(0.985, 1.015)
    print(f"[*] 本场配速基准 {run_pace:.1f}s/km, 步幅 {stride:.2f}m", flush=True)
    w = Walker(course)
    r = call("POST", "/v1/route/start_route", {"is_in_room": 0, "sports_type": 1, "location": "",
                                               "source": 2, "device_info": DEV})
    rid = r.get("route_id")
    print("start ->", rid, flush=True)
    if not rid: return
    wall0 = time.time(); ts_cursor = wall0
    cum = 0.0; i = 0; steps = 0; last_steps = 0.0; steps_mark = 0.0
    pending = []; all_pts = []
    while cum < TARGET_M:
        sim = time.time() - wall0
        i += 1
        pace = run_pace + 10 * math.sin(sim / pace_period + pace_phase) + random.gauss(0, 5)
        if (sim + slow_offset) % slow_period < 35: pace += 25
        pace = max(PACE_MIN, min(PACE_MAX, pace))
        dt = random.uniform(3.0, 7.0)              # 真实GPS回调间隔3~7秒
        step_m = 1000.0 / pace * dt
        s = w.step(step_m)
        la, lo = at_arc(poly, arcs, s)
        lat = la + random.gauss(0, 1.5) / 111320   # 噪声单位是米, 必须除以每度米数
        lon = lo + random.gauss(0, 1.5) / 85200
        cum += step_m
        ts_cursor += dt
        pending.append(mkpoint(i, sim + dt, lat, lon, cum, 0 if i > 1 else 1, ts_cursor, step_m))
        if len(pending) >= random.randint(2, 4):
            all_pts.extend(pending)
            rr = call("POST", "/v1/route/create_route_point", {"points": pending, "route_id": rid})
            if rr.get("ok") is not True: print("  BATCH FAIL:", rr, flush=True)
            pending = []
        if sim - last_steps >= 45:
            d = sim - last_steps
            d_dist = cum - steps_mark
            step_delta = max(0, int(d_dist / (stride * random.uniform(.98, 1.02))))
            steps_mark = cum
            steps += step_delta
            call("POST", "/v1/route/create_route_gyroscope_steps",
                 {"route_id": rid, "type": 6, "cur_steps": step_delta, "upload_time": int(time.time()), "dur": int(d)})
            last_steps = sim
        time.sleep(dt)
    sim = time.time() - wall0; i += 1; cum += 3.0; ts_cursor += random.uniform(3, 6)
    s = w.step(3.0); la, lo = at_arc(poly, arcs, s)
    pending.append(mkpoint(i, sim, la, lo, cum, 5, ts_cursor, 3.0))
    all_pts.extend(pending)
    print("final:", call("POST", "/v1/route/create_route_point", {"points": pending, "route_id": rid}), flush=True)
    d = sim - last_steps
    if d >= 3:
        step_delta = max(0, int((cum - steps_mark) / (stride * random.uniform(.98, 1.02))))
        if step_delta:
            steps += step_delta
            call("POST", "/v1/route/create_route_gyroscope_steps",
                 {"route_id": rid, "type": 6, "cur_steps": step_delta,
                  "upload_time": int(time.time()), "dur": int(d)})
    time.sleep(1.5)
    for attempt in range(3):
        comp = call("POST", "/v1/route/completes_route", {"is_delete": False, "route_id": rid, "device_info": DEV})
        print(f"complete#{attempt} ->", comp, flush=True)
        if comp.get("route_id") not in (0, None, ""):
            elevs = [p["elevation"] for p in all_pts]
            raw_climb = sum(max(0, elevs[k+1]-elevs[k]) for k in range(len(elevs)-1))
            sm = [sum(elevs[max(0, k-2):k+3]) / len(elevs[max(0, k-2):k+3]) for k in range(len(elevs))]
            smooth_climb = sum(max(0, sm[k+1]-sm[k]) for k in range(len(sm)-1))
            print(f"[*] 海拔 {min(elevs):.1f}~{max(elevs):.1f}m 原始爬升 {raw_climb:.1f}m 平滑爬升 {smooth_climb:.1f}m", flush=True)
            print(f"[OK] 记录ID {comp['route_id']}, {i}点 {cum:.0f}m {sim/60:.1f}min 步数{steps} 爬升{raw_climb:.0f}m", flush=True)
            points_path = os.path.join(tempfile.gettempdir(), f"points_{rid[:8]}.json")
            with open(points_path, "w") as f:
                json.dump([{"latitude": p["latitude"], "longitude": p["longitude"]} for p in all_pts], f)
            try:
                result = subprocess.run([sys.executable, os.path.join(DIR, "route_img.py"), rid,
                                         points_path, f"{cum/1000:.2f}", f"{sim/60:.1f}"],
                                        timeout=300, capture_output=True, text=True)
                if result.returncode == 0:
                    print("[OK] 轨迹图已生成并挂到记录", flush=True)
                else:
                    print("[!] 挂图失败:", result.stderr[-500:], flush=True)
            except Exception as e:
                print("[!] 挂图失败:", e, flush=True)
            finally:
                try:
                    os.remove(points_path)
                except OSError:
                    pass
            break
        time.sleep(5)
    else:
        print("[FAIL] 完成失败; 进行中的路线可用 completes_route is_delete=true 清理后重试", flush=True)

def cli_opts():
    opts = {"route": None, "target": None, "pace": None, "dry": False}
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry":
            opts["dry"] = True
        elif a in ("--route", "--target", "--pace") and i + 1 < len(args):
            opts[a[2:]] = args[i + 1]
            i += 1
        i += 1
    return opts


def main():
    global TARGET_M, BASE_PACE, PACE_MIN, PACE_MAX
    opts = cli_opts()
    if opts["target"]:
        try:
            TARGET_M = max(300, int(round(float(opts["target"]))))
        except ValueError:
            pass
    if opts["pace"]:
        try:
            BASE_PACE = int(round(float(opts["pace"])))
            # UI 里可自由选 4'00"~10'00"，围绕所选配速上下浮动，不再被默认上下限夹死
            PACE_MIN = max(180, BASE_PACE - 20)
            PACE_MAX = min(900, BASE_PACE + 20)
        except ValueError:
            pass
    if opts["dry"]:
        dry(opts["route"])
    else:
        run(opts["route"])


if __name__ == "__main__":
    main()
