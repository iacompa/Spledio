"""Session-level tests: history, layers, preview/export consistency, project
save/reopen, relink, view independence and the end-to-end workflow.

Audio files are synthetic TEST FIXTURES written into pytest's tmp_path.
"""

import math
import os
import shutil
import zipfile

import numpy as np
import pytest
import soundfile as sf

from spledio.audio.export import ExportSettings
from spledio.dsp.spectral import Cancelled
from spledio.engine.session import EditError, EditorSession
from spledio.persistence.project_file import AssetMismatchError, MissingMediaError, ProjectFormatError, read_manifest
from spledio.selections.geometry import BrushShape, RectShape, time_range
from spledio.selections.selection import Selection

SR = 48000


def make_wav(path, channels=2, dur=3.0, subtype="FLOAT"):
    t = np.arange(int(dur * SR)) / SR
    a = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.25 * np.sin(2 * np.pi * 2500 * t)
    data = np.stack([a, 0.9 * a][:channels], axis=1) if channels > 1 else a[:, None]
    sf.write(path, data, SR, subtype=subtype)
    return path


def level(sig, f, a, b):
    tt = np.arange(a, b) / SR
    return 2 * abs(np.mean(sig[a:b] * np.exp(-2j * np.pi * f * tt)))


@pytest.fixture
def session(tmp_path):
    p = make_wav(str(tmp_path / "fixture.wav"))
    s = EditorSession.from_audio_file(p)
    yield s
    s.close()


# ---------------------------------------------------------------- 10. history
def test_undo_redo_restores_exact_audio_and_structure(session):
    s = session
    orig = s.active_layer.audio.copy()
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    s.apply_gain(-6)
    after_gain = s.active_layer.audio.copy()
    s.set_selection(Selection.of(RectShape(1.0, 2.0, 300, 600)))
    s.extract_to_new_layer()
    assert len(s.project.layers) == 2
    s.set_layer_props(s.project.layers[1].id, mute=True, gain_db=-3.0)
    s.undo()
    s.undo()
    assert len(s.project.layers) == 1
    assert np.array_equal(s.active_layer.audio, after_gain)
    s.undo()
    assert np.array_equal(s.active_layer.audio, orig)
    assert s.undo() is None
    s.redo()
    s.redo()
    assert len(s.project.layers) == 2
    s.redo()
    assert s.project.layers[1].mute and s.project.layers[1].gain_db == -3.0


def test_one_brush_stroke_is_one_undo_event(session):
    s = session
    n0 = len(s.history.undo_stack)
    path = tuple((0.5 + 0.001 * i, 2500.0 + i) for i in range(300))  # many pointer events
    s.apply_brush(BrushShape(path, rt=0.03, rf=80, hardness=0.5), -12)
    assert len(s.history.undo_stack) == n0 + 1


def test_failed_or_cancelled_processing_adds_no_operation(session):
    s = session
    before = s.active_layer.audio.copy()
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    with pytest.raises(Cancelled):
        s.component(cancel=lambda: True)
    assert len(s.history.undo_stack) == 0
    s.set_layer_props(s.active_layer_id, locked=True)
    with pytest.raises(EditError):
        s.apply_gain(-6)
    assert len(s.history.undo_stack) == 1  # only the lock change
    assert np.array_equal(s.active_layer.audio, before)
    with pytest.raises(EditError):
        s.set_selection(None) or s.apply_gain(-6)


def test_tiny_selection_reports_resolution_instead_of_silently_doing_nothing(session):
    s = session
    s.set_selection(Selection.of(RectShape(1.0, 1.001, 1000.0, 1001.0)))
    with pytest.raises(EditError, match="processing resolution"):
        s.apply_gain(-6)


# ---------------------------------------------------------------- 8. transfer conservation
def test_transfer_brush_preserves_sum(session):
    s = session
    s.add_empty_layer("target")
    target = s.active_layer_id
    s.set_active_layer(s.project.layers[0].id)
    mix_before = s.project.render_mix()
    brush = BrushShape(((0.6, 2500.0), (2.0, 2500.0)), rt=0.05, rf=200, hardness=0.4)
    s.transfer_brush(brush, target, amount=0.6)
    mix_after = s.project.render_mix()
    assert np.max(np.abs(mix_after - mix_before)) < 1e-6
    assert np.max(np.abs(s.project.layer(target).audio)) > 0.05


# ---------------------------------------------------------------- 7. extraction via session
def test_extract_and_copy_layer_semantics(session):
    s = session
    mix0 = s.project.render_mix()
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700), feather_t=0.03, feather_f=50))
    s.extract_to_new_layer()
    assert np.max(np.abs(s.project.render_mix() - mix0)) < 1e-6  # float32 storage tolerance
    s.undo()
    r = s.extract_to_new_layer(copy=True)
    assert any("louder" in w for w in r.warnings)
    a, b = int(1.0 * SR), int(2.0 * SR)
    mix = s.project.render_mix()
    assert level(mix[0], 2500, a, b) == pytest.approx(2 * level(mix0[0], 2500, a, b), rel=1e-3)



@pytest.mark.parametrize("props", [{"gain_db": -12.0}, {"polarity_inverted": True},
                                   {"gain_db": 4.0, "polarity_inverted": True}, {"mute": True}],
                         ids=["gain", "polarity", "gain+polarity", "muted"])
def test_extract_keeps_the_mix_when_the_source_layer_has_gain_or_polarity(session, props):
    """Extract moves content into a new layer; the new layer must play at the source layer's
    gain and polarity (and mute), otherwise the moved content changes level in the mix. It used
    to be created at 0 dB / normal polarity: from a -12 dB layer the extract came out 12 dB louder."""
    s = session
    s.set_layer_props(s.active_layer_id, **props)
    mix0 = s.project.render_mix()
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    s.extract_to_new_layer()
    assert np.max(np.abs(s.project.render_mix() - mix0)) < 1e-6
    src, new = s.project.layers
    assert {k: getattr(new, k) for k in props} == props
    s.undo()
    assert len(s.project.layers) == 1 and np.max(np.abs(s.project.render_mix() - mix0)) == 0.0


def test_solo_mute_rules(session):
    s = session
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    s.extract_to_new_layer()
    base, ext = s.project.layers
    s.set_layer_props(ext.id, solo=True)
    assert [l.id for l in s.project.audible_layers()] == [ext.id]
    s.set_layer_props(ext.id, mute=True)
    assert s.project.audible_layers() == []  # solo does not override mute
    s.set_layer_props(ext.id, mute=False, solo=False, polarity_inverted=True)
    mix = s.project.render_mix()
    np.testing.assert_allclose(mix, base.audio.astype(float) - ext.audio.astype(float), atol=1e-7)


# ---------------------------------------------------------------- 14. preview / export consistency
def test_preview_equals_commit_equals_export(session, tmp_path):
    s = session
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    pv = s.preview_gain(-9)
    processed = pv.processed.astype(np.float32)
    s.commit_preview()
    np.testing.assert_array_equal(s.active_layer.audio[:, pv.start : pv.end], processed)
    out = str(tmp_path / "out.wav")
    s.export(out, ExportSettings(format="WAV", subtype="FLOAT"))
    data, sr = sf.read(out, dtype="float32", always_2d=True)
    assert sr == SR
    np.testing.assert_array_equal(data.T, s.render_full_mix())


def test_cancel_preview_leaves_audio_unchanged(session):
    s = session
    before = s.active_layer.audio.copy()
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    s.preview_gain(-20)
    s.cancel_preview()
    assert s.preview is None
    assert np.array_equal(s.active_layer.audio, before)
    assert len(s.history.undo_stack) == 0


def test_difference_signal_is_original_minus_processed(session):
    s = session
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    pv = s.preview_gain(-6)
    d = s.difference_buffer()
    np.testing.assert_allclose(d.data, (pv.original - pv.processed).astype(np.float32), atol=1e-7)
    s.commit_preview()
    d2 = s.difference_buffer()
    assert d2.start == pv.start
    np.testing.assert_allclose(d2.data, d.data, atol=1e-6)


# ---------------------------------------------------------------- 18. clipping
def test_boost_triggers_clipping_warning_and_float_export_not_clipped(session, tmp_path):
    from spledio.audio.export import ClippingError

    s = session
    s.set_selection(Selection.of(time_range(1.0, 2.0)))
    r = s.apply_gain(+12)
    assert r.warnings and "peaks" in r.warnings[0]
    assert r.peak_after > 1.0
    out = str(tmp_path / "f.wav")
    s.export(out, ExportSettings(subtype="FLOAT"))
    data, _ = sf.read(out, dtype="float32")
    assert np.max(np.abs(data)) > 1.0
    with pytest.raises(ClippingError):
        s.export(str(tmp_path / "i.wav"), ExportSettings(subtype="PCM_16", clip_policy="error"))
    assert not os.path.exists(tmp_path / "i.wav")


# ---------------------------------------------------------------- 2. no-edit export
def test_no_edit_float_export_is_bit_exact(tmp_path):
    p = make_wav(str(tmp_path / "src.wav"), subtype="PCM_24")
    s = EditorSession.from_audio_file(p)
    out = str(tmp_path / "copy.wav")
    s.export(out, ExportSettings(subtype="FLOAT"))
    a, _ = sf.read(p, dtype="float32")
    b, _ = sf.read(out, dtype="float32")
    assert np.array_equal(a, b)
    s.close()


def test_export_refuses_to_overwrite_source(session):
    src = list(session.project.assets.values())[0].path
    with pytest.raises(Exception, match="(?i)source|overwrite|exists"):
        session.export(src, ExportSettings(subtype="FLOAT", overwrite=True))


# ---------------------------------------------------------------- 11. view independence
def test_edit_config_is_stored_with_operation_and_not_reinterpreted(session):
    s = session
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700)))
    s.apply_gain(-6)
    audio = s.active_layer.audio.copy()
    cmd = s.history.undo_stack[-1]
    assert cmd.params["config"] == {"n_fft": 4096, "hop": 1024, "window": "hann"}
    from spledio.dsp.stft import STFTConfig

    s.set_edit_config(STFTConfig(1024, 256, "hann"))  # affects future edits only
    assert np.array_equal(s.active_layer.audio, audio)
    s.undo()
    s.redo()
    assert np.array_equal(s.active_layer.audio, audio)


# ---------------------------------------------------------------- 15. project reopen
def _edited(s):
    s.set_selection(Selection.of(RectShape(0.5, 2.5, 2300, 2700), feather_f=40))
    s.apply_gain(-6)
    s.set_selection(Selection.of(RectShape(1.0, 2.0, 300, 600)))
    s.extract_to_new_layer()
    s.set_layer_props(s.project.layers[1].id, mute=True, gain_db=-4.5, polarity_inverted=True, name="Low part")
    s.named_selections["low"] = s.selection


def test_save_close_reopen_restores_audio_and_layers(session, tmp_path):
    s = session
    _edited(s)
    proj = str(tmp_path / "p.spledio")
    s.save(proj)
    assert not s.dirty
    layers = [(l.id, l.name, l.gain_db, l.mute, l.polarity_inverted, l.audio.copy()) for l in s.project.layers]
    mix = s.render_full_mix()
    s.close()
    r = EditorSession.open_project(proj)
    assert [(l.id, l.name, l.gain_db, l.mute, l.polarity_inverted) for l in r.project.layers] == [x[:5] for x in layers]
    for l, x in zip(r.project.layers, layers):
        assert np.array_equal(l.audio, x[5])
    assert np.array_equal(r.render_full_mix(), mix)
    assert r.selection is not None and "low" in r.named_selections
    assert not r.dirty
    r.close()


def test_portable_project_survives_missing_source(tmp_path):
    src = make_wav(str(tmp_path / "orig.wav"))
    s = EditorSession.from_audio_file(src)
    _edited(s)
    proj = str(tmp_path / "portable.spledio")
    s.save(proj, portable=True)
    mix = s.render_full_mix()
    s.close()
    os.remove(src)
    r = EditorSession.open_project(proj)
    assert np.array_equal(r.render_full_mix(), mix)
    # re-saving a reopened portable project keeps working without the original
    r.save(str(tmp_path / "again.spledio"), portable=True)
    r.close()
    r2 = EditorSession.open_project(str(tmp_path / "again.spledio"))
    assert np.array_equal(r2.render_full_mix(), mix)
    r2.close()


def test_missing_media_relink_and_checksum(tmp_path):
    src = make_wav(str(tmp_path / "orig.wav"))
    s = EditorSession.from_audio_file(src)
    _edited(s)
    proj = str(tmp_path / "linked.spledio")
    s.save(proj)
    mix = s.render_full_mix()
    aid = list(s.project.assets)[0]
    s.close()
    moved = str(tmp_path / "moved" / "renamed.wav")
    os.makedirs(os.path.dirname(moved))
    shutil.move(src, moved)
    with pytest.raises(MissingMediaError) as ei:
        EditorSession.open_project(proj)
    assert ei.value.missing[0]["id"] == aid
    r = EditorSession.open_project(proj, relink={aid: moved})
    assert np.array_equal(r.render_full_mix(), mix)
    r.close()
    wrong = make_wav(str(tmp_path / "wrong.wav"), dur=3.0, subtype="PCM_16")
    with pytest.raises(AssetMismatchError):
        EditorSession.open_project(proj, relink={aid: wrong})


def test_unedited_project_stores_no_audio_blocks(session, tmp_path):
    proj = str(tmp_path / "plain.spledio")
    session.save(proj)
    with zipfile.ZipFile(proj) as z:
        assert z.namelist() == ["manifest.json"]
    m = read_manifest(proj)
    assert m["schema_version"] == 1 and m["layers"][0]["data"] is None


def test_malicious_archives_rejected(tmp_path, session):
    proj = str(tmp_path / "ok.spledio")
    _edited(session)
    session.save(proj)
    bad = str(tmp_path / "bad.spledio")
    with zipfile.ZipFile(proj) as zin, zipfile.ZipFile(bad, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("../../evil.txt", b"x")
    with pytest.raises(ProjectFormatError):
        EditorSession.open_project(bad)
    junk = str(tmp_path / "junk.spledio")
    with open(junk, "wb") as f:
        f.write(os.urandom(2000))
    with pytest.raises(ProjectFormatError):
        EditorSession.open_project(junk)
    # newer schema is refused clearly
    newer = str(tmp_path / "newer.spledio")
    import json

    with zipfile.ZipFile(proj) as zin, zipfile.ZipFile(newer, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "manifest.json":
                m = json.loads(data)
                m["schema_version"] = 99
                data = json.dumps(m).encode()
            zout.writestr(item, data)
    with pytest.raises(ProjectFormatError, match="newer"):
        EditorSession.open_project(newer)


def test_save_is_atomic_on_failure(session, tmp_path, monkeypatch):
    proj = str(tmp_path / "p.spledio")
    session.save(proj)
    before = open(proj, "rb").read()
    import spledio.persistence.project_file as pf

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pf, "_npy_bytes", boom)
    _edited(session)
    with pytest.raises(RuntimeError):
        session.save(proj)
    assert open(proj, "rb").read() == before
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".partial")]


# ---------------------------------------------------------------- 20. end-to-end (engine level)
def test_end_to_end_workflow(tmp_path):
    src = make_wav(str(tmp_path / "e2e.wav"))
    src_bytes = open(src, "rb").read()
    s = EditorSession.from_audio_file(src)
    # narrow selection on the 2500 Hz component
    s.set_selection(Selection.of(RectShape(0.8, 2.2, 2400, 2600)))
    heard = s.audition_selected()
    assert heard.data.shape[0] == 2 and np.max(np.abs(heard.data)) > 0.1
    s.apply_gain(-6)
    a0, A, B, label = s.ab_regions()
    assert not np.array_equal(A, B)
    s.set_selection(Selection.of(RectShape(0.5, 1.0, 300, 600)))
    s.extract_to_new_layer()
    s.set_layer_props(s.project.layers[1].id, mute=True)
    s.undo()  # un-mute
    assert not s.project.layers[1].mute
    proj = str(tmp_path / "e2e.spledio")
    s.save(proj)
    s.close()
    r = EditorSession.open_project(proj)
    out = str(tmp_path / "e2e-export.wav")
    r.export(out, ExportSettings(subtype="FLOAT"))
    data, sr = sf.read(out, dtype="float64", always_2d=True)
    x, _ = sf.read(src, dtype="float64", always_2d=True)
    a, b = int(1.1 * SR), int(1.9 * SR)
    ratio = level(data[:, 0], 2500, a, b) / level(x[:, 0], 2500, a, b)
    assert abs(20 * math.log10(ratio) + 6) < 0.1  # the intended -6 dB change is in the file
    assert abs(level(data[:, 0], 440, a, b) / level(x[:, 0], 440, a, b) - 1) < 1e-3
    assert data.shape == x.shape  # duration and channels preserved
    assert open(src, "rb").read() == src_bytes  # source never modified
    r.close()


def test_recovered_session_points_at_original_and_stays_dirty(session, tmp_path):
    s = session
    _edited(s)
    original = str(tmp_path / "orig.spledio")
    s.save(original)
    recovery_copy = str(tmp_path / "recovery.spledio")
    s.save(recovery_copy)
    s.close()
    r = EditorSession.open_project(recovery_copy)
    r.mark_recovered(original)
    assert r.path == original and r.dirty
    r.undo()
    r.redo()
    assert r.dirty  # no history state counts as "saved" until the user saves
    r.mark_recovered(str(tmp_path / "gone.spledio"))
    assert r.path is None
    r.save(original)
    assert not r.dirty
    r.close()
