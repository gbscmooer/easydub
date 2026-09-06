import { useCallback, useRef, useState } from "react";

const LANGS = [
  { code: "en", label: "英语" },
  { code: "zh", label: "中文" },
  { code: "ja", label: "日语" },
  { code: "ko", label: "韩语" },
  { code: "es", label: "西班牙语" },
];

const STAGE_LABEL = {
  upload: "上传完成",
  asr: "语音识别",
  translate: "AI 翻译",
  tts: "语音合成",
  retry: "超时重译",
  mix: "音轨合成",
  lipsync: "口型同步",
  mux: "封装成品",
  done: "完成",
};

export default function App() {
  const [file, setFile] = useState(null);
  const [lang, setLang] = useState("en");
  const [job, setJob] = useState(null); // {state, stage, error}
  const [jobId, setJobId] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const poll = useCallback(async (id) => {
    for (;;) {
      let data;
      try {
        const r = await fetch(`/api/jobs/${id}`);
        data = await r.json();
      } catch {
        data = { state: "error", error: "网络错误" };
      }
      setJob(data);
      if (data.state === "done" || data.state === "error") return;
      await new Promise((r) => setTimeout(r, 1500));
    }
  }, []);

  const submit = useCallback(async () => {
    if (!file) return;
    setJob({ state: "queued", stage: "upload" });
    const body = new FormData();
    body.append("video", file);
    body.append("lang", lang);
    try {
      const r = await fetch("/api/jobs", { method: "POST", body });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${r.status}`);
      }
      const { job_id: id } = await r.json();
      setJobId(id);
      await poll(id);
    } catch (e) {
      setJob({ state: "error", error: String(e.message || e) });
    }
  }, [file, lang, poll]);

  const reset = () => {
    setFile(null);
    setJob(null);
    setJobId(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  };

  const busy = job && job.state !== "done" && job.state !== "error";

  return (
    <div className="wrap">
      <h1>EasyDub 视频翻译</h1>
      <p className="sub">拖入口播视频 → 选目标语言 → 自动配音 + 双语字幕</p>

      {!jobId && (
        <>
          <div
            className={`drop ${dragOver ? "over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
          >
            {file ? (
              <p>已选择：{file.name}</p>
            ) : (
              <p>把视频拖到这里，或点击选择（MP4/MOV）</p>
            )}
            <input
              ref={inputRef}
              type="file"
              accept=".mp4,.mov,video/mp4,video/quicktime"
              hidden
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </div>
          <div className="row">
            <select value={lang} onChange={(e) => setLang(e.target.value)}>
              {LANGS.map((l) => (
                <option key={l.code} value={l.code}>
                  翻译成{l.label}
                </option>
              ))}
            </select>
            <button disabled={!file || busy} onClick={submit}>
              开始翻译
            </button>
          </div>
        </>
      )}

      {job && busy && (
        <div className="progress">
          <div className="spinner" />
          <div className="bar">
            <div className="bar-fill" style={{ width: `${job.percent || 5}%` }} />
          </div>
          <p>
            {STAGE_LABEL[job.stage] || job.stage || "排队中"}
            （{job.percent || 5}%，任务 {jobId}）
          </p>
        </div>
      )}

      {job?.state === "done" && (
        <div className="result">
          <video src={`/api/jobs/${jobId}/result`} controls autoPlay />
          <div className="row">
            <a className="btn" href={`/api/jobs/${jobId}/result`} download>
              下载成品
            </a>
            <button onClick={reset}>再译一个</button>
          </div>
        </div>
      )}

      {job?.state === "error" && (
        <div className="error">
          <p>出错了：{job.error}</p>
          <button onClick={reset}>重试</button>
        </div>
      )}
    </div>
  );
}
