const fs = require('node:fs')
const path = require('node:path')

if (process.platform !== 'darwin') {
  process.exit(0)
}

const desktopRoot = path.resolve(__dirname, '..')
const repoRoot = path.resolve(desktopRoot, '..', '..')
const electronMacPath = path.join(repoRoot, 'node_modules', 'app-builder-lib', 'out', 'electron', 'electronMac.js')
const macPackagerPath = path.join(repoRoot, 'node_modules', 'app-builder-lib', 'out', 'macPackager.js')

function patchFile({ filePath, marker, needle, replacement, appliedMessage, alreadyMessage, missingMessage }) {
  if (!fs.existsSync(filePath)) {
    console.warn(`[patch-electron-builder] skipped: ${filePath} not found`)
    return false
  }

  const source = fs.readFileSync(filePath, 'utf8')
  if (source.includes(marker)) {
    console.log(alreadyMessage)
    return false
  }

  if (!source.includes(needle)) {
    console.warn(missingMessage)
    return false
  }

  fs.writeFileSync(filePath, source.replace(needle, replacement))
  console.log(appliedMessage)
  return true
}

const electronBinaryMarker = 'hermes-macos-electron-binary-fallback'
const electronBinaryNeedle = `    await Promise.all([
        doRename(path.join(contentsPath, "MacOS"), electronBranding.productName, appPlist.CFBundleExecutable),
        (0, builder_util_1.unlinkIfExists)(path.join(appOutDir, "LICENSE")),
        (0, builder_util_1.unlinkIfExists)(path.join(appOutDir, "LICENSES.chromium.html")),
    ]);`
const electronBinaryReplacement = `    // ${electronBinaryMarker}: electron-builder 26.8.x can sometimes copy
    // Electron.app without its main MacOS/Electron binary before this rename.
    // Restore it from the installed Electron runtime so local desktop installs
    // do not fail with ENOENT during macOS arm64 packaging.
    const macosDir = path.join(contentsPath, "MacOS");
    const bundledElectronBinary = path.join(macosDir, electronBranding.productName);
    if (!fs.existsSync(bundledElectronBinary)) {
        const candidates = [
            path.join(packager.info.framework.distMacOsAppName, "Contents", "MacOS", electronBranding.productName),
            // npm may nest the workspace-only electron devDep under
            // apps/desktop/node_modules (process.cwd() during pack), or hoist
            // it to the repo root. Try the workspace-local install first, then
            // the root hoist, so the fallback works under either layout.
            path.join(process.cwd(), "node_modules", "electron", "dist", "Electron.app", "Contents", "MacOS", electronBranding.productName),
            path.join(process.cwd(), "..", "..", "node_modules", "electron", "dist", "Electron.app", "Contents", "MacOS", electronBranding.productName),
        ];
        const sourceBinary = candidates.find(candidate => fs.existsSync(candidate));
        if (sourceBinary == null) {
            throw new Error("Electron binary missing from packaged app and Electron runtime: " + bundledElectronBinary);
        }
        await (0, promises_1.copyFile)(sourceBinary, bundledElectronBinary);
        await (0, promises_1.chmod)(bundledElectronBinary, 0o755);
    }
    await Promise.all([
        doRename(macosDir, electronBranding.productName, appPlist.CFBundleExecutable),
        (0, builder_util_1.unlinkIfExists)(path.join(appOutDir, "LICENSE")),
        (0, builder_util_1.unlinkIfExists)(path.join(appOutDir, "LICENSES.chromium.html")),
    ]);`

patchFile({
  filePath: electronMacPath,
  marker: electronBinaryMarker,
  needle: electronBinaryNeedle,
  replacement: electronBinaryReplacement,
  appliedMessage: '[patch-electron-builder] applied macOS Electron binary fallback',
  alreadyMessage: '[patch-electron-builder] macOS Electron binary fallback already applied',
  missingMessage: '[patch-electron-builder] skipped: expected electronMac.js shape not found',
})

const signingHashMarker = 'hermes-macos-code-sign-identity-hash'
const signingHashNeedle = '        return customSign ? Promise.resolve(customSign(opts, this)) : (0, macCodeSign_1.sign)({ ...opts, identity: identity ? identity.name : undefined });'
const signingHashReplacement = `        // ${signingHashMarker}: electron-builder logs the resolved identity hash but
        // used to pass only the certificate common name to codesign. If the login
        // keychain contains two valid Developer ID Application certificates with the
        // same common name, codesign rejects the name as ambiguous. The SHA-1 hash is
        // the stable identifier codesign accepts for both single and duplicate names.
        return customSign ? Promise.resolve(customSign(opts, this)) : (0, macCodeSign_1.sign)({ ...opts, identity: identity ? identity.hash : undefined });`

patchFile({
  filePath: macPackagerPath,
  marker: signingHashMarker,
  needle: signingHashNeedle,
  replacement: signingHashReplacement,
  appliedMessage: '[patch-electron-builder] applied macOS codesign identity hash fallback',
  alreadyMessage: '[patch-electron-builder] macOS codesign identity hash fallback already applied',
  missingMessage: '[patch-electron-builder] skipped: expected macPackager.js signing shape not found',
})
