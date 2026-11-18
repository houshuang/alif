"""Run the production snap/pre-read scan call on saved page photos with several Gemini models."""
import json, sys, time, glob, random
sys.path.insert(0, "/opt/alif/backend")
import litellm
from app.services import ocr_service as o

MODELS = sys.argv[1].split(",")
OUT = "/tmp/ocr_exp_out.jsonl"
random.seed(7)
old = sorted(glob.glob("data/textbook-uploads/45d1a7ab/*.jpg")) + sorted(glob.glob("data/textbook-uploads/6cdfd0e2/*.jpg"))
pages = sorted(glob.glob("data/textbook-uploads/d5db39f5/*.jpg")) + random.sample(old, 8)
usage = {}
_orig = litellm.completion
def comp(*a, **kw):
    r = _orig(*a, **kw)
    u = getattr(r, "usage", None)
    usage["calls"] = usage.get("calls", 0) + 1
    usage["in"] = usage.get("in", 0) + (getattr(u, "prompt_tokens", 0) or 0)
    usage["out"] = usage.get("out", 0) + (getattr(u, "completion_tokens", 0) or 0)
    return r
litellm.completion = comp
_vision = o._call_gemini_vision
o._save_snap_failure = lambda *a, **k: None
for model in MODELS:
    def vision(image_bytes, **kw):
        kw["model_override"] = "gemini/" + model
        return _vision(image_bytes, **kw)
    o._call_gemini_vision = vision
    for p in pages:
        usage.clear(); t0 = time.time()
        try:
            res = o.extract_text_and_translation(open(p, "rb").read()); err = None
        except Exception as e:
            res = {}; err = repr(e)[:300]
        rec = {"model": model, "page": p, "s": round(time.time() - t0, 1), "err": err, **usage,
               "arabic": res.get("arabic_text", ""), "english": res.get("translation_en", ""),
               "n_sent": len(res.get("sentences") or []), "n_links": sum(len(s.get("links") or []) for s in res.get("sentences") or [])}
        open(OUT, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(model, p[-20:], rec["s"], err, usage, len(rec["arabic"]), rec["n_sent"], rec["n_links"], flush=True)
print("DONE", flush=True)
