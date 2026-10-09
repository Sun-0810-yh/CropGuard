#!/usr/bin/env python3
"""把已下好的 xtensa 工具链灌进 PlatformIO 下载缓存，让 pio 离线完成安装。

前置：ESP32/tools/toolchain-xtensa-esp32.tar.gz 已存在且 sha256 正确
     （由同目录 fetch_toolchain.py 下载）

原理（复用 pio_seed_cache.py 里已验证的算法）：
    下载缓存文件名 = sha1(mirror_location + sha256)
    %USERPROFILE%\\.platformio\\.cache\\downloads\\<该 sha1>
    再把 HEAD 结果写进 pio 的 http 缓存，pio 第一次就会命中这个镜像，
    于是它直接解包安装（自己写 .piopm 等元数据），全程不联网。

用法：
    & "$env:USERPROFILE\\.platformio\\penv\\Scripts\\python.exe" ESP32\\tools\\install_toolchain.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARBALL = os.path.join(HERE, "toolchain-xtensa-esp32.tar.gz")
SHA256 = "abc98e1765139746154dc2927e3438eb7a65478909463210f4b5fb984cee0491"
URL = (
    "https://dl.registry.platformio.org/download/espressif/tool/toolchain-xtensa-esp32/"
    "8.4.0+2021r2-patch5/toolchain-xtensa-esp32-windows_amd64-8.4.0+2021r2-patch5.tar.gz"
)


def load_seeder():
    """把同目录的 pio_seed_cache.py 当库加载（它只有常量与函数，没有副作用）。"""
    path = os.path.join(HERE, "pio_seed_cache.py")
    if not os.path.isfile(path):
        raise SystemExit(f"[FATAL] 找不到 {path}")
    spec = importlib.util.spec_from_file_location("pio_seed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    if not os.path.isfile(TARBALL):
        raise SystemExit(f"[FATAL] 缺少 {TARBALL}（先跑 fetch_toolchain.py）")

    actual = sha256_of(TARBALL)
    if actual != SHA256:
        raise SystemExit(f"[FATAL] 本地压缩包 sha256 不匹配\n  期望 {SHA256}\n  实际 {actual}")
    print(f"[1/4] 本地压缩包校验通过  {os.path.getsize(TARBALL) / 1048576:.1f} MB")

    seed = load_seeder()
    mirrors = seed.enumerate_mirrors(URL)
    location, checksum, mirror_name = mirrors[0]
    print(f"[2/4] 枚举到 {len(mirrors)} 个镜像，首选 {location.split('/')[2]}")

    keys = [
        hashlib.sha1((loc + sha).encode("utf-8")).hexdigest()
        for loc, sha, _ in mirrors
    ]
    busy = [k for k in keys if (seed.DL_CACHE_DIR / f"{k}.lock").exists()]
    if busy:
        raise SystemExit(
            f"[FATAL] pio 正在下载这个包（{busy[0][:12]}.lock 还在），"
            "先停掉它再跑本脚本，免得两边打架"
        )

    seed.DL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for key in keys:
        dst = seed.DL_CACHE_DIR / key
        if dst.exists():
            print(f"      缓存已有 {key[:12]}，跳过")
            continue
        try:
            os.link(TARBALL, dst)          # 硬链接：同一份数据，不额外占空间
            how = "硬链接"
        except OSError:
            shutil.copy2(TARBALL, dst)     # 跨卷时退回复制
            how = "复制"
        print(f"      写入缓存 {key[:12]}（{how}）")

    print("[3/4] 写 pio 的 http 缓存（把它钉到我们用的镜像上）")
    seed.seed_http_cache(URL, location, checksum, mirror_name)

    print("[4/4] 完成。现在 pio 会直接从缓存解包安装，不再联网。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
