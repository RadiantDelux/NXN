#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 anchor, found {count}")
    return text.replace(old, new, 1)


def replace_exact(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{label}: expected exactly {expected} anchors, found {count}")
    return text.replace(old, new)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    path = root / "src/yuzu_cmd/yuzu.cpp"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "struct SdlState {\n    Core::System system{};\n    std::unique_ptr<EmuWindow_SDL3> emu_window;\n};",
        "struct SdlState {\n    Core::System system{};\n    std::unique_ptr<InputCommon::InputSubsystem> input_subsystem;\n    std::unique_ptr<EmuWindow_SDL3> emu_window;\n    bool system_initialized = false;\n    bool network_initialized = false;\n};",
        "SdlState lifecycle fields",
    )

    text = replace_once(
        text,
        "extern \"C\" SDL_AppResult SDL_AppInit(void **appstate, int argc, char **argv) {\n    SdlState* state = new SdlState();",
        "extern \"C\" SDL_AppResult SDL_AppInit(void **appstate, int argc, char **argv) {\n    SdlState* state = new SdlState();\n    *appstate = state;",
        "SDL appstate assignment",
    )

    text = replace_once(
        text,
        "            case 'h':\n                PrintHelp(argv[0]);\n                return SDL_APP_FAILURE;",
        "            case 'h':\n                PrintHelp(argv[0]);\n                return SDL_APP_SUCCESS;",
        "help clean exit",
    )

    text = replace_once(
        text,
        "            case 'v':\n                PrintVersion();\n                return SDL_APP_FAILURE;",
        "            case 'v':\n                PrintVersion();\n                return SDL_APP_SUCCESS;",
        "version clean exit",
    )

    text = replace_once(
        text,
        "    if (filepath.empty()) {\n        LOG_CRITICAL(Frontend, \"Failed to load ROM: No ROM specified\");\n        return SDL_APP_FAILURE;\n    }\n\n    state->system.Initialize();\n\n    InputCommon::InputSubsystem input_subsystem{};",
        "    if (filepath.empty()) {\n        LOG_CRITICAL(Frontend, \"Failed to load ROM: No ROM specified\");\n        return SDL_APP_FAILURE;\n    }\n\n    if (!Network::Init()) {\n        LOG_CRITICAL(Frontend, \"Failed to initialize network subsystem\");\n        return SDL_APP_FAILURE;\n    }\n    state->network_initialized = true;\n\n    state->system.Initialize();\n    state->system_initialized = true;\n\n    state->input_subsystem = std::make_unique<InputCommon::InputSubsystem>();",
        "network and system initialization",
    )

    text = replace_exact(
        text,
        "&input_subsystem, state->system, fullscreen",
        "state->input_subsystem.get(), state->system, fullscreen",
        3,
        "persistent input subsystem references",
    )

    text = replace_once(
        text,
        "extern \"C\" void SDL_AppQuit(void *appstate, SDL_AppResult result) {\n    SdlState *state = (SdlState *)appstate;\n    state->system.DetachDebugger();\n    void(state->system.Pause());\n    state->system.ShutdownMainProcess();\n    delete state;\n}",
        "extern \"C\" void SDL_AppQuit(void *appstate, SDL_AppResult result) {\n    SdlState *state = (SdlState *)appstate;\n    if (state == nullptr) {\n        return;\n    }\n    if (state->system_initialized) {\n        state->system.DetachDebugger();\n        void(state->system.Pause());\n        state->system.ShutdownMainProcess();\n    }\n    if (state->network_initialized) {\n        Network::Shutdown();\n    }\n    delete state;\n}",
        "safe SDL cleanup",
    )

    path.write_text(text, encoding="utf-8")
    print("patched src/yuzu_cmd/yuzu.cpp (SDL/network/input lifecycle)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
