from scripts import hik_sdk_worker


def test_maps_logical_nvr_channel_to_hcnetsdk_digital_channel():
    device_info = bytearray(512)
    device_info[55] = 16
    device_info[66] = 33

    metadata = hik_sdk_worker._channel_metadata(bytes(device_info), requested_channel=1)

    assert metadata["digital_channel_start"] == 33
    assert metadata["sdk_channel"] == 33


def test_keeps_explicit_hcnetsdk_channel_number():
    device_info = bytearray(512)
    device_info[55] = 16
    device_info[66] = 33

    metadata = hik_sdk_worker._channel_metadata(bytes(device_info), requested_channel=35)

    assert metadata["sdk_channel"] == 35


def test_keeps_direct_camera_channel_number():
    device_info = bytearray(512)
    device_info[52] = 1
    device_info[53] = 1

    metadata = hik_sdk_worker._channel_metadata(bytes(device_info), requested_channel=1)

    assert metadata["sdk_channel"] == 1
