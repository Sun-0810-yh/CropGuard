#!/usr/bin/env python3
"""把 xtensa 工具链（115MB）下到本目录，供 PlatformIO 离线安装。

为什么单独写这一个脚本，而不用 pio 自带下载器 / pio_seed_cache.py：
  1. pio 的镜像列表里有 github.com 与 usc1.contabostorage.com，本机实测
     github 被拒(10061)、usc1 连 DNS 都解析不了 -> pio 会一路失败；
  2. requests 会自动读 Windows 注册表里的系统代理(127.0.0.1:65532)，
     而那个代理转发已失效 -> 必须在进程内设置 NO_PROXY 才能直连；
  3. 本脚本只往**工作区内**写文件，不碰 ~/.platformio（不需要提权、不抢包锁），
     中途被打断也不会留下任何锁。

用法:
    D:\\Dev Env\\Python\\Conda\\envs\\pest-gpu\\python.exe ESP32\\tools\\fetch_toolchain.py

成功后产物: ESP32/tools/toolchain-xtensa-esp32.tar.gz（sha256 已校验）
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time

# ---- 必须在 import requests 之前/之后都设置：requests 在发请求时读这些变量 ----
os.environ["NO_PROXY"] = (
    "dl.registry.platformio.org,platformio.org,contabostorage.com,"
    "dl.registry.ns3.platformio.org,dl.registry.nm1.platformio.org,"
    "sin1.contabostorage.com,eu2.contabostorage.com,github.com"
)
os.environ["no_proxy"] = os.environ["NO_PROXY"]
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

import requests  # noqa: E402  (必须在清代理之后导入)

URL = (
    "https://dl.registry.platformio.org/download/espressif/tool/toolchain-xtensa-esp32/"
    "8.4.0+2021r2-patch5/toolchain-xtensa-esp32-windows_amd64-8.4.0+2021r2-patch5.tar.gz"
)
SHA256 = "abc98e1765139746154dc2927e3438eb7a65478909463210f4b5fb984cee0491"
UA = {"User-Agent": "fetch-toolchain/1.0"}
THREADS = 16
RETRIES = 6

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "toolchain-xtensa-esp32.tar.gz")
PART = OUT + ".part"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def resolve_mirror() -> str:
    """HEAD 不跟随跳转，拿到 registry 分配的真实镜像地址。"""
    resp = requests.head(URL, headers=UA, allow_redirects=False, timeout=(10, 30))
    loc = resp.headers.get("Location")
    if not loc:
        raise SystemExit(f"[FATAL] registry 未返回跳转 (HTTP {resp.status_code})")
    print(f"[1/4] 镜像: {loc.split('/')[2]}", flush=True)
    return loc


def main() -> int:
    if os.path.isfile(OUT) and os.path.getsize(OUT) > 0:
        print("[0/4] 已存在文件，校验中 ...", flush=True)
        if sha256_of(OUT) == SHA256:
            print(f"[SKIP] 已下载且 sha256 正确: {OUT}", flush=True)
            return 0
        print("[0/4] 已有文件校验失败，重新下载", flush=True)

    mirror = resolve_mirror()

    head = requests.head(mirror, headers=UA, allow_redirects=True, timeout=(10, 30))
    total = int(head.headers.get("Content-Length", 0) or 0)
    print(f"      大小 {total / 1048576:.1f} MB, Accept-Ranges={head.headers.get('Accept-Ranges')}", flush=True)
    if total <= 0:
        raise SystemExit("[FATAL] 镜像未返回 Content-Length")

    print(f"[2/4] {THREADS} 线程分块下载 -> {os.path.basename(PART)}", flush=True)
    with open(PART, "wb") as fp:
        fp.truncate(total)

    lock = threading.Lock()
    progress = [0] * THREADS      # 每块已写入字节（用于实时进度显示）
    errors: list[str] = []
    chunk = (total + THREADS - 1) // THREADS

    def worker(idx: int) -> None:
        start = idx * chunk
        end = min(start + chunk, total) - 1
        if start > end:
            return
        want = end - start + 1
        for attempt in range(1, RETRIES + 1):
            try:
                got = 0
                with lock:
                    progress[idx] = 0          # 重试时清零，避免重复计数
                with requests.get(
                    mirror, headers={**UA, "Range": f"bytes={start}-{end}"},
                    stream=True, timeout=(10, 60),
                ) as resp:
                    resp.raise_for_status()
                    with open(PART, "r+b") as fp:
                        fp.seek(start)
                        for piece in resp.iter_content(65536):
                            if piece:
                                fp.write(piece)
                                got += len(piece)
                                with lock:
                                    progress[idx] = got
                if got != want:
                    raise IOError(f"只拿到 {got}/{want} 字节")
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == RETRIES:
                    with lock:
                        errors.append(f"块{idx}: {type(exc).__name__}: {str(exc)[:70]}")
                    return
                time.sleep(2 * attempt)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(THREADS)]
    t0 = time.time()
    for th in threads:
        th.start()
    while any(th.is_alive() for th in threads):
        time.sleep(5)
        el = max(time.time() - t0, 0.1)
        received = sum(progress)
        print(
            f"      {received / 1048576:6.1f}/{total / 1048576:.1f} MB "
            f"({received / total * 100:5.1f}%)  {received / 1024 / el:6.1f} KB/s  "
            f"用时 {el / 60:.1f} min",
            flush=True,
        )
    for th in threads:
        th.join()

    if errors:
        print("[!] 有分块失败:", flush=True)
        for err in errors:
            print("    " + err, flush=True)

    print("[3/4] 校验 sha256 ...", flush=True)
    actual = sha256_of(PART)
    if actual != SHA256:
        print(f"[FATAL] sha256 不匹配\n  期望 {SHA256}\n  实际 {actual}", flush=True)
        print(f"  半成品保留在 {PART}（下次会覆盖重下）", flush=True)
        return 2

    print("[4/4] 完成", flush=True)
    os.replace(PART, OUT)
    print(f"OK  {OUT}  ({os.path.getsize(OUT) / 1048576:.1f} MB)  sha256 通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
