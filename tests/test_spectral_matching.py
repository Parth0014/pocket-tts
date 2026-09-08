import numpy as np
import pytest
import soundfile as sf

from speaker_similarity import regression_gate
from spectral_matching import _deess, apply_curve, match_roles, spectral_match


@pytest.fixture
def tonal_pair(tmp_path):
    rate = 24000
    rng = np.random.default_rng(41)
    noise = rng.normal(size=rate * 4)
    freqs = np.fft.rfftfreq(len(noise), 1 / rate)
    # Broadband speech-like rolloff, then a broad treble loss to be corrected.
    voice = np.fft.irfft(np.fft.rfft(noise) / np.maximum(freqs / 200, 1))
    dull = np.fft.irfft(np.fft.rfft(voice) / np.sqrt(1 + (freqs / 1200) ** 2))
    anchor = tmp_path / "anchor.wav"
    sf.write(anchor, voice * 0.2, rate, subtype="FLOAT")
    return anchor, (dull * 0.2).astype(np.float32), rate


def test_match_moves_toward_reference_and_bounds_gain(tonal_pair):
    anchor, dull, rate = tonal_pair
    matched, report = spectral_match(anchor, dull, rate)
    assert report["enabled"]
    assert max(abs(value) for value in report["gains_db"]) <= 3
    assert report["before_centroid_hz"] < report["after_centroid_hz"] <= report["reference_centroid_hz"] + 1
    assert report["after_distance_db"] < report["before_distance_db"]
    assert matched.shape == dull.shape and np.isfinite(matched).all()


def test_gain_difference_alone_does_not_trigger_eq(tonal_pair):
    anchor, _, rate = tonal_pair
    reference, _ = sf.read(anchor, dtype="float32")
    output, report = spectral_match(anchor, reference * 0.25, rate)
    assert not report["enabled"]
    np.testing.assert_array_equal(output, reference * 0.25)


def test_wola_identity_and_impulse_alignment():
    source = np.zeros((24000, 2), dtype=np.float32)
    source[12000] = [0.5, 0.25]
    result = apply_curve(source, 24000, np.array([125, 1000, 8000]), np.zeros(3))
    np.testing.assert_allclose(result, source, atol=1e-7)
    assert np.argmax(result[:, 0]) == 12000


def test_role_matching_retains_silence_and_unrelated_role(tonal_pair):
    anchor, dull, rate = tonal_pair
    source = np.r_[dull, np.zeros(1000), dull * 0.7].astype(np.float32)
    result, reports = match_roles(source, rate, [(0, len(dull), "narration")], {"narration": anchor})
    assert reports["narration"]["enabled"]
    np.testing.assert_array_equal(result[len(dull):], source[len(dull):])


def test_invalid_controls_fail(tonal_pair):
    anchor, dull, rate = tonal_pair
    with pytest.raises(ValueError):
        spectral_match(anchor, dull, rate, amount=1)


def test_similarity_gate_rejects_regression():
    assert regression_gate([0.8, 0.7], [0.81, 0.71])["passed"]
    assert not regression_gate([0.8, 0.7], [0.79, 0.69])["passed"]
    with pytest.raises(ValueError):
        regression_gate([0.8], [float("nan")])


def test_deesser_preserves_samples_and_channels(tonal_pair):
    _, samples, rate = tonal_pair
    result, settings = _deess(samples, rate)
    assert result.shape == samples.shape and np.isfinite(result).all()
    assert settings == "deesser=i=0.1:m=0.2:f=0.5"
