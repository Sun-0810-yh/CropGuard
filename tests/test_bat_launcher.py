# -*- coding: utf-8 -*-
"""双击启动脚本的回归：Windows 批处理必须是 CRLF 换行（第一套演示踩过的坑）。

2026-10-23 现场现象：双击 `启动演示系统.bat` 窗口闪一下就退出，浏览器不会自动打开，
但服务器其实起得来（手工开 /demo 一切正常）。

根因：这个 .bat 被写成了 **UTF-8 无 BOM + 纯 LF**。cmd 解析 LF 换行的中文批处理会错位——
`echo` / `REM` 前缀被吃掉，注释和提示的残句被当成命令执行
（`'xxx' 不是内部或外部命令`），整个流程根本走不到 `start "" http://.../demo` 那一行。
对照实验：同样的内容存成 CRLF 一次就对了。

所以这里锁三条：
* 仓库根目录所有 .bat 必须是 CRLF（编辑器默认存 LF 就会复发）；
* 必须是合法 UTF-8 且不带 BOM（cmd 会把 BOM 当命令的一部分报错）；
* 启动脚本必须真的去开 /demo，并且留一条"浏览器没弹出来"的手动兜底提示。
"""
from pathlib import Path

from _registry import test_case

ROOT = Path(__file__).resolve().parent.parent
BATS = sorted(ROOT.glob('*.bat'))


@test_case
def test_bat_launchers_exist():
    names = {p.name for p in BATS}
    for must in ('启动演示系统.bat', '停止演示系统.bat', '启动评分GUI.bat'):
        assert must in names, f'缺少 {must}（现场双击入口）'


@test_case
def test_bat_files_use_crlf():
    """裸 LF 换行 = cmd 解析错位，启动流程静默中断。"""
    assert BATS, '仓库根目录没有 .bat'
    for p in BATS:
        raw = p.read_bytes()
        stripped = raw.replace(b'\r\n', b'')
        assert b'\n' not in stripped, (
            f'{p.name} 有裸 LF 换行：cmd 解析中文批处理会错位，'
            f'启动流程会中途断掉（存成 CRLF 即可）')
        assert b'\r' not in stripped, f'{p.name} 有裸 CR'


@test_case
def test_bat_files_are_utf8_without_bom():
    for p in BATS:
        raw = p.read_bytes()
        assert not raw.startswith(b'\xef\xbb\xbf'), (
            f'{p.name} 带 UTF-8 BOM：cmd 会把 BOM 当成第一行命令的一部分')
        raw.decode('utf-8')          # 解不开就说明编码混了，直接失败


@test_case
def test_launcher_opens_demo_page():
    """一键启动的终点必须是 /demo，且失败时能让人手动打开。"""
    src = (ROOT / '启动演示系统.bat').read_text(encoding='utf-8')
    assert 'http://127.0.0.1:8080/demo' in src, '启动脚本没有打开演示指挥台 /demo'
    assert 'start "" "http://127.0.0.1:8080/demo"' in src, '必须用 start 交给默认浏览器打开'
    assert '如果浏览器没有自动弹出来' in src, '缺少"浏览器没弹出就手动打开"的兜底提示'


@test_case
def test_launchers_use_only_the_gpu_env():
    """推理环境直接替换成 GPU 版，不提供备选：不能再退回 py -3.14 / CPU。"""
    for name in ('启动演示系统.bat', '启动评分GUI.bat', '停止演示系统.bat'):
        src = (ROOT / name).read_text(encoding='utf-8')
        assert r'torch\venv\Scripts\python.exe' in src, f'{name} 没有用 GPU 环境'
        assert 'py -3.14' not in src, f'{name} 不应再退回 py -3.14'
        assert 'CG_DEVICE=cpu' not in src, f'{name} 不应再切 CPU'


@test_case
def test_gitattributes_pins_bat_to_crlf():
    """现场机器多为拷过去的目录，不受 core.autocrlf 影响，靠 .gitattributes 兜底。"""
    ga = ROOT / '.gitattributes'
    assert ga.is_file(), '缺少 .gitattributes：*.bat 的 CRLF 约束会随环境漂移'
    text = ga.read_text(encoding='utf-8')
    assert '*.bat' in text and 'eol=crlf' in text, '.gitattributes 没锁定 *.bat 的换行'
