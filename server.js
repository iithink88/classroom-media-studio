/* 《AIGC 帮我讲家乡》课堂素材生成器 · 本地服务（Node 零依赖）
 *  - 学生浏览器/手机 → 本服务 → Agnes CLI（图片/视频）+ DeepSeek（提示词润色与合规审核）
 *  - 两个 API key 只留在本机服务端，绝不下发到浏览器，学生端 grep 不到任何密钥
 */
"use strict";
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const ROOT = __dirname;
const PUBLIC = path.join(ROOT, "public");
const OUT = path.join(ROOT, "学生作品素材");
fs.mkdirSync(OUT, { recursive: true });

/* ---------- 配置：config.json > 环境变量 ---------- */
const DEFAULT_CFG = { port: 8788, pythonPath: "", agnesKey: "", deepseekKey: "" };
let CFG = Object.assign({}, DEFAULT_CFG);
for (const f of ["config.json"]) {
  try {
    Object.assign(CFG, JSON.parse(fs.readFileSync(path.join(ROOT, f), "utf8")));
    break;
  } catch (e) { /* 这个文件不存在就换下一个 */ }
}
if (!process.env.AGNES_API_KEY && !CFG.agnesKey) CFG.agnesKey = "";
const AGNES_KEY = (CFG.agnesKey || process.env.AGNES_API_KEY || "").trim();
const DS_KEY = (CFG.deepseekKey || process.env.DEEPSEEK_API_KEY || "").trim();
const PORT = Number(CFG.port || 8788);

/* 可搬运：Agnes CLI / Python 解都能自动找，找不到再退回本机默认路径 */
function firstExisting(list) {
  for (const p of list) { try { if (p && fs.existsSync(p)) return p; } catch (e) {} }
  return null;
}
const HOME_DIR = process.env.USERPROFILE || process.env.HOME || "";
function resolveAgnesCli() {
  if (process.env.AGNES_CLI) return process.env.AGNES_CLI;
  return firstExisting([
    HOME_DIR ? path.join(HOME_DIR, ".workbuddy", "skills", "agnes-media-skill", "scripts", "agnes_media.py") : null,
    path.join(ROOT, "..", "agnes-media-skill", "scripts", "agnes_media.py"),
    path.join(ROOT, "agnes", "scripts", "agnes_media.py"),
    path.join(ROOT, "agnes", "agnes_media.py"),
    "agnes_media.py",
  ]);
}
function resolvePython() {
  if (CFG.pythonPath) return CFG.pythonPath;
  if (process.env.PY_EXE) return process.env.PY_EXE;
  const bundled = firstExisting([path.join(ROOT, "运行时", "python.exe"), path.join(ROOT, "agnes", "python.exe")]);
  if (bundled) return bundled;
  return process.platform === "win32" ? "python.exe" : "python3";
}
const AGNES_CLI = resolveAgnesCli();

/* key 有效性：占位串 / 非 ASCII 都会在调 API 时以很隐晦的方式炸掉，这里提前拦 */
function isPlaceholder(v) {
  return /填这里|CHANGE_ME|CHANGEME|YOUR_KEY|<你的|your[_-]?key/i.test(v || "");
}
function nonAscii(v) {
  return ![...(v || "")].every((c) => c.codePointAt(0) < 128);
}
function keyProblem(k) {
  if (!k) return "还没配";
  if (isPlaceholder(k)) return "还是占位符，请在 config.json 里换成真实 key";
  if (nonAscii(k)) return "含非 ASCII 字符（多半是被复制成了占位串），请重贴一次真实 key";
  return "";
}

/* ---------- 合规：敏感词（三层审核的第一层） ---------- */
const BLOCK_WORDS = [
  "色情", "裸体", "裸露", "情色", "挑逗", "暧昧", "内衣", "泳装", "性感", "成人内容",
  "暴力", "血腥", "屠杀", "尸体", "流血", "枪支", "枪械", "军火", "爆炸", "恐怖主义",
  "毒品", "吸毒", "酒精", "香烟",
  "自杀", "自残", "割腕", "厌食", "暴食",
  "赌博", "赌场", "六合彩", "私彩", "诈骗", "黑客", "病毒",
  "邪教", "法轮", "封建迷信", "算命", "风水", "驱鬼", "鬼怪", "僵尸", "丧尸", "恐怖片",
  "纳粹", "希特勒",
  "歧视", "仇恨", "辱骂", "恶搞", "整蛊", "网络暴力", "人肉搜索",
  "厌学", "逃学", "早恋", "校园霸凌", "霸凌",
  "台独", "港独", "藏独", "疆独", "分裂国家", "政治人物", "国家领导人", "领导人肖像",
  "翻墙", "武器", "导弹", "坦克", "军队",
];
function localCheck(text) {
  for (const w of BLOCK_WORDS) if (text && text.includes(w)) return w;
  return null;
}

/* ---------- 风格串：与第 1 单元课件同一套画风 ---------- */
const STYLES = {
  "扁平插画": "flat illustration style, flat design, clean with no clutter, soft blue-grey tones with warm orange accents, clean white background, children's textbook illustration quality, bright and clear, high recognizability",
  "儿童教材插图": "children's textbook illustration, friendly flat vector, soft blue-grey and warm orange palette, clean white background, warm and approachable",
  "国风淡彩": "Chinese traditional ink wash painting, soft light colors, elegant and calm, flat vector feel, clean white background",
  "清新写实": "soft realistic lighting, fresh natural colors, clean composition, children's textbook illustration quality, no text",
  "手绘线条": "hand-drawn line art, sketch style, soft blue-grey ink with warm orange accents, clean white background",
  "线描简笔": "minimal line drawing, simple strokes, blue-grey lines with a single warm orange accent, clean white background",
};
function stylePrefix(name) { return STYLES[name] || STYLES["扁平插画"]; }

const SYSTEM_PROMPT = `你是中学信息科技课堂（七年级）的素材提示词助手，服务于一节课《AIGC 帮我讲家乡》：学生要用「百度搜索取证 + 生成式 AI 生成」做一份介绍自己家乡的演示文稿或短视频。

[硬性合规要求，必须逐条满足]
1. 弘扬社会主义核心价值观（富强、民主、文明、和谐、自由、平等、公正、法治、爱国、敬业、诚信、友善），内容健康向上；
2. 符合中国初中生（12–15 岁）的素养与审美，画面温暖、积极、有生活气息；
3. 禁止：色情低俗、暴力血腥、违法犯罪、恐怖主义、毒品、自残自杀、校园霸凌、厌学早恋、封建迷信、宗教极端、民族/地域歧视、政治敏感、国家领导人形象、以及任何违反中国法律法规与主流内容平台规则的内容；
4. 禁止生成他人肖像、名人形象、受版权保护的角色；不得诱导侵权；
5. 画面不得出现任何文字、字母、数字、logo、水印（课堂投影需要干净画面，且避免错别字）；
6. 只围绕「家乡、风景、传统美食、非遗手艺、四季变化、城乡新貌、同学伙伴、书香校园、科技强国」这类积极主题。

[任务]
把学生用中文写的朴素想法（可能很短、很口语）改写成一个高质量的英文生成提示词。要求：
- 结构：主体 + 场景 + 风格 + 光照 + 构图；
- 保持学生原意里的地名、事物、季节、时间等关键信息，不得擅自编造地名或事件；
- 不要输出解释、不要输出选项，只输出最终提示词正文；
- 必须原样追加这一句风格描述（放在末尾，不要翻译它）：__STYLE__
- 若学生请求违反上述任何一条，返回 ok:false 并给一句中文理由。

[输出]
严格只输出一个 JSON 对象，不要包裹代码块，不要任何多余文字：
{"ok":true|false,"reason":"（仅 ok:false 时填写）一句中文理由","prompt":"最终英文提示词","tip":"一句给学生的中文建议，告诉他画面里哪里可以更具体"}`;

/* ---------- 工具 ---------- */
function deepSeekChat(messages, timeoutMs) {
  return new Promise((resolve, reject) => {
    const p = keyProblem(DS_KEY);
    if (p) return reject(new Error("DEEPSEEK_KEY_BAD " + p));
    const body = JSON.stringify({
      model: "deepseek-chat",
      messages,
      response_format: { type: "json_object" },
      temperature: 0.8,
    });
    const req = https.request({
      host: "api.deepseek.com", path: "/chat/completions", method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + DS_KEY, "Content-Length": Buffer.byteLength(body) },
      timeout: timeoutMs || 60000,
    }, (res) => {
      let d = "";
      res.on("data", (c) => (d += c));
      res.on("end", () => {
        try {
          const j = JSON.parse(d);
          if (j.error) return reject(new Error("DEEPSEEK_ERROR " + (j.error.message || d.slice(0, 200))));
          resolve(j.choices[0].message.content);
        } catch (e) { reject(new Error("DEEPSEEK_PARSE " + d.slice(0, 200))); }
      });
    });
    req.on("timeout", () => { req.destroy(new Error("DEEPSEEK_TIMEOUT")); });
    req.on("error", reject);
    req.end(body);
  });
}
function parseJsonObject(text) {
  const s = String(text || "").trim();
  const a = s.indexOf("{"), b = s.lastIndexOf("}");
  if (a < 0 || b < 0) throw new Error("NO_JSON");
  return JSON.parse(s.slice(a, b + 1));
}
/* 具体解释器由上面的 resolvePython() 统一决定，这里只做调用前后的可诊断输出 */
function looksGood(j) {
  return !!j && j.ok !== false && (j.image_urls || j.local_paths || j.video_url || j.local_path || j.video_id);
}
function runAgnes(args, retries) {
  retries = typeof retries === "number" ? retries : 3;
  return new Promise((resolve, reject) => {
    const py = resolvePython();
    const env = Object.assign({}, process.env, {
      AGNES_API_KEY: AGNES_KEY,
      AGNES_OUTPUT_DIR: OUT,
    });
    execFile(py, [AGNES_CLI].concat(args), { env, maxBuffer: 1024 * 1024 * 32, timeout: 600000 },
      (err, stdout, stderr) => {
        /* 注意：CLI 遇到队列满会以「退出码 1」返回错误 JSON，
           只看 execFile 的 err 会漏掉，必须先解析 stdout 再判断。 */
        const s = String(stdout || "");
        const a = s.indexOf("{"), b = s.lastIndexOf("}");
        let j = null;
        if (a >= 0 && b >= 0) { try { j = JSON.parse(s.slice(a, b + 1)); } catch (e) {} }
        if (j && looksGood(j)) return resolve(j);

        const msg = j ? JSON.stringify(j.error || j.message || "") : (stderr || (err && err.message) || "");
        if (retries > 0 && /queue_full|队列已满|503|rate|limit|too many/i.test(msg)) {
          const wait = 30000;
          console.log("       （Agnes 排队/限流，等 " + (wait / 1000) + " 秒后重试，还剩 " + retries + " 次）");
          return setTimeout(() => runAgnes(args, retries - 1).then(resolve, reject), wait);
        }
        if (j) return reject(new Error("AGNES_FAIL " + msg.slice(0, 300)));
        console.log("       [诊断] 未解析出结果：exit=" + (err && err.code) + " stdout=" + JSON.stringify(s.slice(0, 200)) + " stderr=" + JSON.stringify(String(stderr || "").slice(0, 200)));
        reject(new Error("AGNES_CLI " + (stderr || (err && err.message) || "").slice(0, 400)));
      });
  });
}
/* 把 Agnes 的英文报错翻成人话 */
function humanizeAgnes(e) {
  const m = String(e && e.message || "");
  if (/queue_full|队列已满/.test(m)) return "视频队列满了（平台侧，不是代码问题）。让学生先做图片，一两分钟后再点一次生成通常就恢复。";
  if (/401|Unauthorized|invalid_api/i.test(m)) return "key 不对或已过期，请老师核对 config.json。";
  if (/403/.test(m)) return "账号没有这个模型的权限（视频/图片权限），请老师核对。";
  if (/ENOENT/.test(m)) return "没找到 Python 解释器。请在 config.json 里把 pythonPath 写成 python.exe 的完整路径。";
  if (/\[SIGTERM\]|killed|timed out|ETIMEDOUT/i.test(m)) return "生成超时了（视频通常 1–3 分钟）。让学生别关页面，再点一次；还是不行就先做图片。";
  if (/^AGNES_FAIL/.test(m)) return "平台侧返回了错误：" + m.replace(/^AGNES_FAIL\s*/, "").slice(0, 160);
  return m.slice(0, 240);
}
/* 作品可能被 CLI 放进 images/、videos/ 子目录，这里解析成 OUT 下的相对路径再对外暴露 */
function findUnderOut(target) {
  const p = path.resolve(OUT, target);
  if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
  const name = path.basename(p);
  let found = null;
  (function walk(dir) {
    if (found) return;
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const d = path.join(dir, e.name);
      if (e.isDirectory()) walk(d);
      else if (e.name === name) { found = d; return; }
    }
  })(OUT);
  return found;
}
function localUrlFor(anyPath) {
  const abs = findUnderOut(anyPath);
  if (!abs) return null;
  const rel = path.relative(OUT, abs).split(path.sep).join("/");
  return "/media/" + rel.split("/").map(encodeURIComponent).join("/");
}

/* ---------- 路由 ---------- */
function send(res, code, obj) {
  const body = Buffer.from(JSON.stringify(obj), "utf8");
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Content-Length": body.length });
  res.end(body);
}
function readBody(req) {
  return new Promise((resolve, reject) => {
    let d = "";
    req.on("data", (c) => { d += c; if (d.length > 4e6) req.destroy(); });
    req.on("end", () => { try { resolve(JSON.parse(d || "{}")); } catch (e) { resolve({}); } });
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  /* 课堂里一次异常不该让整个服务挂掉：整段兜底 */
  try {
  const url = decodeURIComponent(req.url.split("?")[0]);

  /* 静态：作品文件（从 OUT 目录读，防路径穿越） */
  if (url.startsWith("/media/")) {
    const rel = decodeURIComponent(url.slice("/media/".length)).replace(/^([/\\])+/, "");
    const file = path.resolve(OUT, rel);
    /* 防路径穿越：解析后必须仍在 OUT 之内 */
    if (file !== OUT && !file.startsWith(OUT + path.sep)) { res.writeHead(403); return res.end("forbidden"); }
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); return res.end("not found"); }
    const ext = path.extname(file).toLowerCase();
    const mime = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".mp4": "video/mp4" }[ext] || "application/octet-stream";
    const st = fs.statSync(file);
    res.writeHead(200, { "Content-Type": mime, "Content-Length": st.size });
    return fs.createReadStream(file).pipe(res);
  }

  /* 首页 */
  if (url === "/" || url === "/index.html") {
    const f = path.join(PUBLIC, "index.html");
    if (!fs.existsSync(f)) { res.writeHead(500); return res.end("index.html missing"); }
    const body = fs.readFileSync(f);
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Content-Length": body.length });
    return res.end(body);
  }

  /* 健康检查 + key 是否就位（只回状态，绝不回 key 内容） */
  if (url === "/api/health") {
    const ap = keyProblem(AGNES_KEY), dp = keyProblem(DS_KEY);
    return send(res, 200, {
      ok: true,
      agnesReady: !ap,
      deepseekReady: !dp,
      agnesProblem: ap,
      deepseekProblem: dp,
      styleCount: Object.keys(STYLES).length,
    });
  }

  /* AI 辅助写提示词（含合规审核） */
  if (url === "/api/ai-assist" && req.method === "POST") {
    const b = await readBody(req);
    const idea = String(b.idea || "").trim();
    if (!idea) return send(res, 400, { ok: false, message: "先写一句话，说说你想画什么。" });
    const hit = localCheck(idea);
    if (hit) return send(res, 200, { ok: false, blocked: true, message: '「' + hit + '」这类内容不符合中学生课堂与平台规范，换一个健康向上的主题试试，比如家乡的一处风景、一道小吃、一门老手艺。' });

    let raw;
    try {
      raw = await deepSeekChat([
        { role: "system", content: SYSTEM_PROMPT.replace("__STYLE__", stylePrefix(b.style)) },
        { role: "user", content: "学生原话：" + idea + "\n主题类型：" + (b.kind === "video" ? "短视频（4–12 秒，固定 720P，画面要能在一瞬间看懂）" : "图片（16:9 课堂投影）") },
      ]);
    } catch (e) {
      const m = String(e.message || "");
      if (m.startsWith("DEEPSEEK_KEY_BAD")) return send(res, 500, { ok: false, configProblem: m, message: "老师的 DeepSeek key 没配好（" + m.split(" ").slice(1).join(" ") + "）。请老师打开 config.json 核对后重启本工具。" });
      if (m.startsWith("DEEPSEEK_KEY_MISSING"))
        return send(res, 500, { ok: false, message: "服务端没检测到 DeepSeek key，请联系老师在 config.json 里填好。" });
      return send(res, 500, { ok: false, message: "AI 辅助失败：" + m });
    }
    let j;
    try { j = parseJsonObject(raw); } catch (e) { return send(res, 500, { ok: false, message: "AI 返回格式异常，请重试。", raw: String(raw).slice(0, 200) }); }

    if (j.ok === false || localCheck(j.prompt || "")) {
      const why = localCheck(j.prompt || "") || j.reason || "该内容不符合课堂与平台规范";
      return send(res, 200, { ok: false, blocked: true, message: "（AI 审核拦下的）" + why + "。可以改成：家乡的一处风景 / 一道老小吃 / 一门非遗手艺 / 放学路上的晚霞。", reason: why });
    }
    return send(res, 200, {
      ok: true,
      prompt: String(j.prompt || ""),
      tip: String(j.tip || ""),
      idea: idea,
    });
  }

  /* 生成图片 / 短视频 */
  if (url === "/api/generate" && req.method === "POST") {
    const b = await readBody(req);
    const prompt = String(b.prompt || "").trim();
    const kind = b.kind === "video" ? "video" : "image";
    if (!prompt) return send(res, 400, { ok: false, message: "提示词是空的。" });
    const hit = localCheck(prompt);
    if (hit) return send(res, 400, { ok: false, blocked: true, message: '提示词里含「' + hit + '」，这不符合中学生课堂与平台规范，请改一改。' });
    const ap = keyProblem(AGNES_KEY);
    if (ap) return send(res, 500, { ok: false, configProblem: ap, message: "老师的图片/视频服务 key 没配好（" + ap + "）。请老师打开 config.json 核对后重启本工具。" });

    try {
      let args, payload;
      if (kind === "image") {
        const ratio = ["16:9", "4:3", "1:1", "9:16"].indexOf(b.ratio) >= 0 ? b.ratio : "16:9";
        args = ["image-generate", "--prompt", prompt, "--ratio", ratio];
        payload = await runAgnes(args);
        const files = (payload.local_paths || []).map(localUrlFor).filter(Boolean);
        if (!files.length) throw new Error("AGNES_NO_LOCAL_FILE " + JSON.stringify(payload.save_errors || []).slice(0, 200));
        return send(res, 200, {
          ok: true, kind: "image", files,
          remote: payload.image_urls || [],
          model: payload.model || "",
          saveErrors: payload.save_errors || [],
          prompt,
        });
      } else {
        let dur = Number(b.duration);
        if (!isFinite(dur)) dur = 6;
        dur = Math.max(4, Math.min(12, Math.round(dur)));
        const ratio = ["16:9", "4:3", "1:1", "9:16", "3:4", "21:9"].indexOf(b.ratio) >= 0 ? b.ratio : "16:9";
        args = ["video-generate", "--prompt", prompt, "--duration", String(dur), "--aspect-ratio", ratio, "--timeout-seconds", "420"];
        payload = await runAgnes(args);
        const files = [localUrlFor(payload.local_path)].filter(Boolean);
        if (!files.length) throw new Error("AGNES_NO_LOCAL_VIDEO");
        return send(res, 200, {
          ok: true, kind: "video", files,
          remote: payload.video_url ? [payload.video_url] : [],
          videoId: payload.video_id || "",
          duration: dur,
          model: payload.model || "",
          prompt,
        });
      }
    } catch (e) {
      const m = String(e.message || "");
      const known = /AGNES_FAIL|AGNES_CLI|AGNES_NO_JSON|AGNES_JSON|AGNES_EMPTY|AGNES_NO_LOCAL|AGNES_/;
      if (known.test(m)) return send(res, 502, { ok: false, message: humanizeAgnes(e), detail: m.slice(0, 300) });
      return send(res, 502, { ok: false, message: "生成失败：" + m.slice(0, 300) });
    }
  }

  res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
  res.end("404");
  } catch (e) {
    console.error("[" + new Date().toISOString() + "] handler error: " + (e && e.message));
    try {
      if (!res.headersSent) res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, message: "服务端处理出错：" + String(e && e.message || e).slice(0, 200) }));
    } catch (_) {}
  }
});

let listenPort = PORT;
function tryListen() {
  server.once("error", (e) => {
    if (e.code === "EADDRINUSE" && listenPort < PORT + 9) {
      listenPort++;
      console.log("端口 " + (listenPort - 1) + " 被占用，自动改到 " + listenPort + " ...");
      tryListen();
    } else {
      console.error("启动失败：" + (e && e.message));
      console.error("→ 关掉占用端口的软件，或改 config.json 里的 port 后重试。");
      process.exit(1);
    }
  });
  server.listen(listenPort, "0.0.0.0", () => {
  const nets = require("os").networkInterfaces();
  const ips = [];
  Object.keys(nets).forEach((k) => nets[k].forEach((n) => { if (n.family === "IPv4" && !n.internal) ips.push(n.address); }));
  console.log("==== 课堂素材生成器已启动 ====");
  if (listenPort !== PORT) console.log("（原定端口 " + PORT + " 被占用，已改用 " + listenPort + "）");
  console.log("本机打开 : http://localhost:" + listenPort);
  ips.slice(0, 3).forEach((ip) => console.log("同 WiFi 学生手机/平板 : http://" + ip + ":" + listenPort));
  console.log("作品保存 : " + OUT);
  console.log("Agnes CLI : " + (AGNES_CLI || "【没找到】把 agnes_media.py 放到本目录 agnes/ 下，或设环境变量 AGNES_CLI"));
  console.log("Python    : " + resolvePython());
  const ap = keyProblem(AGNES_KEY), dp = keyProblem(DS_KEY);
  console.log("Agnes key : " + (ap ? "【没配好】" + ap : "已配置（纯 ASCII）"));
  console.log("DeepSeek : " + (dp ? "【没配好】" + dp : "已配置（纯 ASCII）"));
  if (ap || dp) console.log("→ 学生现在点了生成会失败，请先修上面两项，再重启本工具。");
  console.log("按 Ctrl+C 停止");
  });
}
tryListen();
