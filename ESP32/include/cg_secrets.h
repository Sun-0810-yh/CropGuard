#pragma once

// ============================================================
// WiFi 凭据（现场手机热点）
// ------------------------------------------------------------
// 两种填法：
//   A) 直接改下面的默认值（最省事，但会随代码进 git）
//   B) 复制 cg_secrets.local.h.example 为同目录下的 cg_secrets.local.h
//      并在里面重新定义 WIFI_SSID / WIFI_PASS
//      —— 该文件已在 ESP32/.gitignore 里忽略，不会提交到公开仓库
//
// 本项目仓库是公开的，建议用 B；正式比赛现场用完也方便清掉。
// ============================================================

#if defined(__has_include)
#  if __has_include("cg_secrets.local.h")
#    include "cg_secrets.local.h"
#  endif
#endif

#ifndef WIFI_SSID
#  define WIFI_SSID "vivo X300"
#endif
#ifndef WIFI_PASS
#  define WIFI_PASS "20050706syh"
#endif
