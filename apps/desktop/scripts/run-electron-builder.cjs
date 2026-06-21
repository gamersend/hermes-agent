"use strict"

// Resolve electronDist at runtime (#38673, #47917): electron-builder 26.8.x can
// re-unpack a broken Electron.app; reusing the installed dist dodges that.
// npm workspace hoisting is non-deterministic — require.resolve finds electron
// wherever it landed. Dist present → -c.electronDist=<abs>/dist; absent → let
// electron-builder fetch via @electron/get (electronVersion + ELECTRON_MIRROR).

const fs = require("node:fs")
const path = require("node:path")
const { spawnSync } = require("node:child_process")

function electronDistDir() {
  try {
    return path.join(path.dirname(require.resolve("electron/package.json")), "dist")
  } catch {
    return null
  }
}

function distBinary(dist) {
  if (process.platform === "darwin") {
    return path.join(dist, "Electron.app", "Contents", "MacOS", "Electron")
  }
  if (process.platform === "win32") {
    return path.join(dist, "electron.exe")
  }
  return path.join(dist, "electron")
}

function electronBuilderCli() {
  const pkgJson = require.resolve("electron-builder/package.json")
  const bin = require(pkgJson).bin
  const rel = typeof bin === "string" ? bin : bin["electron-builder"]
  return path.join(path.dirname(pkgJson), rel)
}

function isDirBuild(argv) {
  return argv.includes("--dir")
}

function hasExplicitSigningEnv(env) {
  return Boolean(
    env.CSC_NAME ||
      env.CSC_LINK ||
      env.CSC_KEY_PASSWORD ||
      env.CSC_IDENTITY_AUTO_DISCOVERY ||
      env.APPLE_API_KEY ||
      env.APPLE_NOTARY_PROFILE
  )
}

const dist = electronDistDir()
const args = []
if (dist && fs.existsSync(distBinary(dist))) {
  args.push(`-c.electronDist=${dist}`)
} else {
  console.warn(
    "[run-electron-builder] no local electron dist; electron-builder will fetch " +
      "via @electron/get (electronVersion + ELECTRON_MIRROR)."
  )
}
args.push(...process.argv.slice(2))

const env = { ...process.env }
if (process.platform === "darwin" && isDirBuild(args) && !hasExplicitSigningEnv(env)) {
  // `npm run pack` / `--dir` is the local desktop build used by `hermes desktop`.
  // It does not need distribution signing. If the login keychain contains a
  // usable-looking Developer ID cert but codesign cannot access the private key
  // from a non-interactive shell, electron-builder auto-discovery turns a local
  // build into an avoidable errSecInternalComponent failure. Leave dist/DMG/ZIP
  // builds alone, and let explicit CSC_* / APPLE_* env vars opt back into signing.
  env.CSC_IDENTITY_AUTO_DISCOVERY = "false"
  console.warn("[run-electron-builder] disabled macOS signing auto-discovery for --dir build")
}

const result = spawnSync(process.execPath, [electronBuilderCli(), ...args], {
  stdio: "inherit",
  env,
})
if (result.error) {
  console.error(`[run-electron-builder] spawn failed: ${result.error.message}`)
  process.exit(1)
}
process.exit(result.status == null ? 1 : result.status)
