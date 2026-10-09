#pragma once

// ============================================================
// 喷淋指令协议解析（纯 const char*，不依赖 Arduino.h）
// ------------------------------------------------------------
// 逻辑逐行对齐已验证的 feedback/spray_executor/*.ino：
//   http_node.ino / spray_node.ino / wifi_gateway.ino / gateway.ino
// 唯一差别：这里不用 Arduino String，改用固定缓冲区 + 长度参数，
// 因此同一份代码可以在板载自检（env:selftest）里被直接验证。
//
// 协议（由 feedback/spray.py 生成）：
//   {"group":"g1","duration":2}   按分组喷 2 秒（group="all" 表示全体）
//   {"duration":0}                立即停止
//   {"node":1,"duration":2}       方案丙网关单播用（可选）
// ============================================================

#include <stddef.h>

namespace cg {

// 数字解析的饱和上限：超过按此返回，避免 .ino 里 int 溢出的未定义行为。
// 真正的喷淋时长另有 MAX_SPRAY_SEC（30 秒）硬钳制。
const int kNumberMax = 1000000;

// 取 "group":"xxx"；找不到字段或值不完整时返回 false 并令 out[0] = '\0'。
// 返回值仅表示"字段与引号配对成功"：值可能是空串（{"group":""}）。
// out 不够长时按 outSize-1 截断，始终以 NUL 结尾。
bool parseGroup(const char* json, char* out, size_t outSize);

// 取 "duration":N；字段缺失或后面不是数字时返回 0；支持负数。
int parseDuration(const char* json);

// 取 "node":N；字段缺失时返回 -1（与 .ino 语义一致，网关据此决定单播/广播）。
int parseNodeId(const char* json);

}  // namespace cg
