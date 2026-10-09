#include "cg_protocol.h"

#include <ctype.h>
#include <string.h>

namespace cg {
namespace {

// 定位形如 "field"（含两侧引号）的字段，返回起始引号的下标；找不到返回 -1。
// 语义等价于 .ino 里的 json.indexOf("\"field\"")。
int findField(const char* json, const char* field) {
    if (json == nullptr || field == nullptr) return -1;
    const size_t n = strlen(field);
    for (const char* p = json; (p = strchr(p, '"')) != nullptr; ++p) {
        if (strncmp(p + 1, field, n) == 0 && p[1 + n] == '"') {
            return (int)(p - json);
        }
    }
    return -1;
}

// 跳到字段值里的数字：跳过空格，并沿用 .ino 的规则一并跳过多余的 ':'。
// neg 输出该数是否为负。
const char* skipToNumber(const char* p, bool* neg) {
    while (*p == ' ' || *p == ':') ++p;
    *neg = false;
    if (*p == '-') {
        *neg = true;
        ++p;
    }
    return p;
}

// 读连续数字，带上限饱和（.ino 是裸 int 累加，长数字会溢出为未定义行为）。
int readNumber(const char* p) {
    int val = 0;
    while (isdigit((unsigned char)*p)) {
        if (val < kNumberMax) {
            val = val * 10 + (*p - '0');
            if (val > kNumberMax) val = kNumberMax;
        }
        ++p;
    }
    return val;
}

}  // namespace

bool parseGroup(const char* json, char* out, size_t outSize) {
    if (out == nullptr || outSize == 0) return false;
    out[0] = '\0';

    const int pos = findField(json, "group");
    if (pos < 0) return false;

    const char* colon = strchr(json + pos, ':');
    if (colon == nullptr) return false;

    const char* q1 = strchr(colon + 1, '"');
    if (q1 == nullptr) return false;

    const char* q2 = strchr(q1 + 1, '"');
    if (q2 == nullptr) return false;   // 截断串：视为无有效分组

    size_t len = (size_t)(q2 - (q1 + 1));
    if (len > outSize - 1) len = outSize - 1;
    memcpy(out, q1 + 1, len);
    out[len] = '\0';
    return true;
}

int parseDuration(const char* json) {
    const int pos = findField(json, "duration");
    if (pos < 0) return 0;

    const char* colon = strchr(json + pos, ':');
    if (colon == nullptr) return 0;

    bool neg = false;
    const int val = readNumber(skipToNumber(colon + 1, &neg));
    return neg ? -val : val;
}

int parseNodeId(const char* json) {
    const int pos = findField(json, "node");
    if (pos < 0) return -1;

    const char* colon = strchr(json + pos, ':');
    if (colon == nullptr) return -1;

    bool neg = false;
    const int val = readNumber(skipToNumber(colon + 1, &neg));
    return neg ? -val : val;
}

}  // namespace cg
