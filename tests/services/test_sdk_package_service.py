import io
import tarfile
import zipfile

import pytest

from app.services import sdk_package_service as service


@pytest.fixture
def sdk_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SDK_INSTALL_ROOT", str(tmp_path / "sdk-packages"))
    return tmp_path / "sdk-packages"


def _zip(entries: dict[str, bytes]) -> io.BytesIO:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    payload.seek(0)
    return payload


def test_installs_hikvision_library_atomically(sdk_root):
    result = service.install_archive(
        "hikvision",
        "official.zip",
        _zip({
            "HCNetSDK/lib/libhcnetsdk.so": b"native-library",
            "HCNetSDK/lib/HCNetSDKCom/libcrypto.so": b"dependency",
            "HCNetSDK/examples/demo.py": b"raise SystemExit",
        }),
    )

    assert result["installed"] is True
    assert service.installed_library_path("hikvision").read_bytes() == b"native-library"
    assert (service.installed_lib_dir("hikvision") / "HCNetSDKCom" / "libcrypto.so").is_file()
    assert not (sdk_root / "hikvision" / "current" / "examples").exists()


def test_rejects_zip_path_traversal(sdk_root):
    with pytest.raises(service.SdkPackageError, match="caminho inseguro"):
        service.install_archive(
            "dahua",
            "unsafe.zip",
            _zip({"../libdhnetsdk.so": b"bad"}),
        )


def test_rejects_tar_symbolic_link(sdk_root):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        link = tarfile.TarInfo("Bin/libdhnetsdk.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    payload.seek(0)

    with pytest.raises(service.SdkPackageError, match="Links"):
        service.install_archive("dahua", "unsafe.tar.gz", payload)


def test_replaces_current_and_keeps_previous(sdk_root):
    service.install_archive("dahua", "v1.zip", _zip({"Bin/libdhnetsdk.so": b"v1"}))
    service.install_archive("dahua", "v2.zip", _zip({"Bin/libdhnetsdk.so": b"v2"}))

    assert service.installed_library_path("dahua").read_bytes() == b"v2"
    assert (sdk_root / "dahua" / "previous" / "lib" / "libdhnetsdk.so").read_bytes() == b"v1"
