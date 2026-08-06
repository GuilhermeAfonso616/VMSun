from pathlib import Path


def test_ptz_ui_serializes_move_and_stop_and_stops_when_window_loses_focus():
    source = Path("templates/sdk_test.html").read_text(encoding="utf-8")

    assert "stopPtzHold" in source
    assert "startPtzHold" in source
    assert "window.addEventListener('blur',stopPtzHold)" in source


def test_3d_overlay_shares_the_same_box_as_the_video_frame():
    source = Path("templates/sdk_test.html").read_text(encoding="utf-8")

    # O iframe do video e o overlay 3D vivem no mesmo palco, que recebe a
    # proporcao real do fluxo: sem isso o overlay sobrava sobre as barras pretas
    # e o clique/arrasto mapeava coordenadas fora da imagem exibida.
    assert 'id="video-stage"' in source
    stage = source.split('id="video-stage"', 1)[1].split("</div></div>", 1)[0]
    assert 'id="player"' in stage
    assert 'id="ptz-3d-overlay"' in stage
    assert "syncStageBounds" in source


def test_ptz_ui_exposes_goto_preset_command():
    source = Path("templates/sdk_test.html").read_text(encoding="utf-8")

    assert "presets/goto" in source
    assert "activatePreset(preset.token,preset.name)" in source
