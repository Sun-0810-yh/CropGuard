#!/usr/bin/env python3
"""用多线程下载器把 PlatformIO 的包直接灌进它的下载缓存，绕开 pio 自带的单连接下载器。

背景（2026-10 在本机实测）:
    * pio 单条长连接下载 toolchain-xtensa-esp-elf 只有 ~50 KB/s；
    * 同一台机器、同一个文件，多连接能跑满 ~1.5 MB/s（30 倍）；
    * 官方 registry 会把包重定向到多个镜像（Contabo 美/欧、GitHub、pio ns3），
      实测 4 个镜像速度几乎一致 —— 所以"换镜像源"没用，瓶颈在 pio 的下载器本身。

原理（已用两个样本反推出并验证）:
    1. PlatformIO 先对 download_url 发 HEAD（不跟随跳转），拿到真实 CDN 地址
       Location 和 X-PIO-Content-SHA256；
    2. 下载缓存文件名 = sha1(Location + sha256)，存放于
       %USERPROFILE%\\.platformio\\.cache\\downloads\\<sha1>；
    3. 安装时若该文件已存在，直接解压，完全不联网
       （见 platformio/package/manager/_download.py 的 download()）。

用法::

    python scripts/pio_seed_cache.py --preset espidf --dry-run   # 只看清单和大小
    python scripts/pio_seed_cache.py --preset espidf             # 预取本工程所有包
    python scripts/pio_seed_cache.py platformio/tool/toolchain-xtensa-esp32@8.4.0+2021r2-patch5
    python scripts/pio_seed_cache.py --url https://dl.registry.platformio.org/download/...

注意:
    * 已存在的 <key>.lock / tmp* 不会被本脚本改动；
    * 预取错的版本不会导致装错包（缓存 key 里含精确 URL），只是白占缓存，30 天后自动清理。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests

try:  # platformio 的 penv 里一定有 semantic_version
    from semantic_version import SimpleSpec, Version
except ImportError:  # pragma: no cover
    SimpleSpec = None

CACHE_DIR = Path(os.environ["USERPROFILE"]) / ".platformio" / ".cache"
DL_CACHE_DIR = CACHE_DIR / "downloads"
API_BASE = "https://api.registry.platformio.org/v3/packages"
UA = {"User-Agent": "pio-seed-cache/1.0"}
TIMEOUT = (15, 60)

# 分块下载共用一个 Session（连接复用）。
# 注意：原实现只在这里漏了 SESSION 与 HTTP_CACHE_DIR 两个名字，
# 导致「分块多线程下载」每次全部抛 NameError 后回退单连接，
# 以及下载成功后 seed_http_cache() 直接 NameError 崩溃退出。
SESSION = requests.Session()
HTTP_CACHE_DIR = CACHE_DIR / "http"

# 输出重定向到文件时（powershell > 或日志）避免中文编码报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # pylint: disable=broad-except
        pass

# esp32dev + framework=espidf 需要的包（来自 platform = espressif32@7.1.3 的 platform.json）
PRESET_ESPIDF = [
    "platformio/tool/toolchain-xtensa-esp-elf@15.2.0+20251204",
    "espressif/tool/toolchain-xtensa-esp32@8.4.0+2021r2-patch5",
    "platformio/tool/toolchain-esp32ulp@~1.23800.0",
    "platformio/tool/framework-espidf@~4.60100.0",
    "platformio/tool/tool-esptoolpy@~2.41100.0",
    "platformio/tool/tool-cmake@~3.30.0",
    "platformio/tool/tool-ninja@^1.7.0",
    "platformio/tool/tool-mconf@~1.4060000.0",
    "platformio/tool/tool-idf@~1.0.1",
    "platformio/tool/tool-esp-rom-elfs@0.0.1+20241011",
]


# --------------------------------------------------------------------------------------
# registry API
# --------------------------------------------------------------------------------------
def api_package(owner: str, pkg_type: str, name: str) -> dict:
    """取某个包的全部版本信息。"""
    resp = requests.get(
        f"{API_BASE}/{owner}/{pkg_type}/{name}", headers=UA, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def pick_satisfying_version(data: dict, constraint: str | None) -> dict:
    """从 versions 列表里挑出满足约束的最新版本（没有约束就取 latest）。"""
    versions = data.get("versions") or []
    if not versions:
        raise RuntimeError(f"registry 里没有 {data['owner']['username']}/{data['name']} 的版本")

    if not constraint:
        for item in versions:
            if item.get("is_latest"):
                return item
        return versions[0]

    if constraint.startswith(("~", "^")) and SimpleSpec is not None:
        spec = SimpleSpec(constraint)
        candidates = []
        for item in versions:
            try:
                ver = Version(item["name"].replace("+", "-"))
            except ValueError:
                continue
            if spec.match(ver):
                candidates.append((ver, item))
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
        raise RuntimeError(f"没有满足 {constraint} 的版本")

    for item in versions:  # 精确版本
        if item["name"] == constraint:
            return item
    raise RuntimeError(f"找不到版本 {constraint}")


def pick_compatible_file(version: dict) -> dict:
    """挑当前平台（Windows x64）能用的文件。"""
    systype = "windows_amd64" if sys.platform == "win32" else sys.platform
    files = version.get("files") or []
    for item in files:
        if systype in (item.get("system") or []):
            return item
    for item in files:  # 平台无关的包（framework 等）：system 为 ["*"] 或空
        systems = item.get("system") or []
        if not systems or "*" in systems:
            return item
    for item in files:  # 兜底
        if "amd64" in item["name"]:
            return item
    raise RuntimeError(f"{version['name']} 没有适配 {systype} 的文件")


# --------------------------------------------------------------------------------------
# 镜像解析（复刻 platformio/registry/mirror.py）
# --------------------------------------------------------------------------------------
def enumerate_mirrors(download_url: str) -> list[tuple[str, str, str | None]]:
    """用 bypass 参数逐个问出所有镜像，返回 [(Location, sha256, 镜像名), ...]。"""
    results: list[tuple[str, str, str | None]] = []
    visited: list[str] = []
    for _ in range(8):
        params = {"bypass": ",".join(visited)} if visited else None
        try:
            resp = requests.head(
                download_url,
                params=params,
                allow_redirects=False,
                headers=UA,
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            break
        location = resp.headers.get("Location")
        checksum = resp.headers.get("X-PIO-Content-SHA256")
        mirror = resp.headers.get("X-PIO-Mirror")
        if resp.status_code not in (302, 307) or not location or not checksum:
            break
        results.append((location, checksum, mirror))
        if not mirror or mirror in visited:
            break
        visited.append(mirror)
    if not results:
        raise RuntimeError(f"无法解析镜像地址: {download_url}")
    return results


def seed_http_cache(download_url: str, location: str, checksum: str, mirror: str | None,
                    ttl_hours: int = 1) -> Path:
    """把 HEAD 的结果写进 pio 的 http 缓存。

    这样 pio 的 RegistryFileMirrorIterator 第一次 next() 就直接拿到"我们下载过的那个镜像"，
    不会再去随机抽一个别的镜像（缓存 key = sha1("head" + download_url)，见 cache.py）。
    """
    key = hashlib.sha1(("head" + download_url).encode("utf-8")).hexdigest()
    HTTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = HTTP_CACHE_DIR / key
    cache_path.write_text(
        json.dumps(
            {
                "Location": location,
                "X-PIO-Content-SHA256": checksum,
                "X-PIO-Mirror": mirror,
            }
        ),
        encoding="utf8",
    )

    # 过期索引 db.data：<过期时间戳>=<文件名>
    db_path = HTTP_CACHE_DIR / "db.data"
    lines = []
    if db_path.is_file():
        lines = [
            line.strip()
            for line in db_path.read_text(encoding="utf8").splitlines()
            if line.strip() and not line.strip().endswith(f"={key}")
        ]
    lines.append(f"{int(time.time()) + ttl_hours * 3600}={key}")
    db_path.write_text("\n".join(lines) + "\n", encoding="utf8")
    return cache_path



# --------------------------------------------------------------------------------------
# 多线程下载（这个网络会偶发 SSL/EOF 中断，所以每块都带重试）
# --------------------------------------------------------------------------------------
def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _range_worker(url: str, fp, offset: int, length: int, lock, attempts: int = 4) -> int:
    """下载 [offset, offset+length) 这一段，失败就整段重来。"""
    last_exc = None
    for attempt in range(attempts):
        headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
        try:
            written = 0
            with SESSION.get(url, headers=headers, stream=True, timeout=TIMEOUT) as resp:
                resp.raise_for_status()
                if resp.status_code != 206:  # 服务器不支持 Range
                    raise RuntimeError("server ignored Range header")
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    with lock:
                        fp.seek(offset + written)
                        fp.write(chunk)
                    written += len(chunk)
            if written != length:
                raise RuntimeError(f"短读 {written}/{length}")
            return written
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(1 + attempt * 2)
    raise RuntimeError(f"分块重试 {attempts} 次仍失败: {last_exc}")


def _download_once(url: str, dest: Path, connections: int) -> None:
    head = SESSION.head(url, timeout=TIMEOUT, allow_redirects=True)
    head.raise_for_status()
    size = int(head.headers.get("Content-Length") or 0)
    if size <= 0:
        raise RuntimeError(f"拿不到 Content-Length: {url}")

    part = max(size // max(connections, 1), 1024 * 1024)
    ranges = [(off, min(part, size - off)) for off in range(0, size, part)]

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fp:
        fp.truncate(size)
        if len(ranges) > 1:
            import threading

            lock = threading.Lock()
            with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
                futures = [
                    pool.submit(_range_worker, url, fp, off, length, lock)
                    for off, length in ranges
                ]
                for future in futures:
                    future.result()
            return
        # 服务器不支持 Range 或只给 1 个连接：整文件顺序下载（也带重试）
        headers = {}
        for attempt in range(4):
            try:
                written = 0
                with SESSION.get(url, headers=headers, stream=True, timeout=TIMEOUT) as resp:
                    resp.raise_for_status()
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        fp.seek(written)
                        fp.write(chunk)
                        written += len(chunk)
                if written != size:
                    raise RuntimeError(f"短读 {written}/{size}")
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    raise
                print(f"    ! 单连接第 {attempt + 1} 次失败({exc})，重试")
                time.sleep(2 + attempt * 2)


def download_multi(url: str, dest: Path, connections: int = 8) -> None:
    """分块多线程下载；并发逐步降级，最多试 3 轮。"""
    plan = [connections, max(connections // 2, 2), 1]
    last_exc = None
    for round_no, conns in enumerate(plan, start=1):
        try:
            _download_once(url, dest, conns)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"    ! 第 {round_no} 轮（{conns} 连接）失败: {exc}")
    raise RuntimeError(f"下载失败: {last_exc}")


def download_multi(url: str, dest: Path, connections: int = 8) -> None:
    """分块多线程下载，任何一块失败就退回单连接整文件重下。"""
    head = requests.head(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
    head.raise_for_status()
    size = int(head.headers.get("Content-Length") or 0)
    if size <= 0:
        raise RuntimeError(f"拿不到 Content-Length: {url}")

    part = max(size // connections, 1024 * 1024)
    ranges = [(off, min(part, size - off)) for off in range(0, size, part)]

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fp:
        fp.truncate(size)
        if len(ranges) > 1:
            import threading

            lock = threading.Lock()
            try:
                with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
                    futures = [
                        pool.submit(_range_worker, url, fp, off, length, lock)
                        for off, length in ranges
                    ]
                    for future in futures:
                        future.result()
                return
            except Exception as exc:  # noqa: BLE001
                print(f"    ! 多线程失败({exc})，退回单连接重试")
                fp.seek(0)
                fp.truncate(size)
        with requests.get(url, headers=UA, stream=True, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            pos = 0
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fp.seek(pos)
                fp.write(chunk)
                pos += len(chunk)


# --------------------------------------------------------------------------------------
# 灌缓存
# --------------------------------------------------------------------------------------
def resolve_spec(spec: str) -> tuple[str, str, str, str | None]:
    """'owner/type/name@version' -> (owner, type, name, version)"""
    if "@" in spec:
        left, version = spec.rsplit("@", 1)
    else:
        left, version = spec, None
    parts = left.strip("/").split("/")
    if len(parts) != 3:
        raise SystemExit(f"spec 格式应为 owner/type/name[@version]: {spec}")
    return parts[0], parts[1], parts[2], version


def seed_one(
    download_url: str,
    label: str,
    dry_run: bool,
    connections: int,
    http_cache: bool = True,
) -> int:
    mirrors = enumerate_mirrors(download_url)
    location, checksum, mirror_name = mirrors[0]
    hosts = [loc.split("/")[2] for loc, _, _ in mirrors]

    size = 0
    try:
        size = int(
            requests.head(location, headers=UA, timeout=TIMEOUT).headers.get(
                "Content-Length"
            )
            or 0
        )
    except requests.RequestException:
        pass

    print(f"\n[{label}]")
    print(f"  sha256 : {checksum}")
    print(f"  镜像   : {len(mirrors)} 个 -> {', '.join(hosts)}")
    print(f"  大小   : {size / 1024 / 1024:.1f} MB" if size else "  大小   : ?")
    if dry_run:
        return size

    keys = [hashlib.sha1((loc + sha).encode("utf-8")).hexdigest() for loc, sha, _ in mirrors]
    key = keys[0]

    # pio 正在下这个包时绝对不要插手：它下完会 os.rename(tmp, dl_path)，
    # Windows 下目标已存在会抛 FileExistsError，反而把安装搞挂。
    busy = [k for k in keys if (DL_CACHE_DIR / f"{k}.lock").exists()]
    if busy:
        print(f"  ! 跳过 : pio 正在下载该包（{busy[0][:12]}.lock 存在），等它下完再来")
        return size

    target = DL_CACHE_DIR / key
    if target.is_file() and _sha256_of(target) == checksum:
        print(f"  已存在 : {key}（校验通过，跳过）")
        source = target
    else:
        tmp = DL_CACHE_DIR / f"seed-{key[:12]}.part"
        print(f"  下载   : {connections} 线程 -> {tmp.name}")
        download_multi(location, tmp, connections)
        got = _sha256_of(tmp)
        if got != checksum:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"sha256 不匹配（拿到 {got}）")
        print("  校验   : sha256 OK")
        tmp.replace(target)
        source = target

    # 每个镜像地址都放一份（硬链接，不额外占空间），保证 pio 随机挑到哪个都命中
    for extra in keys:
        link = DL_CACHE_DIR / extra
        if extra == key or link.exists():
            continue
        try:
            os.link(source, link)
        except OSError:
            import shutil

            shutil.copy2(source, link)
    print(f"  缓存键 : {', '.join(k[:12] for k in keys)}")
    if http_cache:
        seed_http_cache(download_url, location, checksum, mirror_name)
        print(f"  已告知 pio 用镜像: {mirror_name}（http 缓存 1 小时内有效）")
    return size


# --------------------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------------------
PRESETS = {"espidf": PRESET_ESPIDF}


def resolve_to_url(spec: str) -> tuple[str, str]:
    """'owner/type/name@约束' -> (download_url, 'owner/type/name@真实版本')"""
    owner, pkg_type, name, constraint = resolve_spec(spec)
    data = api_package(owner, pkg_type, name)
    version = pick_satisfying_version(data, constraint)
    pkgfile = pick_compatible_file(version)
    return pkgfile["download_url"], f"{owner}/{pkg_type}/{name}@{version['name']}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="多线程把 PlatformIO 包灌进下载缓存（绕开 pio 的单连接慢下载）"
    )
    parser.add_argument("specs", nargs="*", help="owner/type/name[@version]，可多个")
    parser.add_argument("--url", action="append", default=[], help="直接给 download_url")
    parser.add_argument("--preset", action="append", choices=sorted(PRESETS), help="预置清单")
    parser.add_argument("--connections", type=int, default=8, help="并发连接数（默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只看清单/镜像/大小，不下载")
    parser.add_argument("--no-http-cache", action="store_true", help="不要覆盖 pio 的 http 缓存")
    args = parser.parse_args()

    if not (args.specs or args.url or args.preset):
        parser.print_help()
        return 1

    targets: list[tuple[str, str]] = []
    for spec in list(args.specs) + [
        spec for preset in (args.preset or []) for spec in PRESETS[preset]
    ]:
        targets.append(resolve_to_url(spec))
    for url in args.url:
        targets.append((url, Path(urlparse(url).path).name))

    total = 0
    for url, label in targets:
        size = seed_one(
            url,
            label,
            args.dry_run,
            args.connections,
            http_cache=not args.no_http_cache,
        )
        total += size or 0

    suffix = "（dry-run，未下载）" if args.dry_run else "已就绪"
    print(f"\n合计 {total / 1024 / 1024:.1f} MB {suffix}")
    if not args.dry_run:
        print("现在重跑 pio / VS Code 里的 PlatformIO 任务会直接命中缓存，不再走慢下载。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

