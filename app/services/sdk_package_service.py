"""Instala SDKs proprietarios enviados por administradores, sem executar o arquivo recebido."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


class SdkPackageError(RuntimeError):
    """Erro seguro para exibicao na interface administrativa."""


MANUFACTURERS = {
    "hikvision": "libhcnetsdk.so",
    "dahua": "libdhnetsdk.so",
    # Intelbras eh OEM Dahua: mesmo protocolo NetSDK, biblioteca de mesmo nome.
    "intelbras": "libdhnetsdk.so",
}
MAX_ARCHIVE_BYTES = 750 * 1024 * 1024
MAX_EXPANDED_BYTES = 3 * 1024 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_COMPRESSION_RATIO = 250
COPY_CHUNK_SIZE = 1024 * 1024


def install_root() -> Path:
    return Path(os.getenv("SDK_INSTALL_ROOT", "/data/sdk-packages")).resolve()


def installed_lib_dir(manufacturer: str) -> Path:
    _library_name(manufacturer)
    return install_root() / manufacturer / "current" / "lib"


def installed_library_path(manufacturer: str) -> Path:
    return installed_lib_dir(manufacturer) / _library_name(manufacturer)


def package_status() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for manufacturer, library in MANUFACTURERS.items():
        lib_path = installed_library_path(manufacturer)
        manifest_path = install_root() / manufacturer / "current" / "manifest.json"
        manifest: dict[str, object] = {}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            pass
        result[manufacturer] = {
            "installed": lib_path.is_file(),
            "library": library,
            "filename": manifest.get("filename"),
            "sha256": manifest.get("sha256"),
            "size": manifest.get("size"),
        }
    return result


def install_archive(manufacturer: str, filename: str, stream: BinaryIO) -> dict[str, object]:
    library_name = _library_name(manufacturer)
    safe_filename = Path(str(filename or "sdk-package")).name[:180]
    root = install_root()
    staging_root = root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{manufacturer}-", dir=staging_root) as temp_name:
        temp_dir = Path(temp_name)
        archive_path = temp_dir / "upload.bin"
        sha256, archive_size = _copy_limited(stream, archive_path)
        extracted_dir = temp_dir / "extracted"
        extracted_dir.mkdir()
        _extract_archive(archive_path, extracted_dir)

        candidates = sorted(
            path for path in extracted_dir.rglob(library_name)
            if path.is_file() and not path.is_symlink()
        )
        if not candidates:
            raise SdkPackageError(f"O pacote nao contem {library_name}.")
        if len(candidates) > 8:
            raise SdkPackageError(f"O pacote contem copias demais de {library_name}.")

        selected = _select_library(candidates)
        source_lib_dir = selected.parent
        prepared = temp_dir / "prepared"
        prepared_lib = prepared / "lib"
        shutil.copytree(source_lib_dir, prepared_lib, symlinks=False)
        _remove_unsafe_entries(prepared_lib)
        if not (prepared_lib / library_name).is_file():
            raise SdkPackageError("A biblioteca principal nao permaneceu valida apos a verificacao.")

        manifest = {
            "manufacturer": manufacturer,
            "filename": safe_filename,
            "sha256": sha256,
            "size": archive_size,
            "library": library_name,
        }
        (prepared / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        _activate(manufacturer, prepared)
        return {**manifest, "installed": True}


def _library_name(manufacturer: str) -> str:
    try:
        return MANUFACTURERS[manufacturer]
    except KeyError as exc:
        raise SdkPackageError("Fabricante de SDK invalido.") from exc


def _copy_limited(stream: BinaryIO, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with destination.open("wb") as handle:
        while True:
            chunk = stream.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise SdkPackageError("O arquivo excede o limite de 750 MB.")
            digest.update(chunk)
            handle.write(chunk)
    if not total:
        raise SdkPackageError("O arquivo enviado esta vazio.")
    return digest.hexdigest(), total


def _safe_relative_path(raw_name: str) -> Path:
    normalized = str(raw_name).replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise SdkPackageError("O pacote contem um caminho inseguro.")
    if ":" in pure.parts[0]:
        raise SdkPackageError("O pacote contem um caminho absoluto do Windows.")
    return Path(*pure.parts)


def _validate_totals(entries: Iterable[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    members = list(entries)
    if len(members) > MAX_MEMBERS:
        raise SdkPackageError("O pacote contem arquivos demais.")
    expanded = sum(max(0, size) for _, size, _ in members)
    compressed = sum(max(0, packed) for _, _, packed in members)
    if expanded > MAX_EXPANDED_BYTES:
        raise SdkPackageError("O conteudo descompactado excede 3 GB.")
    if compressed and expanded > compressed * MAX_COMPRESSION_RATIO:
        raise SdkPackageError("O pacote possui taxa de compressao insegura.")
    return members


def _extract_archive(archive_path: Path, destination: Path) -> None:
    if zipfile.is_zipfile(archive_path):
        _extract_zip(archive_path, destination)
        return
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            _extract_tar(archive, destination)
        return
    except (tarfile.TarError, OSError) as exc:
        raise SdkPackageError("Envie um arquivo ZIP, TAR, TAR.GZ ou TGZ valido.") from exc


def _extract_zip(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        _validate_totals((item.filename, item.file_size, item.compress_size) for item in infos)
        for info in infos:
            relative = _safe_relative_path(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise SdkPackageError("Links simbolicos nao sao aceitos no pacote.")
            target = destination / relative
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, COPY_CHUNK_SIZE)


def _extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    members = archive.getmembers()
    _validate_totals((item.name, item.size, item.size) for item in members)
    for member in members:
        relative = _safe_relative_path(member.name)
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SdkPackageError("Links e arquivos especiais nao sao aceitos no pacote.")
        target = destination / relative
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            continue
        source = archive.extractfile(member)
        if source is None:
            raise SdkPackageError("Nao foi possivel ler um arquivo do pacote.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output, COPY_CHUNK_SIZE)


def _select_library(candidates: list[Path]) -> Path:
    preferred = [path for path in candidates if path.parent.name.lower() in {"lib", "bin"}]
    pool = preferred or candidates
    return min(pool, key=lambda path: (len(path.parts), len(str(path))))


def _remove_unsafe_entries(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SdkPackageError("Links simbolicos nao sao permitidos na instalacao.")
        if path.is_file():
            path.chmod(0o644)
        elif path.is_dir():
            path.chmod(0o755)


def _activate(manufacturer: str, prepared: Path) -> None:
    manufacturer_root = install_root() / manufacturer
    manufacturer_root.mkdir(parents=True, exist_ok=True)
    current = manufacturer_root / "current"
    previous = manufacturer_root / "previous"
    if previous.exists():
        shutil.rmtree(previous)
    if current.exists():
        current.rename(previous)
    try:
        shutil.move(str(prepared), str(current))
    except Exception:
        if previous.exists() and not current.exists():
            previous.rename(current)
        raise
