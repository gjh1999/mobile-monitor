"""Versioned Windows releases authenticated by the paired HTTPS coordinator."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request
from zipfile import ZipFile

from coordination_client import device_client

VERSION = "44"
LIMIT = 160 * 1024**2


def release_fields(data):
    version, sha, size = (data[name] for name in ("version", "sha256", "size"))
    if (not re.fullmatch(r"[1-9][0-9]{0,5}", version)
            or not re.fullmatch(r"[0-9a-f]{64}", sha)
            or type(size) is not int or not 0 < size <= LIMIT):
        raise ValueError("更新清单格式无效。")
    return version, sha, size


def client_for(config):
    client = device_client(config)
    if client is None or not client.url.startswith("https://") or not client.token:
        raise ValueError("请先配对 HTTPS 协调服务。")
    return client


def endpoint(client, suffix=""):
    return client.opener.open(Request(client.url + "/api/v1/releases/windows" + suffix, headers={
        "Authorization": "Bearer " + client.token, "X-Lanxi-Device": client.device,
    }), timeout=20)


def latest(config):
    with endpoint(client_for(config)) as response:
        data = json.loads(response.read(4096))
    return data if int(release_fields(data)[0]) > int(VERSION) else None


def prepare(config, manifest, root):
    version, sha, expected_size = release_fields(manifest)
    destination = root / f"Lanxi-R{version}-Portable"
    executable = f"Lanxi-Glass-R{version}-Portable.exe"
    client = client_for(config)
    root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"更新目录已存在，请从该目录打开新版。\n{destination}")
    with tempfile.TemporaryDirectory(prefix=".lanxi-install-", dir=root) as temp:
        archive = Path(temp) / "package.zip"
        actual_size = 0
        checksum = hashlib.sha256()
        with endpoint(client, "/" + version) as response, archive.open("xb") as stream:
            while chunk := response.read(128 * 1024):
                actual_size += len(chunk)
                if actual_size > expected_size:
                    raise ValueError("更新包超过清单大小。")
                checksum.update(chunk)
                stream.write(chunk)
        if actual_size != expected_size or checksum.hexdigest() != sha:
            raise ValueError("更新包校验失败。")
        stage = Path(temp) / "unpacked"
        stage.mkdir()
        with ZipFile(archive) as package:
            files = package.infolist()
            names = {item.filename for item in files}
            if (len(names) != len(files) or len(files) > 24 or executable not in names
                    or sum(item.file_size for item in files) > 3 * LIMIT):
                raise ValueError("更新包文件清单无效。")
            for item in files:
                name = item.filename
                if not re.fullmatch(r"[\w.-]+", name) or name[0] == "." or name[-1] == "." or Path(name).is_reserved():
                    raise ValueError("更新包中有无效文件名。")
                with package.open(item) as source, (stage / name).open("xb") as target:
                    shutil.copyfileobj(source, target)
        stage.rename(destination)
    return destination / executable


def powershell(script):
    command = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", command]


def register_start_menu_shortcut(executable, versions, programs=None):
    version = re.fullmatch(r"Lanxi-Glass-R([1-9][0-9]{0,5})-Portable\.exe", executable.name)
    if (os.name != "nt" or not version or not executable.is_file()
            or executable.parent != versions / f"Lanxi-R{version[1]}-Portable"):
        return
    script = r"""
$ErrorActionPreference = 'Stop'
$directory = [Environment]::GetFolderPath('Programs')
if ($env:LANXI_SHORTCUT_PROGRAMS) { $directory = $env:LANXI_SHORTCUT_PROGRAMS }
if (!$directory) { throw 'Missing Start menu folder' }
[void][IO.Directory]::CreateDirectory($directory)
$path = Join-Path $directory '澜汐.lnk'
$shell = New-Object -ComObject WScript.Shell
$entry = $shell.CreateShortcut($path)
if ($entry.TargetPath) {
    $name = [IO.Path]::GetFileName($entry.TargetPath)
    if ($name -notmatch '^Lanxi-Glass-R([1-9][0-9]{0,5})-Portable\.exe$') { exit 0 }
    if ([int]$Matches[1] -gt [int]$env:LANXI_SHORTCUT_VERSION -and [IO.File]::Exists($entry.TargetPath)) { exit 0 }
    if ($entry.TargetPath -eq $env:LANXI_SHORTCUT_TARGET) { exit 0 }
}
$temporary = Join-Path $directory ('.lanxi-' + [Guid]::NewGuid().ToString('N') + '.lnk')
$backup = $temporary + '.bak'
try {
    $entry = $shell.CreateShortcut($temporary)
    $entry.TargetPath = $env:LANXI_SHORTCUT_TARGET
    $entry.WorkingDirectory = [IO.Path]::GetDirectoryName($entry.TargetPath)
    $entry.Description = '澜汐开发工作台'
    $entry.Save()
    if ([IO.File]::Exists($path)) {
        [IO.File]::Replace($temporary, $path, $backup)
    } else {
        [IO.File]::Move($temporary, $path)
    }
} finally {
    if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) }
}
"""
    env = {**os.environ, "LANXI_SHORTCUT_TARGET": str(executable),
           "LANXI_SHORTCUT_VERSION": version[1], "LANXI_SHORTCUT_PROGRAMS": str(programs or "")}
    subprocess.run(powershell(script), env=env, capture_output=True, check=True, timeout=12,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def restart_after_exit(executable):
    literal = "'" + str(executable).replace("'", "''") + "'"
    directory = "'" + str(executable.parent).replace("'", "''") + "'"
    script = (f"$ErrorActionPreference='Stop'; Wait-Process -Id {os.getpid()}; "
              f"Start-Process -FilePath {literal} -WorkingDirectory {directory}")
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen(powershell(script),
                     env=env, creationflags=subprocess.CREATE_NO_WINDOW)
