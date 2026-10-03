/* 《AIGC 帮我讲家乡》课堂素材生成器 · 自检脚本
 * 用法： node smoketest.js
 * 作用： 不开浏览器，直接打本地服务，逐项验证「首页 / 健康 / 合规拦截 / AI 改写 / 生成」五条链路。
 * 输出全英文/ASCII，避免 Windows 控制台 GBK 乱码；中文结果在浏览器里看更直观。
 */
"use strict";
const http = require("http");

const PORT = Number(process.env.PORT || 8788);

function req(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const data = body ? Buffer.from(JSON.stringify(body), "utf8") : null;
    const r = http.request(
      { host: "127.0.0.1", port: PORT, path: pathname, method,
        headers: Object.assign({ "Content-Type": "application/json; charset=utf-8" },
          data ? { "Content-Length": data.length } : {}) },
      (res) => {
        /* 二进制（图片/视频）必须收 Buffer，用字符串拼会被 utf8 解码截断 */
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const buf = Buffer.concat(chunks);
          let j = null;
          if (/json/.test(res.headers["content-type"] || "")) {
            try { j = JSON.parse(buf.toString("utf8")); } catch (e) { j = { _raw: buf.toString("utf8").slice(0, 300) }; }
          } else {
            j = { _raw: buf };
          }
          resolve({ code: res.statusCode, body: j });
        });
      }
    );
    r.on("error", reject);
    if (data) r.write(data);
    r.end();
  });
}

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("[OK]   " + name); }
  else { fail++; console.log("[NG]   " + name + (detail ? "  -> " + detail : "")); }
}

(async () => {
  console.log("=== Smoketest @ http://127.0.0.1:" + PORT + " ===\n");

  /* 1. 首页 */
  try {
    const r = await req("GET", "/api/health");
    check("GET /api/health reachable", r.code === 200, "code=" + r.code);
    const b = r.body;
    check("health.ok === true", b.ok === true, JSON.stringify(b));
    check("agnesReady === " + !!b.agnesReady, b.agnesReady === true, b.agnesProblem || "");
    check("deepseekReady === " + !!b.deepseekReady, b.deepseekReady === true, b.deepseekProblem || "");
    check("no key value leaked to client", !JSON.stringify(b).match(/sk-[A-Za-z0-9]{8}|AGNES_API_KEY_[A-Za-z]/), "possible key leak!");
    console.log("       agnesProblem    = " + (b.agnesProblem || "none"));
    console.log("       deepseekProblem = " + (b.deepseekProblem || "none"));
    console.log("       styleCount      = " + b.styleCount);
  } catch (e) {
    check("GET /api/health reachable", false, e.message);
    console.log("\n[NG] Server not running. Please double-click 启动.bat first.");
    process.exit(1);
  }

  /* 2. 合规拦截（不需要 key，必须立刻拦住） */
  const blocked = ["我想画一张校园霸凌的画面", "来的可是毒品市场", "给我画个自杀场景"];
  for (const bad of blocked) {
    const r = await req("POST", "/api/ai-assist", { idea: bad, style: "扁平插画", kind: "image" });
    const b = r.body;
    check("block \"" + bad + "\"", b.ok === false && b.blocked === true, JSON.stringify(b).slice(0, 160));
  }

  /* 3. AI 改写（需要 DeepSeek key） */
  const r2 = await req("POST", "/api/ai-assist", {
    idea: "我们家乡有一座老街，青砖房子，早上有人卖早餐，想做上课投影用的图",
    style: "扁平插画", kind: "image",
  });
  const b2 = r2.body;
  if (b2.configProblem) {
    console.log("\n[SKIP] AI assist needs DeepSeek key -> " + b2.configProblem);
  } else {
    check("POST /api/ai-assist returns ok", b2.ok === true, JSON.stringify(b2).slice(0, 200));
    if (b2.ok) {
      const pr = String(b2.prompt || "");
      check("prompt is english (no CJK) + has style suffix",
        !/[\u4e00-\u9fa5]/.test(pr) && /[a-zA-Z]{8}/.test(pr) && pr.length > 80 &&
        /children|illustration|style|background/i.test(pr),
        "len=" + pr.length + " tail=" + pr.slice(-60));
      check("tip present", !!b2.tip, String(b2.tip));
      console.log("       prompt = " + (b2.prompt || "").slice(0, 220));
      console.log("       tip    = " + (b2.tip || ""));
    }
  }

  /* 4. 生成图片（需要 Agnes key） */
  const r3 = await req("POST", "/api/generate", {
    prompt: "a quiet old stone street at dawn, mist on grey brick houses, flat illustration, soft blue-grey with warm orange accents, clean white background, no text",
    kind: "image", ratio: "16:9",
  });
  const b3 = r3.body;
  if (b3.configProblem) {
    console.log("\n[SKIP] Image generation needs Agnes key -> " + b3.configProblem);
  } else {
    check("POST /api/generate image ok", b3.ok === true, JSON.stringify(b3).slice(0, 240));
    if (b3.ok) {
      check("image files returned", Array.isArray(b3.files) && b3.files.length > 0, JSON.stringify(b3.files));
      let mr = null, mErr = "";
      try {
        mr = await req("GET", b3.files && b3.files[0] ? decodeURIComponent(b3.files[0]) : "/media/");
      } catch (e) { mErr = e.code || e.message; }
      check("GET " + b3.files[0] + " downloadable",
        !!mr && mr.code === 200 && Buffer.isBuffer(mr.body._raw) && mr.body._raw.length > 50000,
        mErr ? "network error: " + mErr : ("code=" + (mr && mr.code) + (mr && mr.body._raw ? " bytes=" + mr.body._raw.length : "")));
    }
  }

  /* 5. 生成短视频（需要 Agnes key） */
  const r4 = await req("POST", "/api/generate", {
    prompt: "sunrise over an ancient town rooftops, slow drift, flat illustration, no text",
    kind: "video", ratio: "16:9", duration: 30,
  });
  const b4 = r4.body;
  if (b4.configProblem) {
    console.log("\n[SKIP] Video generation needs Agnes key -> " + b4.configProblem);
  } else {
    check("POST /api/generate video ok", b4.ok === true, JSON.stringify(b4).slice(0, 240));
    if (b4.ok) {
      check("video duration clamped to 4..12", b4.duration >= 4 && b4.duration <= 12, "duration=" + b4.duration);
      check("video files returned", Array.isArray(b4.files) && b4.files.length > 0, JSON.stringify(b4.files));
    }
  }

  console.log("\n=== Result: " + pass + " passed, " + fail + " failed ===\n");
  process.exit(fail ? 1 : 0);
})();
