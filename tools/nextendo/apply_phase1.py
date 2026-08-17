#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 anchor, found {count}")
    return text.replace(old, new, 1)


def patch_file(root: Path, rel: str, transform) -> None:
    path = root / rel
    if not path.is_file():
        raise RuntimeError(f"missing expected Eden file: {rel}")
    original = path.read_text(encoding="utf-8")
    updated = transform(original)
    if updated == original:
        raise RuntimeError(f"{rel}: transform produced no change")
    backup = path.with_suffix(path.suffix + ".nextendo-phase1.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    print(f"patched {rel}")


def patch_settings(text: str) -> str:
    anchor = '''    SwitchableSetting<bool> airplane_mode{linkage, false, "airplane_mode", Category::Network};\n\n    // WebService'''
    replacement = '''    SwitchableSetting<bool> airplane_mode{linkage, false, "airplane_mode", Category::Network};\n\n    // Nextendo Network. Disabled by default so stock Eden networking behavior is preserved.\n    Setting<bool> enable_nextendo{linkage, false, "enable_nextendo", Category::Network};\n    Setting<std::string> nextendo_server_ip{linkage, "51.178.29.194", "nextendo_server_ip",\n                                            Category::Network};\n    Setting<std::string> nextendo_nat_ip{linkage, "164.132.111.120", "nextendo_nat_ip",\n                                         Category::Network};\n    Setting<std::string> nextendo_pid{linkage, "", "nextendo_pid", Category::Network};\n\n    // WebService'''
    return replace_once(text, anchor, replacement, "settings network section")


def patch_sfdnsres_h(text: str) -> str:
    text = replace_once(
        text,
        '#pragma once\n\n#include "core/hle/service/service.h"',
        '#pragma once\n\n#include <string>\n\n#include "core/hle/service/service.h"',
        "sfdnsres.h include",
    )
    anchor = '''};\n\n} // namespace Service::Sockets'''
    replacement = '''};\n\n// Records the original hostname for a redirected IP so raw TLS sockets can restore SNI.\nvoid SetLastHostForIp(const std::string& ip, const std::string& host);\nstd::string GetLastHostForIp(const std::string& ip);\n\n} // namespace Service::Sockets'''
    return replace_once(text, anchor, replacement, "sfdnsres.h declarations")


def patch_sfdnsres_cpp(text: str) -> str:
    text = replace_once(
        text,
        '#include <string_view>\n#include <utility>\n#include <vector>',
        '#include <cstdlib>\n#include <mutex>\n#include <string_view>\n#include <unordered_map>\n#include <utility>\n#include <vector>',
        "sfdnsres.cpp std includes",
    )
    text = replace_once(
        text,
        '#include "common/string_util.h"',
        '#include "common/settings.h"\n#include "common/string_util.h"',
        "sfdnsres.cpp settings include",
    )

    ns_anchor = 'namespace Service::Sockets {\n\nSFDNSRES::SFDNSRES'
    helpers = r'''namespace Service::Sockets {

namespace {
std::mutex g_last_host_mutex;
std::unordered_map<std::string, std::string> g_last_host_for_ip;

std::string GetConfiguredNextendoIp(const std::string& setting, const char* env_var) {
    if (!setting.empty()) {
        return setting;
    }
    if (const char* env = std::getenv(env_var); env && *env) {
        return env;
    }
    return "127.0.0.1";
}

std::optional<std::string> GetNextendoRedirectIp(const std::string& host) {
    if (!Settings::values.enable_nextendo.GetValue()) {
        return std::nullopt;
    }

    const std::string server_ip = GetConfiguredNextendoIp(
        Settings::values.nextendo_server_ip.GetValue(), "NEXTENDO_SERVER_IP");
    const std::string nat_ip = GetConfiguredNextendoIp(
        Settings::values.nextendo_nat_ip.GetValue(), "NEXTENDO_NAT_IP");

    if (host.starts_with("nncs2-") && host.ends_with(".n.n.srv.nintendo.net")) {
        LOG_INFO(Service, "[Nextendo] Redirecting NAT check host '{}' -> '{}'", host, nat_ip);
        return nat_ip;
    }

    if (host == "nintendo.net" || host.ends_with(".nintendo.net") ||
        host == "nintendo.com" || host.ends_with(".nintendo.com") ||
        host == "nintendowifi.net" || host.ends_with(".nintendowifi.net") ||
        host == "nintendo.co.jp" || host.ends_with(".nintendo.co.jp")) {
        LOG_INFO(Service, "[Nextendo] Redirecting Nintendo host '{}' -> '{}'", host, server_ip);
        return server_ip;
    }

    return std::nullopt;
}
} // namespace

void SetLastHostForIp(const std::string& ip, const std::string& host) {
    std::lock_guard lock{g_last_host_mutex};
    g_last_host_for_ip[ip] = host;
}

std::string GetLastHostForIp(const std::string& ip) {
    std::lock_guard lock{g_last_host_mutex};
    const auto it = g_last_host_for_ip.find(ip);
    return it == g_last_host_for_ip.end() ? std::string{} : it->second;
}

SFDNSRES::SFDNSRES'''
    text = replace_once(text, ns_anchor, helpers, "sfdnsres.cpp helper insertion")

    first_old = r'''    const auto host_buffer = ctx.ReadBuffer(0);
    const std::string host = Common::StringFromBuffer(host_buffer);
    // For now, ignore options, which are in input buffer 1 for GetHostByNameRequestWithOptions.

    // Prevent resolution of Nintendo servers
    if (IsBlockedHost(host)) {
        LOG_WARNING(Network, "Resolution of hostname {} requested, returning EAI_AGAIN", host);
        return {0, GetAddrInfoError::AGAIN};
    }

    auto res_v = Network::GetAddressInfo(host, /*service*/ std::nullopt);
    if (auto* res = std::get_if<std::vector<Network::AddrInfo>>(&res_v)) {
        const std::vector<u8> data = SerializeAddrInfoAsHostEnt(*res, host);
        const u32 data_size = u32(data.size());
        ctx.WriteBuffer(data, 0);
        return {data_size, GetAddrInfoError::SUCCESS};
    }'''
    first_new = r'''    const auto host_buffer = ctx.ReadBuffer(0);
    std::string host = Common::StringFromBuffer(host_buffer);
    // For now, ignore options, which are in input buffer 1 for GetHostByNameRequestWithOptions.

    if (parameters.use_nsd_resolve || host.find('%') != std::string::npos) {
        if (const auto pos = host.find('%'); pos != std::string::npos) {
            host.replace(pos, 1, "lp1");
        }
        if (host == "api.accounts.nintendo.com" || host == "accounts.nintendo.com") {
            host = "e0d67c509fb203858ebcb2fe3f88c2aa.baas.nintendo.com";
        }
    }

    const auto redirect = GetNextendoRedirectIp(host);
    if (!redirect.has_value() && IsBlockedHost(host)) {
        LOG_WARNING(Network, "Resolution of hostname {} requested, returning EAI_AGAIN", host);
        return {0, GetAddrInfoError::AGAIN};
    }
    const std::string query_host = redirect.value_or(host);

    auto res_v = Network::GetAddressInfo(query_host, /*service*/ std::nullopt);
    if (auto* res = std::get_if<std::vector<Network::AddrInfo>>(&res_v)) {
        if (redirect.has_value()) {
            for (const auto& addrinfo : *res) {
                SetLastHostForIp(Network::IPv4AddressToString(addrinfo.addr.ip), host);
            }
        }
        const std::vector<u8> data = SerializeAddrInfoAsHostEnt(*res, host);
        const u32 data_size = u32(data.size());
        ctx.WriteBuffer(data, 0);
        return {data_size, GetAddrInfoError::SUCCESS};
    }'''
    text = replace_once(text, first_old, first_new, "GetHostByName Nextendo redirect")

    second_old = r'''    const auto host_buffer = ctx.ReadBuffer(0);
    const std::string host = Common::StringFromBuffer(host_buffer);

    // Prevent resolution of Nintendo servers
    if (IsBlockedHost(host)) {
        LOG_WARNING(Network, "Resolution of hostname {} requested, returning EAI_AGAIN", host);
        return {0, GetAddrInfoError::AGAIN};
    }

    std::optional<std::string> service = std::nullopt;
    if (ctx.CanReadBuffer(1)) {
        const std::span<const u8> service_buffer = ctx.ReadBuffer(1);
        service = Common::StringFromBuffer(service_buffer);
    }

    // Serialized hints are also passed in a buffer, but are ignored for now.

    auto res_v = Network::GetAddressInfo(host, service);
    if (auto* res = std::get_if<std::vector<Network::AddrInfo>>(&res_v)) {
        const std::vector<u8> data = SerializeAddrInfo(*res, host);
        const u32 data_size = u32(data.size());
        ctx.WriteBuffer(data, 0);
        return {data_size, GetAddrInfoError::SUCCESS};
    }'''
    second_new = r'''    const auto host_buffer = ctx.ReadBuffer(0);
    std::string host = Common::StringFromBuffer(host_buffer);

    if (parameters.use_nsd_resolve || host.find('%') != std::string::npos) {
        if (const auto pos = host.find('%'); pos != std::string::npos) {
            host.replace(pos, 1, "lp1");
        }
        if (host == "api.accounts.nintendo.com" || host == "accounts.nintendo.com") {
            host = "e0d67c509fb203858ebcb2fe3f88c2aa.baas.nintendo.com";
        }
    }

    const auto redirect = GetNextendoRedirectIp(host);
    if (!redirect.has_value() && IsBlockedHost(host)) {
        LOG_WARNING(Network, "Resolution of hostname {} requested, returning EAI_AGAIN", host);
        return {0, GetAddrInfoError::AGAIN};
    }
    const std::string query_host = redirect.value_or(host);

    std::optional<std::string> service = std::nullopt;
    if (ctx.CanReadBuffer(1)) {
        const std::span<const u8> service_buffer = ctx.ReadBuffer(1);
        service = Common::StringFromBuffer(service_buffer);
    }

    // Serialized hints are also passed in a buffer, but are ignored for now.

    auto res_v = Network::GetAddressInfo(query_host, service);
    if (auto* res = std::get_if<std::vector<Network::AddrInfo>>(&res_v)) {
        if (redirect.has_value()) {
            for (const auto& addrinfo : *res) {
                SetLastHostForIp(Network::IPv4AddressToString(addrinfo.addr.ip), host);
            }
        }
        const std::vector<u8> data = SerializeAddrInfo(*res, host);
        const u32 data_size = u32(data.size());
        ctx.WriteBuffer(data, 0);
        return {data_size, GetAddrInfoError::SUCCESS};
    }'''
    return replace_once(text, second_old, second_new, "GetAddrInfo Nextendo redirect")


def patch_bsd_h(text: str) -> str:
    anchor = '''        s32 flags = 0;\n        bool is_connection_based = false;\n    };'''
    replacement = '''        s32 flags = 0;\n        bool is_connection_based = false;\n        bool nextendo_sni_injected = false;\n    };'''
    return replace_once(text, anchor, replacement, "bsd.h FileDescriptor")


def patch_bsd_cpp(text: str) -> str:
    text = replace_once(
        text,
        '#include <array>\n#include <memory>',
        '#include <array>\n#include <cstring>\n#include <memory>',
        "bsd.cpp cstring include",
    )
    text = replace_once(
        text,
        '#include "core/hle/service/sockets/bsd.h"\n#include "core/hle/service/sockets/sockets_translate.h"',
        '#include "core/hle/service/sockets/bsd.h"\n#include "core/hle/service/sockets/sfdnsres.h"\n#include "core/hle/service/sockets/sockets_translate.h"',
        "bsd.cpp sfdnsres include",
    )

    helper_anchor = r'''bool IsConnectionBased(Type type) {
    switch (type) {
    case Type::STREAM:
        return true;
    case Type::DGRAM:
        return false;
    default:
        UNIMPLEMENTED_MSG("Unimplemented type={}", type);
        return false;
    }
}
'''
    helper_replacement = helper_anchor + r'''
// Some Nintendo clients emit a TLS ClientHello without SNI on their raw BSD socket path.
// Once DNS is redirected to a shared Nextendo endpoint, the original host is required for the
// reverse proxy to select the right virtual host. This inserts server_name only when absent.
bool TryInjectNextendoTlsSni(std::span<const u8> input, const std::string& host_name,
                             std::vector<u8>& output) {
    if (input.size() < 43 || input[0] != 0x16 || input[5] != 0x01 || host_name.empty()) {
        return false;
    }
    const size_t record_len = (static_cast<size_t>(input[3]) << 8) | input[4];
    if (5 + record_len > input.size()) {
        return false;
    }

    size_t p = 5 + 4 + 2 + 32;
    if (p >= input.size()) return false;
    const size_t sid_len = input[p];
    p += 1 + sid_len;
    if (p + 2 > input.size()) return false;
    const size_t cs_len = (static_cast<size_t>(input[p]) << 8) | input[p + 1];
    p += 2 + cs_len;
    if (p + 1 > input.size()) return false;
    const size_t cm_len = input[p];
    p += 1 + cm_len;
    if (p + 2 > input.size()) return false;

    const size_t ext_total_len = (static_cast<size_t>(input[p]) << 8) | input[p + 1];
    const size_t ext_len_pos = p;
    const size_t ext_start = p + 2;
    const size_t ext_end = ext_start + ext_total_len;
    if (ext_end > input.size()) return false;

    size_t q = ext_start;
    while (q + 4 <= ext_end) {
        const u16 type = static_cast<u16>((input[q] << 8) | input[q + 1]);
        const u16 len = static_cast<u16>((input[q + 2] << 8) | input[q + 3]);
        if (q + 4 + len > ext_end) return false;
        if (type == 0x0000) return false;
        q += 4 + len;
    }

    const size_t name_len = host_name.size();
    if (name_len > 0xFFFF) return false;
    const size_t list_len = 1 + 2 + name_len;
    const size_t ext_data_len = 2 + list_len;
    const size_t sni_ext_len = 4 + ext_data_len;

    std::vector<u8> sni(sni_ext_len);
    size_t i = 0;
    sni[i++] = 0x00; sni[i++] = 0x00;
    sni[i++] = static_cast<u8>(ext_data_len >> 8); sni[i++] = static_cast<u8>(ext_data_len);
    sni[i++] = static_cast<u8>(list_len >> 8); sni[i++] = static_cast<u8>(list_len);
    sni[i++] = 0x00;
    sni[i++] = static_cast<u8>(name_len >> 8); sni[i++] = static_cast<u8>(name_len);
    std::memcpy(sni.data() + i, host_name.data(), name_len);

    output.resize(input.size() + sni_ext_len);
    std::memcpy(output.data(), input.data(), ext_start);
    std::memcpy(output.data() + ext_start, sni.data(), sni_ext_len);
    std::memcpy(output.data() + ext_start + sni_ext_len, input.data() + ext_start,
                input.size() - ext_start);

    const size_t new_ext_len = ext_total_len + sni_ext_len;
    output[ext_len_pos] = static_cast<u8>(new_ext_len >> 8);
    output[ext_len_pos + 1] = static_cast<u8>(new_ext_len);

    const size_t hs_len = ((static_cast<size_t>(input[6]) << 16) |
                           (static_cast<size_t>(input[7]) << 8) | input[8]) + sni_ext_len;
    output[6] = static_cast<u8>(hs_len >> 16);
    output[7] = static_cast<u8>(hs_len >> 8);
    output[8] = static_cast<u8>(hs_len);

    const size_t new_record_len = record_len + sni_ext_len;
    output[3] = static_cast<u8>(new_record_len >> 8);
    output[4] = static_cast<u8>(new_record_len);
    return true;
}
'''
    text = replace_once(text, helper_anchor, helper_replacement, "bsd.cpp SNI helper")

    send_old = r'''std::pair<s32, Errno> BSD::SendImpl(s32 fd, u32 flags, std::span<const u8> message) {
    if (!IsFileDescriptorValid(fd)) {
        return {-1, Errno::BADF};
    }
    if (!file_descriptors[fd]->socket) {
        LOG_WARNING(Service, "Uninitialized socket");
        return {-1, Errno::BADF};
    }
    return Translate(file_descriptors[fd]->socket->Send(message, flags));
}'''
    send_new = r'''std::pair<s32, Errno> BSD::SendImpl(s32 fd, u32 flags, std::span<const u8> message) {
    if (!IsFileDescriptorValid(fd)) {
        return {-1, Errno::BADF};
    }
    if (!file_descriptors[fd]->socket) {
        LOG_WARNING(Service, "Uninitialized socket");
        return {-1, Errno::BADF};
    }

    auto& descriptor = *file_descriptors[fd];
    std::span<const u8> send_buffer = message;
    std::vector<u8> injected_buffer;

    if (Settings::values.enable_nextendo.GetValue() && !descriptor.nextendo_sni_injected &&
        message.size() > 5 && message[0] == 0x16 && message[5] == 0x01) {
        const auto [peer_addr, peer_error] = descriptor.socket->GetPeerName();
        if (peer_error == Network::Errno::SUCCESS) {
            const std::string ip = Network::IPv4AddressToString(peer_addr.ip);
            const std::string host = GetLastHostForIp(ip);
            if (!host.empty()) {
                descriptor.nextendo_sni_injected = true;
                if (TryInjectNextendoTlsSni(message, host, injected_buffer)) {
                    LOG_INFO(Service, "[Nextendo] Injected TLS SNI '{}' for fd={}", host, fd);
                    send_buffer = injected_buffer;
                }
            }
        }
    }

    return Translate(descriptor.socket->Send(send_buffer, flags));
}'''
    return replace_once(text, send_old, send_new, "bsd.cpp SendImpl")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Eden-Nextendo phase 1 networking port")
    parser.add_argument("eden_root", type=Path, help="Path to an Eden source checkout")
    args = parser.parse_args()
    root = args.eden_root.resolve()

    required = [
        "src/common/settings.h",
        "src/core/hle/service/sockets/sfdnsres.h",
        "src/core/hle/service/sockets/sfdnsres.cpp",
        "src/core/hle/service/sockets/bsd.h",
        "src/core/hle/service/sockets/bsd.cpp",
    ]
    for rel in required:
        if not (root / rel).is_file():
            print(f"error: {root} does not look like the expected Eden checkout; missing {rel}", file=sys.stderr)
            return 2

    try:
        patch_file(root, "src/common/settings.h", patch_settings)
        patch_file(root, "src/core/hle/service/sockets/sfdnsres.h", patch_sfdnsres_h)
        patch_file(root, "src/core/hle/service/sockets/sfdnsres.cpp", patch_sfdnsres_cpp)
        patch_file(root, "src/core/hle/service/sockets/bsd.h", patch_bsd_h)
        patch_file(root, "src/core/hle/service/sockets/bsd.cpp", patch_bsd_cpp)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Eden-Nextendo phase 1 applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
