"""image_edit: remaster a screenshot into a mockup with any OpenAI-compatible images API. Standard library only.

    cmd:         set IMAGE_API_BASE=https://your-endpoint/v1        (or a local proxy's http://127.0.0.1:PORT/v1)
                 set IMAGE_API_KEY=...                              (never print it, never commit it)
                 set IMAGE_MODEL=gpt-image-1                        (whatever your endpoint serves)
    PowerShell:  $env:IMAGE_API_BASE = "https://your-endpoint/v1"; $env:IMAGE_API_KEY = "..."

    python image_edit.py shot.png mockup.png --prompt @prompt.txt [--image ref2.png] [--size 1536x1024]
    (PowerShell: quote it, --prompt '@prompt.txt', since @ means splatting there)

POSTs multipart to {base}/images/edits and writes the first returned image, plus mockup.png.json with the prompt,
model and timing (provenance). See references/mockups.md for the prompt template and caveats.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def multipart(fields, files):
    boundary = "----jujitsu" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, path in files:
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{os.path.basename(path)}"'
                f"\r\nContent-Type: {mime}\r\n\r\n").encode()
        with open(path, "rb") as f:
            out += f.read() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--prompt", required=True, help="text, or @file")
    ap.add_argument("--image", action="append", default=[], help="extra reference image(s)")
    ap.add_argument("--model", default=os.environ.get("IMAGE_MODEL", "gpt-image-1"))
    ap.add_argument("--size", help="sent as-is; some endpoints ignore it and follow the prompt's aspect words")
    a = ap.parse_args()
    base, key = os.environ.get("IMAGE_API_BASE"), os.environ.get("IMAGE_API_KEY")
    if not base or not key:
        print("set IMAGE_API_BASE and IMAGE_API_KEY", file=sys.stderr)
        return 2
    prompt = open(a.prompt[1:], encoding="utf-8").read().strip() if a.prompt.startswith("@") else a.prompt
    images = [a.src] + a.image
    fields = [("model", a.model), ("prompt", prompt)] + ([("size", a.size)] if a.size else [])
    body, ctype = multipart(fields,
                            [("image[]" if len(images) > 1 else "image", p) for p in images])
    req = urllib.request.Request(base.rstrip("/") + "/images/edits", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": ctype})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode(errors='replace')[:800]}", file=sys.stderr)
        return 1
    item = data["data"][0]
    if item.get("b64_json"):
        blob = base64.b64decode(item["b64_json"])
    else:
        with urllib.request.urlopen(item["url"], timeout=600) as r:
            blob = r.read()
    with open(a.out, "wb") as f:
        f.write(blob)
    with open(a.out + ".json", "w", encoding="utf-8") as f:
        json.dump({"model": a.model, "prompt": prompt, "inputs": images, "seconds": round(time.time() - t, 1),
                   "returned": {k: data.get(k) for k in ("size", "quality", "usage")}}, f, indent=1)
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
