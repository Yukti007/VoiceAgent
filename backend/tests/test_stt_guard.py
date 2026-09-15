from app.agent.stt_guard import detect_repetition


def test_normal_sentence_is_not_flagged():
    assert detect_repetition("Hi, I need a dental appointment for tomorrow") is None


def test_short_natural_repetition_is_not_flagged():
    # "haan haan" (yes yes) is normal Hindi speech, not a hallucination.
    assert detect_repetition("haan haan theek hai") is None


def test_empty_transcript_is_not_flagged():
    assert detect_repetition("") is None
    assert detect_repetition("   ") is None


def test_degenerate_repetition_is_flagged():
    signal = detect_repetition("हाँ " * 100)
    assert signal is not None
    assert signal.token == "हाँ"
    assert signal.count == 100
    assert signal.total_tokens == 100
    assert signal.ratio == 1.0


def test_mixed_repetition_below_ratio_threshold_is_not_flagged():
    # Ten repeats of "ok" meets min_repeats but is diluted by other words,
    # so it should not be flagged as degenerate.
    text = "ok " * 10 + "so let's book the appointment for tomorrow afternoon instead please"
    assert detect_repetition(text) is None


def test_thresholds_are_configurable():
    text = "no " * 5
    assert detect_repetition(text) is None
    assert detect_repetition(text, min_repeats=5) is not None
